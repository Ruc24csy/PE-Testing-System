"""体育成绩录入平台 Web 入口。"""
from __future__ import annotations

import io
import re
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import openpyxl
from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import auth, config, exporter, grading, importer, score_import, scoring, services
from .auth import admin_user, client_ip, current_user, get_db, is_admin
from .models import (AuditLog, Enrollment, GradeCorrection, ScoreHistory, ScoreRecord, Semester, StandardSnapshot,
                     Student, TeachingClass, User, init_db, now)

STATIC = Path(__file__).parent / "static"

@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield


app = FastAPI(title="体育成绩录入平台", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, session_cookie="pe_session",
                   max_age=8 * 3600, same_site="lax", https_only=config.HTTPS_ONLY)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    # 生产环境的完整安全头（CSP 等）在 deploy/nginx.conf 里加，Nginx 会去掉下面这几个重复的
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if config.HTTPS_ONLY:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if request.url.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index(request: Request, code: str = "", state: str = ""):
    # 应用中心登记的回调地址可以是站点根路径：学校把 ?code=..&state=.. 接在后面
    if config.AUTH_MODE == "sso" and code and state:
        db = next(get_db())
        try:
            return sso_callback(request, code, state, db)
        finally:
            db.close()
    return FileResponse(STATIC / "index.html")


# ================================================================= 登录

@app.get("/api/auth/mode")
def auth_mode():
    return {"mode": config.AUTH_MODE}


@app.get("/auth/login")
def login_redirect(request: Request):
    if config.AUTH_MODE != "sso":
        return RedirectResponse("/#/login")
    import secrets
    state = secrets.token_urlsafe(24)
    # state 存在签名会话里：只有发起登录的这个浏览器能完成回调（防登录 CSRF），一次一用
    request.session["sso_state"] = state
    return RedirectResponse(auth.sso_login_url(state))


@app.get("/auth/sso/callback")
def sso_callback(request: Request, code: str = "", state: str = "", db: Session = Depends(get_db)):
    if config.AUTH_MODE != "sso":
        raise HTTPException(404)
    ip = client_ip(request)
    expected = request.session.pop("sso_state", None)
    if not code or not state or not expected or state != expected:
        return RedirectResponse("/#/login?error=expired")
    try:
        ident = auth.sso_exchange(code)
    except auth.SSOError as e:
        services.log(db, "sso", "登录失败", ip=ip, reason=str(e)[:200])
        db.commit()
        return RedirectResponse("/#/login?error=sso")
    except Exception:
        return RedirectResponse("/#/login?error=unavailable")
    u = db.get(User, ident["staff_no"])
    if u and u.is_public:
        # 公共账号的工号由管理员自定，统一认证里同号的人不能借此登录
        auth.record_attempt(db, ident["staff_no"], ip, False)
        return RedirectResponse("/#/login?error=category")
    if not (u and is_admin(u)) and ident["staff_no"] not in config.ADMIN_STAFF_NOS \
            and ident["category"] not in config.SSO_ALLOWED_CATEGORIES:
        auth.record_attempt(db, ident["staff_no"], ip, False)
        return RedirectResponse("/#/login?error=category")
    u = auth.ensure_user(db, ident["staff_no"], ident["name"])
    auth.record_attempt(db, u.staff_no, ip, True)
    request.session.clear()
    request.session["staff_no"] = u.staff_no
    request.session["login_method"] = "sso"
    services.log(db, u.staff_no, "登录", ip=ip, method="sso")
    db.commit()
    return RedirectResponse("/")


@app.post("/api/auth/dev-login")
def dev_login(request: Request, staff_no: str = Body(..., embed=True), db: Session = Depends(get_db)):
    if config.AUTH_MODE != "dev":
        raise HTTPException(404)
    staff_no = staff_no.strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", staff_no):
        raise HTTPException(400, "请输入工号")
    u = auth.ensure_user(db, staff_no)
    request.session.clear()
    request.session["staff_no"] = u.staff_no
    return {"ok": True}


@app.post("/api/auth/local-login")
def local_login(request: Request, staff_no: str = Body(...), password: str = Body(...), db: Session = Depends(get_db)):
    """本地口令登录：应急管理员（统一认证不可用时）和体育部公共账号。连续失败5次锁定15分钟。"""
    ip = client_ip(request)
    if auth.locked(db, staff_no):
        raise HTTPException(429, f"连续登录失败次数过多，账号已锁定{config.LOGIN_LOCK_MINUTES}分钟")
    u = db.get(User, staff_no)
    if u and not (is_admin(u) or u.is_public):
        u = None  # 本地口令只留给应急管理员和公共账号，教师一律走统一认证
    if not u or not auth.verify_password(password, u.password_hash):
        auth.record_attempt(db, staff_no, ip, False)
        raise HTTPException(401, "工号或口令错误")
    auth.record_attempt(db, staff_no, ip, True)
    request.session.clear()
    request.session["staff_no"] = u.staff_no
    request.session["login_method"] = "local"
    services.log(db, u.staff_no, "登录", ip=ip, method="local")
    db.commit()
    return {"ok": True, "must_change_password": u.must_change_password}


@app.post("/auth/logout")
def logout(request: Request, response: Response):
    request.session.clear()
    # 只清浏览器缓存。不清 "storage"：录入页把没保存上的成绩暂存在 localStorage，下次登录自动补存；
    # 不清 "cookies"：它作用于整个注册域（ruc.edu.cn），会把同域其他系统也登出。会话 cookie 已由上一行作废。
    response.headers["Clear-Site-Data"] = '"cache"'
    # 只退出本平台，不跳统一认证的登出页：老师用自己的电脑，不必连带退出数智人大
    return {"ok": True, "redirect": "/"}


@app.get("/api/me")
def me(request: Request, u: User = Depends(current_user)):
    return {"staff_no": u.staff_no, "name": u.name, "is_admin": is_admin(u), "is_public": bool(u.is_public),
            "must_change_password": auth.must_change_password(request, u), "auth_mode": config.AUTH_MODE}


@app.post("/api/me/password")
def change_password(old: str = Body(...), new: str = Body(...), u: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    if not auth.verify_password(old, u.password_hash):
        raise HTTPException(400, "原口令不正确")
    err = auth.password_ok(new)
    if err:
        raise HTTPException(400, err)
    u.password_hash = auth.hash_password(new)
    u.must_change_password = False
    db.merge(u)
    db.commit()
    return {"ok": True}


# ================================================================= 学期

def sem_dict(s: Semester) -> dict:
    return {"id": s.id, "code": s.code, "name": s.name, "season": s.season, "is_current": s.is_current,
            "entry_open": s.entry_open, "config": s.config, "standard_version": s.standard_version,
            "pe_locked": bool(s.pe_locked), "pe_locked_by": s.pe_locked_by,
            "pe_locked_at": s.pe_locked_at.strftime("%Y-%m-%d %H:%M") if s.pe_locked_at else None}


@app.get("/api/semesters")
def list_semesters(u: User = Depends(current_user), db: Session = Depends(get_db)):
    sems = db.scalars(select(Semester).order_by(Semester.code.desc())).all()
    sems.sort(key=lambda s: (not s.is_current, ))
    return [sem_dict(s) for s in sems]


SEMESTER_YEAR_MIN, SEMESTER_YEAR_MAX = 2020, 2100


@app.post("/api/admin/semesters")
def create_semester(body: dict = Body(...), u: User = Depends(admin_user), db: Session = Depends(get_db)):
    """新建学期：前端选学年起始年份和秋季/春季，名称与编码统一由这里生成。"""
    y, season = body.get("start_year"), body.get("season")
    if isinstance(y, str) and y.strip().isdigit():
        y = int(y.strip())
    if not isinstance(y, int) or isinstance(y, bool) or not SEMESTER_YEAR_MIN <= y <= SEMESTER_YEAR_MAX:
        raise HTTPException(400, f"学年起始年份应为 {SEMESTER_YEAR_MIN}–{SEMESTER_YEAR_MAX} 之间的整数")
    if season not in importer.SEASONS:
        raise HTTPException(400, "学期只能选秋季学期或春季学期")
    code, name, y, season = importer.semester_of(y, season)
    if db.scalar(select(Semester).where(Semester.code == code)):
        raise HTTPException(409, f"{name}已存在，不能重复新建")
    first = db.scalar(select(func.count()).select_from(Semester)) == 0
    s = Semester(code=code, name=name, start_year=y, season=season, is_current=first,
                 config=scoring.default_config(season))
    db.add(s)
    db.flush()
    services.log(db, u.staff_no, "新建学期", name, s.id)
    db.commit()
    return sem_dict(s)


@app.patch("/api/admin/semesters/{sid}")
def update_semester(sid: int, body: dict = Body(...), u: User = Depends(admin_user), db: Session = Depends(get_db)):
    s = _sem(db, sid)
    if body.get("is_current"):
        for o in db.scalars(select(Semester)):
            o.is_current = o.id == s.id
    if "entry_open" in body:
        s.entry_open = bool(body["entry_open"])
    if "config" in body:
        s.config = _validate_config(body["config"])
    services.log(db, u.staff_no, "学期设置", s.name, s.id, change={k: body[k] for k in body})
    db.commit()
    return sem_dict(s)


def _validate_config(cfg: dict) -> dict:
    try:
        slots = []
        for slot in cfg["slots"][:2]:
            items = {g: slot["items"][g] for g in ("M", "F")}
            for g, it in items.items():
                if it not in scoring.ITEM_GENDER or scoring.ITEM_GENDER[it] not in (None, g):
                    raise ValueError(f"{it} 不适用于{'男' if g == 'M' else '女'}生")
            w = float(slot["weight"])
            if not 0 <= w <= 1:
                raise ValueError("权重应在0—100%之间")
            slots.append({"key": slot["key"], "label": str(slot.get("label", ""))[:20], "items": items, "weight": w})
        out = {"slots": slots, "skill_weight": float(cfg["skill_weight"]), "regular_weight": float(cfg["regular_weight"])}
        if [s["key"] for s in slots] != ["a", "b"]:
            raise ValueError("项目配置须包含两个体测项目")
        for k in ("skill_weight", "regular_weight"):
            if not 0 <= out[k] <= 1:
                raise ValueError("权重应在0—100%之间")
        return out
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(400, f"项目配置不正确：{e}")


def _sem(db, sid) -> Semester:
    s = db.get(Semester, sid)
    if not s:
        raise HTTPException(404, "学期不存在")
    return s


# ================================================================= 教学班

def _class(db: Session, cid: int, u: User) -> tuple[TeachingClass, Semester]:
    c = db.get(TeachingClass, cid)
    if not c:
        raise HTTPException(404, "教学班不存在")
    if not is_admin(u) and c.staff_no != u.staff_no:
        raise HTTPException(403, "只能查看和编辑本人所授教学班")
    return c, c.semester


def class_brief(c: TeachingClass, counts: dict | None = None) -> dict:
    return {"id": c.id, "class_no": c.class_no, "course_name": c.course_name, "class_name": c.class_name,
            "teacher_name": c.teacher_name, "staff_no": c.staff_no, "teacher_pending": c.teacher_pending,
            "location": c.location, "weekday": c.weekday, "periods": c.periods, "weeks": c.weeks,
            "enrolled_count": c.enrolled_count, "campus": c.campus, "status": c.status,
            "roster": (counts or {}).get("roster"), "entered": (counts or {}).get("entered"),
            "pending": (counts or {}).get("pending"), "excellent": (counts or {}).get("excellent")}


@app.get("/api/semesters/{sid}/classes")
def list_classes(sid: int, course: str = "", campus: str = "", scope: str = "mine",
                 u: User = Depends(current_user), db: Session = Depends(get_db)):
    sem = _sem(db, sid)
    q = select(TeachingClass).where(TeachingClass.semester_id == sid)
    if not (is_admin(u) and scope == "all"):
        q = q.where(TeachingClass.staff_no == u.staff_no)
    if course:
        q = q.where(TeachingClass.course_name == course)
    if campus:
        q = q.where(TeachingClass.campus == campus)
    classes = db.scalars(q).all()
    ids = [c.id for c in classes]
    roster = dict(db.execute(select(Enrollment.class_id, func.count()).where(
        Enrollment.class_id.in_(ids), Enrollment.active.is_(True)).group_by(Enrollment.class_id)).all()) if ids else {}
    entered = dict(db.execute(select(Enrollment.class_id, func.count()).join(
        ScoreRecord, ScoreRecord.enrollment_id == Enrollment.id).where(
        Enrollment.class_id.in_(ids), Enrollment.active.is_(True)).group_by(Enrollment.class_id)).all()) if ids else {}
    pend = dict(db.execute(select(Enrollment.class_id, func.count()).join(
        StandardSnapshot, (StandardSnapshot.student_no == Enrollment.student_no) &
        (StandardSnapshot.semester_id == Enrollment.semester_id)).where(
        Enrollment.class_id.in_(ids), Enrollment.active.is_(True), StandardSnapshot.pending.is_(True))
        .group_by(Enrollment.class_id)).all()) if ids else {}

    excellent = {}
    if ids:
        for cid, comp in db.execute(select(Enrollment.class_id, ScoreRecord.computed).join(
                ScoreRecord, ScoreRecord.enrollment_id == Enrollment.id).where(
                Enrollment.class_id.in_(ids), Enrollment.active.is_(True))):
            if services.is_excellent((comp or {}).get("total")):
                excellent[cid] = excellent.get(cid, 0) + 1

    # 按课程分组、班名自然排序（教师“我的教学班”和管理端全部班列表一致）
    classes.sort(key=services.class_sort_key)
    all_courses = sorted(set(db.scalars(select(TeachingClass.course_name).where(TeachingClass.semester_id == sid))))
    has_any = db.scalar(select(func.count()).select_from(TeachingClass).where(TeachingClass.semester_id == sid)) > 0
    return {"semester": sem_dict(sem), "imported": has_any, "courses": all_courses,
            "classes": [class_brief(c, {"roster": roster.get(c.id, 0), "entered": entered.get(c.id, 0),
                                        "pending": pend.get(c.id, 0), "excellent": excellent.get(c.id, 0)})
                        for c in classes]}


@app.get("/api/classes/{cid}")
def get_class(cid: int, u: User = Depends(current_user), db: Session = Depends(get_db)):
    c, sem = _class(db, cid, u)
    rows = services.load_class_rows(db, c, sem)
    cfg = services.class_config(c, sem)
    editable, why = services.can_edit(c, sem)
    locked = services.pe_locked(c)
    if editable and locked:
        why = (services.SEM_LOCKED_MSG if services.sem_locked(c) else "本班体测成绩已提交并锁定，如需更正可撤回提交") + \
            "；运动技能、平时成绩、备注和课程成绩特殊情况仍可修改"
    return {"semester": sem_dict(sem), "class": class_brief(c), "config": cfg,
            "config_labels": {s["key"]: {g: scoring.item_label(it) for g, it in s["items"].items()} for s in cfg["slots"]},
            "editable": editable, "pe_locked": locked, "sem_locked": services.sem_locked(c),
            "readonly_reason": why, "rows": rows,
            "statuses": scoring.STATUSES, "course_statuses": scoring.COURSE_STATUSES,
            "standard_version": sem.standard_version, "pe_zeros": services.pe_zeros(rows, cfg)}


@app.post("/api/classes/{cid}/cells")
def save_cells(cid: int, request: Request, changes: list[dict] = Body(..., embed=True),
               u: User = Depends(current_user), db: Session = Depends(get_db)):
    c, sem = _class(db, cid, u)
    ok, why = services.can_edit(c, sem)
    if not ok:
        raise HTTPException(403, why)
    if len(changes) > 2000:
        raise HTTPException(400, "一次修改的单元格太多")
    res = services.save_cells(db, c, sem, changes, u.staff_no)
    if res["touched"]:
        services.log(db, u.staff_no, "保存", f"{c.class_no} {c.class_name}", sem.id, ip=client_ip(request),
                     cells=len([r for r in res["results"] if r.get("ok") and not r.get("unchanged")]))
    db.commit()
    rows = {r["enrollment_id"]: r for r in services.load_class_rows(db, c, sem)} if res["touched"] else {}
    zeros = services.pe_zeros(list(rows.values()), services.class_config(c, sem)) if res["touched"] else None
    return {"results": res["results"], "rows": [rows[i] for i in res["touched"] if i in rows],
            "status": c.status, "pe_zeros": zeros}


@app.post("/api/classes/{cid}/preview")
def preview_cells(cid: int, changes: list[dict] = Body(..., embed=True), u: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    """不保存，只按输入即时换算（录入页离开单元格就更新得分与总成绩）。"""
    c, sem = _class(db, cid, u)
    if len(changes) > 2000:
        raise HTTPException(400, "一次换算的单元格太多")
    return services.preview_rows(db, c, sem, changes)


@app.put("/api/classes/{cid}/config")
def update_class_config(cid: int, request: Request, cfg: dict = Body(..., embed=True),
                        u: User = Depends(current_user), db: Session = Depends(get_db)):
    c, sem = _class(db, cid, u)
    ok, why = services.can_edit(c, sem)
    if not ok:
        raise HTTPException(403, why)
    if services.pe_locked(c):
        raise HTTPException(403, services.pe_locked_msg(c))
    new = _validate_config(cfg)
    old = services.class_config(c, sem)
    # 已有原始成绩的项目不允许改成另一个项目（会让原始值失去意义），只能改权重
    has_scores = db.scalar(select(func.count()).select_from(ScoreRecord).join(
        Enrollment, Enrollment.id == ScoreRecord.enrollment_id).where(Enrollment.class_id == c.id,
                                                                      ScoreRecord.a_raw.is_not(None) |
                                                                      ScoreRecord.b_raw.is_not(None)))
    for so, sn in zip(old["slots"], new["slots"]):
        if so["items"] != sn["items"] and has_scores:
            raise HTTPException(400, "本班已录入原始成绩，不能更换体测项目，只能调整权重")
    c.config = new
    services.recompute_class(db, c, sem)
    services.log(db, u.staff_no, "配置", f"{c.class_no} {c.class_name}", sem.id, ip=client_ip(request),
                 old=old, new=new)
    db.commit()
    return {"ok": True, "config": new}


@app.post("/api/classes/{cid}/info-error")
def mark_info_error(cid: int, enrollment_id: int = Body(...), note: str = Body(""),
                    u: User = Depends(current_user), db: Session = Depends(get_db)):
    """教师提交“其他问题”（文字），体育部在待确认清单里处理；清空即撤回。"""
    c, sem = _class(db, cid, u)
    e = db.get(Enrollment, enrollment_id)
    if not e or e.class_id != c.id:
        raise HTTPException(404, "学生不在本班")
    e.info_error = note.strip()[:256] or None
    e.info_error_resolved, e.info_error_resolved_by, e.info_error_resolved_at = False, None, None
    services.log(db, u.staff_no, "提交其他问题", f"{e.student_no} {e.name}", sem.id, note=note)
    db.commit()
    return {"ok": True}


@app.post("/api/classes/{cid}/students/{eid}")
def teacher_edit_student(cid: int, eid: int, request: Request, body: dict = Body(...),
                         u: User = Depends(current_user), db: Session = Depends(get_db)):
    """教师在“更多”里直接改学生姓名、性别、学院和标准档，保存即生效、立即重算。

    学生基础数据跨学期共用：改后标为手动修改，之后导入基础数据表不覆盖。
    标准档写成只对本学期生效的认定记录（第一优先），待确认的学生由教师定档后不再阻断提交。
    """
    c, sem = _class(db, cid, u)
    ok, why = services.can_edit(c, sem)
    if not ok:
        raise HTTPException(403, why)
    e = db.get(Enrollment, eid)
    if not e or e.class_id != c.id or not e.active:
        raise HTTPException(404, "学生不在本班")
    no = e.student_no
    st = db.get(Student, no)
    snap = db.scalar(select(StandardSnapshot).where(StandardSnapshot.semester_id == sem.id,
                                                    StandardSnapshot.student_no == no))
    shown = services.student_display(e, st)
    before = {"name": shown["name"], "gender": st.gender if st else None, "college": shown["college"],
              "band": snap.band if snap else None, "pending": bool(snap and snap.pending)}
    name = str(body.get("name", before["name"]) or "").strip()[:64]
    college = str(body.get("college", before["college"]) or "").strip()[:64]
    gender = body.get("gender", before["gender"])
    gender = {"男": "M", "女": "F", "M": "M", "F": "F"}.get(str(gender or "").strip(), None)
    band = body.get("band", before["band"])
    if not name:
        raise HTTPException(400, "姓名不能为空")
    if "gender" in body and not gender:
        raise HTTPException(400, "请选择性别")
    if band is not None and band not in ("12", "34"):
        raise HTTPException(400, "标准档只能是大一大二或大三大四")
    gender_changed = gender != before["gender"]
    band_needed = band is not None and (band != before["band"] or before["pending"])
    if gender_changed or (band is not None and band != before["band"]):
        # 性别、标准档直接改变体测得分；体育部锁定学期、或该生所在的班体测已提交后不能再动
        if sem.pe_locked:
            raise HTTPException(403, "本学期体测成绩已由体育部锁定，不能再改性别或标准档；如需更正请联系体育部")
        locked = db.scalar(select(TeachingClass).join(Enrollment, Enrollment.class_id == TeachingClass.id).where(
            Enrollment.semester_id == sem.id, Enrollment.student_no == no, Enrollment.active.is_(True),
            TeachingClass.status == "已提交"))
        if locked:
            raise HTTPException(403, f"该生所在的 {locked.class_name} 体测成绩已提交，不能再改性别或标准档；"
                                     "如需更正请先撤回提交")
    changed = {k for k, v in (("name", name), ("college", college)) if v != before[k]}
    if gender_changed:
        changed.add("gender")
    created = st is None and bool(changed or band_needed)
    if created:  # 学生基础数据里没有这个人：按教师填的补一条
        st = Student(student_no=no)
        db.add(st)
    if changed or created:
        st.name, st.college, st.gender, st.manual = name, college, gender, True
        db.flush()
    if changed or band_needed or created:
        grading.rebuild_snapshots(db, sem, u.staff_no, only={no})
        snap = db.scalar(select(StandardSnapshot).where(StandardSnapshot.semester_id == sem.id,
                                                        StandardSnapshot.student_no == no))
        if band is not None and snap and (snap.band != band or snap.pending):
            db.add(GradeCorrection(student_no=no, band=band, change_type="教师认定", effective_semester=sem.code,
                                   this_semester_only=True, operator=u.staff_no,
                                   note=f"任课教师在录入页认定（{c.class_no} {c.class_name}）"))
            db.flush()
            grading.rebuild_snapshots(db, sem, u.staff_no, only={no})
            snap = db.scalar(select(StandardSnapshot).where(StandardSnapshot.semester_id == sem.id,
                                                            StandardSnapshot.student_no == no))
            changed.add("band")
        services.recompute_students(db, sem, {no})
    if not changed:
        return {"ok": True, "changed": []}
    after = {"name": name, "gender": gender, "college": college, "band": snap.band if snap else None,
             "pending": bool(snap and snap.pending)}
    services.log(db, u.staff_no, "教师修改学生信息", f"{no} {name}", sem.id, ip=client_ip(request),
                 student_no=no, class_no=c.class_no, class_name=c.class_name,
                 fields=sorted(changed), old=before, new=after)
    db.commit()
    return {"ok": True, "changed": sorted(changed)}


@app.get("/api/classes/{cid}/check")
def check_class(cid: int, u: User = Depends(current_user), db: Session = Depends(get_db)):
    c, sem = _class(db, cid, u)
    return services.submit_check(services.load_class_rows(db, c, sem), services.class_config(c, sem))


@app.post("/api/classes/{cid}/submit")
def submit_class(cid: int, request: Request, u: User = Depends(current_user), db: Session = Depends(get_db)):
    c, sem = _class(db, cid, u)
    ok, why = services.can_edit(c, sem)
    if not ok:
        raise HTTPException(403, why)
    if c.status == "已提交":
        raise HTTPException(400, "本班体测成绩已经提交")
    chk = services.submit_check(services.load_class_rows(db, c, sem), services.class_config(c, sem))
    if chk["pending"]:
        raise HTTPException(400, f"本班还有 {len(chk['pending'])} 名学生的年级或性别待确认，"
                                 "可在该生的“更多”里核对后直接修改，或等体育部确认")
    if chk["missing"]:
        raise HTTPException(400, f"本班还有 {len(chk['missing'])} 名学生的体测成绩没有录完（没填成绩也没标特殊情况）")
    c.status, c.submitted_at, c.submitted_by = "已提交", now(), u.staff_no
    services.log(db, u.staff_no, "提交", f"{c.class_no} {c.class_name}", sem.id, ip=client_ip(request),
                 missing=len(chk["missing"]), sudden=len(chk["sudden"]))
    db.commit()
    return {"ok": True}


@app.post("/api/classes/{cid}/withdraw")
def withdraw_class(cid: int, request: Request, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """教师撤回体测提交：直接解锁本班体测，不用填原因、不用体育部审核。体育部锁定了学期时不能撤回。"""
    c, sem = _class(db, cid, u)
    ok, why = services.can_edit(c, sem)
    if not ok:
        raise HTTPException(403, why)
    if c.status != "已提交":
        raise HTTPException(400, "本班体测成绩尚未提交，无需撤回")
    if sem.pe_locked:
        raise HTTPException(403, services.SEM_LOCKED_MSG)
    c.status = "录入中"
    services.log(db, u.staff_no, "撤回提交", f"{c.class_no} {c.class_name}", sem.id, ip=client_ip(request))
    db.commit()
    return {"ok": True}


@app.get("/api/classes/{cid}/history/{eid}")
def score_history(cid: int, eid: int, u: User = Depends(current_user), db: Session = Depends(get_db)):
    c, sem = _class(db, cid, u)
    e = db.get(Enrollment, eid)
    if not e or e.class_id != c.id:
        raise HTTPException(404)
    hs = db.scalars(select(ScoreHistory).where(ScoreHistory.enrollment_id == eid).order_by(ScoreHistory.id.desc())
                    .limit(200)).all()
    names = {u.staff_no: u.name for u in db.scalars(select(User))}
    snap = db.scalar(select(StandardSnapshot).where(StandardSnapshot.semester_id == sem.id,
                                                    StandardSnapshot.student_no == e.student_no))
    st = db.get(Student, e.student_no)
    shown = services.student_display(e, st)
    return {"history": [{"field": h.field, "old": h.old, "new": h.new, "computed": h.computed,
                         "version": h.standard_version, "operator": f"{names.get(h.operator, '')}({h.operator})",
                         "at": h.at.strftime("%Y-%m-%d %H:%M:%S"), "batch": h.batch} for h in hs],
            "standard": _snap_dict(snap),
            "student": {"name": shown["name"], "college": shown["college"], "gender": st.gender if st else None,
                        "manual": bool(st and st.manual)},
            "issue": {"note": e.info_error, "resolved": bool(e.info_error_resolved),
                      "resolved_at": e.info_error_resolved_at.strftime("%Y-%m-%d %H:%M") if e.info_error_resolved_at else None}}


def _snap_dict(snap: StandardSnapshot | None):
    if not snap:
        return None
    return {"student_no": snap.student_no, "grade_num": snap.grade_num, "band": snap.band,
            "band_label": services.band_label(snap.band), "source": snap.source, "detail": snap.detail,
            "pending": snap.pending, "reasons": snap.pending_reasons, "decided_by": snap.decided_by,
            "decided_at": snap.decided_at.strftime("%Y-%m-%d %H:%M")}


@app.get("/api/classes/{cid}/export")
def export_class(cid: int, request: Request, sort: str = "no", u: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    c, sem = _class(db, cid, u)
    rows = services.sort_rows(services.load_class_rows(db, c, sem), sort)
    data = exporter.build_class_workbook(sem, c, rows, services.class_config(c, sem), f"{u.name}({u.staff_no})")
    services.log(db, u.staff_no, "导出", f"{c.class_no} {c.class_name}", sem.id, ip=client_ip(request))
    db.commit()
    return _xlsx(data, f"{sem.name}-{c.class_name}-成绩.xlsx")


@app.get("/api/classes/{cid}/template")
def class_template(cid: int, u: User = Depends(current_user), db: Session = Depends(get_db)):
    c, sem = _class(db, cid, u)
    # 模板按学号升序，与网页录入表默认顺序一致；不受选课表导入行序和后续补录顺序影响
    rows = services.sort_rows(services.load_class_rows(db, c, sem), "no")
    return _xlsx(exporter.build_template(sem, c, rows, services.class_config(c, sem)),
                 f"{c.class_name}-成绩录入模板.xlsx")


def _xlsx(data: bytes, filename: str) -> Response:
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


# ---------------------------------------------------------------- 教师整班上传（备用路径）

def _upload_header_map(head: list[str], la: str, lb: str) -> dict:
    """表头 → 字段。新模板状态并在成绩格里；老模板的“××原始成绩”“××状态”两列照样认。"""
    colmap = {}
    for i, h in enumerate(head):
        if h == "学号":
            colmap["student_no"] = i
        elif h in (f"{la}原始成绩", f"{la}成绩", la):
            colmap["a"] = i
        elif h == f"{la}状态":
            colmap["a_status"] = i
        elif h in (f"{lb}原始成绩", f"{lb}成绩", lb):
            colmap["b"] = i
        elif h == f"{lb}状态":
            colmap["b_status"] = i
        elif h.startswith("运动技能"):
            colmap["skill"] = i
        elif h.startswith("平时"):
            colmap["regular"] = i
        elif h.startswith("课程成绩"):
            colmap["course_status"] = i
    return colmap


def _upload_cell(v):
    if v is None:
        return None
    v = importer.clean(v) if not isinstance(v, (int, float)) else v
    return None if v == "" else v


def _num_text(v) -> str:
    return "" if v is None else f"{v:g}"


def _upload_units(row: dict, cells: dict, cfg: dict) -> tuple[list[dict], list[dict]]:
    """把一行表里的值拆成要比较的格子。

    每个格子：label、sys（平台显示的文字，空串表示平台里没有）、new（表里的文字）、ops（选“用表里的”时要写的字段，
    状态在前）、better（两边都是成绩时，平台的是否更好）。表里和平台一样的格子不返回。
    格子和平台录入表一致：两项体测各一格（成绩或特殊情况）；运动技能、平时成绩各一格，课程成绩特殊情况显示在这两格里。
    """
    vals, units, errors = row["values"], [], []
    gender = row["gender_code"]
    labels = {"a": cfg["slots"][0].get("label", "项目一"), "b": cfg["slots"][1].get("label", "项目二"),
              "skill": "运动技能评定", "regular": "平时成绩"}

    def parse(f, v):
        try:
            return services.parse_cell(f, v, cfg, gender)
        except scoring.ParseError as e:
            key = f[0] if f in ("a_raw", "b_raw", "a_status", "b_status") else f
            errors.append({"field": labels.get(key, "课程成绩"), "value": str(v), "error": str(e)})
            return None

    # 两项体测：一格里写成绩或特殊情况
    for k in ("a", "b"):
        raw, st = cells.get(k), cells.get(f"{k}_status")
        text = None if raw is None else scoring.normalize_status(str(raw).strip())
        if text in scoring.STATUSES:          # 成绩格里写的是特殊情况（新模板）
            st, raw = text, None
        elif st is not None:
            st = parse(f"{k}_status", st)
            if st is None:
                continue
        if st == "正常" and raw is None:
            st = None                          # 老模板状态写“正常”、成绩空着：不算改动
        if st is None and raw is None:
            continue
        sys_st = vals.get(f"{k}_status") or "正常"
        sys_raw = vals.get(f"{k}_raw")
        sys_text = sys_st if sys_st != "正常" else row["display"][k]["raw_text"]
        if st is not None and st != "正常":
            if st == sys_st:
                continue
            units.append({"label": labels[k], "sys": sys_text, "new": st, "better": False,
                          "ops": [(f"{k}_status", st)]})
            continue
        val = parse(f"{k}_raw", raw)
        if val is None:
            continue
        if sys_st == "正常" and val == sys_raw:
            continue
        better = False
        if sys_st == "正常" and sys_raw is not None:
            item = next(s for s in cfg["slots"] if s["key"] == k)["items"][gender]
            better = score_import.better(item, sys_raw, val)
        ops = ([(f"{k}_status", "正常")] if sys_st != "正常" else []) + [(f"{k}_raw", raw)]
        units.append({"label": labels[k], "sys": sys_text, "new": str(raw), "better": better, "ops": ops})

    # 课程成绩特殊情况写在运动技能、平时成绩格里
    sys_course = vals.get("course_status") or "正常"
    course = cells.get("course_status")
    if course is not None:                     # 未发布过的过渡版式里单独一列
        course = parse("course_status", str(course).strip())
    texts = {f: cells.get(f) for f in ("skill", "regular")}
    in_cells = {str(v).strip() for v in texts.values()
                if isinstance(v, str) and str(v).strip() in scoring.COURSE_STATUSES and str(v).strip() != "正常"}
    if len(in_cells) > 1:
        errors.append({"field": "课程成绩", "value": "、".join(sorted(in_cells)),
                       "error": "运动技能和平时成绩两格写了不同的课程成绩特殊情况"})
        return [], errors
    if in_cells:
        c = in_cells.pop()
        others = [f for f, v in texts.items() if v is not None and str(v).strip() != c]
        if others:
            errors.append({"field": labels[others[0]], "value": str(texts[others[0]]),
                           "error": f"课程成绩是“{c}”时这一格不用填成绩，和另一格一样写“{c}”或留空"})
            return [], errors
        course, texts = c, {}
    if course is not None and course != sys_course:
        units.append({"label": "课程成绩", "sys": "" if sys_course == "正常" else sys_course, "new": course,
                      "better": False, "ops": [("course_status", course)]})
    for f, v in texts.items():
        if v is None:
            continue
        val = parse(f, v)
        if val is None:
            continue
        cur = vals.get(f)
        if sys_course != "正常" and course in (None, sys_course):
            # 平台里这一格显示的是课程成绩特殊情况，表里填了成绩：选表里的就把课程成绩改回正常
            units.append({"label": labels[f], "sys": sys_course, "new": _num_text(val), "better": False,
                          "ops": [("course_status", "正常"), (f, v)]})
            continue
        if val == cur:
            continue
        units.append({"label": labels[f], "sys": _num_text(cur), "new": _num_text(val),
                      "better": cur is not None and cur > val, "ops": [(f, v)]})
    return units, errors


@app.post("/api/classes/{cid}/upload")
async def upload_scores(cid: int, file: UploadFile = File(...), u: User = Depends(current_user),
                        db: Session = Depends(get_db)):
    """预检整班上传。平台里空着的格子直接写；平台里有、和表里不一样的列出来让老师逐格选；一样的不显示。

    模板第 2 行是示例（学号格写“示例”），学生名单下面是填写说明（以“填写说明”开头的一行起），都跳过；
    老模板（第 2 行起就是学生、体测状态单独成列）照样能传。
    """
    c, sem = _class(db, cid, u)
    ok, why = services.can_edit(c, sem)
    if not ok:
        raise HTTPException(403, why)
    data = await file.read()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise HTTPException(400, "无法读取文件，请使用平台下载的 .xlsx 模板")
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise HTTPException(400, "文件是空的")
    head = [importer.clean(h) for h in rows[0]]
    cfg = services.class_config(c, sem)
    colmap = _upload_header_map(head, cfg["slots"][0].get("label", "项目一"), cfg["slots"][1].get("label", "项目二"))
    if "student_no" not in colmap:
        raise HTTPException(400, "找不到“学号”列，请使用平台下载的模板")
    roster = {r["student_no"]: r for r in services.load_class_rows(db, c, sem)}
    fills, conflicts, errors, unknown = [], [], [], []
    for n, r in enumerate(rows[1:], start=2):
        sno = importer.clean(r[colmap["student_no"]]) if colmap["student_no"] < len(r) else ""
        if sno.startswith("填写说明"):
            break  # 名单下面的填写说明
        if not sno or sno == "示例":
            continue
        row = roster.get(sno)
        if not row:
            unknown.append(f"第{n}行 {sno}")
            continue
        cells = {f: _upload_cell(r[i]) for f, i in colmap.items() if f != "student_no" and i < len(r)}
        units, errs = _upload_units(row, {f: v for f, v in cells.items() if v is not None}, cfg)
        errors += [{"row": n, "student_no": sno, "name": row["name"], **e} for e in errs]
        for x in units:
            x.update(row=n, student_no=sno, name=row["name"], enrollment_id=row["enrollment_id"])
            if x["sys"] == "":
                fills.append(x)
            else:
                x["id"] = len(conflicts)
                x["default"] = "system" if x["better"] else "table"
                conflicts.append(x)
    token = uuid.uuid4().hex
    (importer.UPLOAD_DIR / f"scores-{token}.json").write_text(
        __import__("json").dumps({"class_id": c.id, "fills": fills, "conflicts": conflicts}, ensure_ascii=False))
    public = lambda xs: [{k: v for k, v in x.items() if k not in ("ops", "enrollment_id", "better")} for x in xs]
    return {"token": token, "fills": public(fills), "conflicts": public(conflicts),
            "students": len({x["enrollment_id"] for x in fills + conflicts}),
            "errors": errors, "unknown": unknown, "roster": len(roster)}


@app.post("/api/classes/{cid}/upload/commit")
def upload_commit(cid: int, request: Request, token: str = Body(...), use_table: list[int] | None = Body(None),
                  u: User = Depends(current_user), db: Session = Depends(get_db)):
    """写入：平台里空着的格子全部写；不一致的格子只写 use_table 里列出的（不传就按预检的默认选择）。"""
    import json
    c, sem = _class(db, cid, u)
    ok, why = services.can_edit(c, sem)
    if not ok:
        raise HTTPException(403, why)
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise HTTPException(400)
    p = importer.UPLOAD_DIR / f"scores-{token}.json"
    if not p.exists():
        raise HTTPException(400, "上传内容已过期，请重新上传")
    payload = json.loads(p.read_text())
    if payload["class_id"] != c.id:
        raise HTTPException(400)
    chosen = set(use_table) if use_table is not None else {x["id"] for x in payload["conflicts"]
                                                          if x["default"] == "table"}
    units = payload["fills"] + [x for x in payload["conflicts"] if x["id"] in chosen]
    changes = [{"enrollment_id": x["enrollment_id"], "field": f, "value": v} for x in units for f, v in x["ops"]]
    batch = f"imp-{token[:12]}"
    res = services.save_cells(db, c, sem, changes, u.staff_no, batch=batch)
    services.log(db, u.staff_no, "导入", f"{c.class_no} {c.class_name}", sem.id, ip=client_ip(request),
                 batch=batch, cells=len(units))
    db.commit()
    p.unlink(missing_ok=True)
    failed = [r for r in res["results"] if not r.get("ok")]
    return {"ok": True, "batch": batch, "saved": len(units), "kept": len(payload["conflicts"]) - len(chosen & {
        x["id"] for x in payload["conflicts"]}), "failed": [r.get("error") for r in failed]}


@app.post("/api/classes/{cid}/upload/{batch}/revert")
def upload_revert(cid: int, batch: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    c, sem = _class(db, cid, u)
    ok, why = services.can_edit(c, sem)
    if not ok:
        raise HTTPException(403, why)
    hs = db.scalars(select(ScoreHistory).where(ScoreHistory.batch == batch).order_by(ScoreHistory.id.desc())).all()
    eids = {e for e in db.scalars(select(Enrollment.id).where(Enrollment.class_id == c.id))}
    hs = [h for h in hs if h.enrollment_id in eids]
    if not hs:
        raise HTTPException(404, "没有找到这次导入")
    if services.pe_locked(c):
        hs = [h for h in hs if h.field not in services.PE_FIELDS]
    for h in hs:
        rec = db.scalar(select(ScoreRecord).where(ScoreRecord.enrollment_id == h.enrollment_id))
        if rec is None:
            continue
        old = h.old
        if h.field in ("a_raw", "b_raw", "skill", "regular"):
            old = None if old is None else float(old)
        elif h.field in ("a_status", "b_status"):
            old = old or "正常"
        cur = getattr(rec, h.field)
        setattr(rec, h.field, old)
        db.add(ScoreHistory(enrollment_id=h.enrollment_id, field=h.field, old=None if cur is None else str(cur),
                            new=h.old, operator=u.staff_no, standard_version=rec.standard_version,
                            computed={"reason": f"回退导入 {batch}"}))
    db.flush()
    services.recompute_class(db, c, sem)
    services.log(db, u.staff_no, "回退导入", f"{c.class_no} {c.class_name}", sem.id, batch=batch)
    db.commit()
    return {"ok": True, "reverted": len(hs)}


@app.get("/api/standards/{item}")
def standard_table(item: str, u: User = Depends(current_user), version: str = "GB2014"):
    std = scoring.load_standard(version)
    if item not in std["items"]:
        raise HTTPException(404)
    it = std["items"][item]
    return {"name": it["name"], "unit": it["unit"], "direction": it["direction"], "tables": it["tables"],
            "bonus": it.get("bonus"), "version": version, "standard_name": std["name"],
            "time": item in scoring.TIME_ITEMS}


# ================================================================= 管理端：导入

@app.post("/api/admin/import/upload")
async def admin_import_upload(kind: str = Form(...), file: UploadFile = File(...), u: User = Depends(admin_user),
                              db: Session = Depends(get_db)):
    if kind not in importer.FIELDS:
        raise HTTPException(400, "未知的表类型")
    try:
        token = importer.save_upload(await file.read(), file.filename or "x.xlsx")
        return importer.inspect_file(db, token, kind)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(400, "无法读取这个 Excel 文件")


def _import_args(body):
    for k in ("token", "kind", "sheet", "header_row", "mapping"):
        if k not in body:
            raise HTTPException(400, f"缺少参数 {k}")
    mode = body.get("mode", "overwrite")
    if mode not in importer.MODES:
        raise HTTPException(400, "未知的导入方式")
    return body["token"], body["kind"], body["sheet"], int(body["header_row"]), body["mapping"], mode


@app.post("/api/admin/import/precheck")
def admin_import_precheck(body: dict = Body(...), u: User = Depends(admin_user), db: Session = Depends(get_db)):
    token, kind, sheet, hr, mapping, mode = _import_args(body)
    sem = _sem(db, int(body["semester_id"])) if kind != "base" else None
    try:
        rows, footers = importer.read_rows(token, sheet, hr, mapping, kind)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    rep, plan = importer.precheck(db, kind, rows, mapping, sem, mode)
    if footers:
        rep.add("info", f"跳过 {footers} 行表尾（如“制表单位”“打印日期”）")
    return {"rows": len(rows), "plan": plan, "items": rep.items, "blocking": rep.blocking}


@app.post("/api/admin/import/commit")
def admin_import_commit(request: Request, body: dict = Body(...), u: User = Depends(admin_user),
                        db: Session = Depends(get_db)):
    token, kind, sheet, hr, mapping, mode = _import_args(body)
    sem = _sem(db, int(body["semester_id"])) if kind != "base" else None
    if mode == "rebuild" and not body.get("confirm_rebuild"):
        raise HTTPException(400, "重建须二次确认")
    rows, footers = importer.read_rows(token, sheet, hr, mapping, kind)
    rep, plan = importer.precheck(db, kind, rows, mapping, sem, mode)
    if rep.blocking:
        raise HTTPException(400, "预检有阻断性错误，请修正后重新上传")
    if plan.get("scored_impact") and not body.get("confirm_scored"):
        raise HTTPException(400, "这次导入会动到已有成绩的教学班，须确认后再导入：" + plan["scored_impact"]["message"])
    result = importer.commit(db, kind, rows, sem, mode, u.staff_no)
    importer.remember_mapping(db, kind, mapping)
    services.log(db, u.staff_no, "导入", importer.KIND_NAMES[kind], sem.id if sem else None, ip=client_ip(request),
                 mode=importer.MODES[mode], rows=len(rows), result=result,
                 warnings=[i["message"] for i in rep.items if i["level"] == "warn"][:50])
    db.commit()
    return {"ok": True, "result": result}


# ---------------------------------------------------------------- 管理端：导入体测成绩（取最优）

def _score_import_plan(db: Session, sem: Semester, token: str) -> dict:
    ok, why = services.can_edit(None, sem)
    if not ok:
        raise HTTPException(403, why)
    try:
        entries, notes = score_import.read_entries(importer._path(token))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(400, "无法读取这个 Excel 文件")
    p = score_import.plan(db, sem, entries)
    p["notes"] = notes
    return p


@app.post("/api/admin/score-import/upload")
async def score_import_upload(semester_id: int = Form(...), file: UploadFile = File(...),
                              u: User = Depends(admin_user), db: Session = Depends(get_db)):
    """上传成绩表，返回预检：将更新哪些成绩、跳过哪些及原因。不写数据库。"""
    sem = _sem(db, semester_id)
    try:
        token = importer.save_upload(await file.read(), file.filename or "x.xlsx")
    except ValueError as e:
        raise HTTPException(400, str(e))
    p = _score_import_plan(db, sem, token)
    return {"token": token, **p}


@app.post("/api/admin/score-import/commit")
def score_import_commit(request: Request, semester_id: int = Body(...), token: str = Body(...),
                        u: User = Depends(admin_user), db: Session = Depends(get_db)):
    """按上传的文件重新预检一遍再写入（预检之后有人改过成绩，以写入时为准）。"""
    sem = _sem(db, semester_id)
    p = _score_import_plan(db, sem, token)
    batch = f"pe-{uuid.uuid4().hex[:12]}"
    saved = score_import.commit(db, sem, p["updates"], u.staff_no, batch)
    services.log(db, u.staff_no, "导入", "体测成绩", sem.id, ip=client_ip(request), batch=batch,
                 updated=saved, students=p["students"], skipped=len(p["skips"]), reasons=p["reason_counts"])
    db.commit()
    return {"ok": True, "batch": batch, "saved": saved, "students": p["students"], "skipped": len(p["skips"])}


@app.post("/api/admin/score-import/{batch}/revert")
def score_import_revert(batch: str, request: Request, semester_id: int = Body(..., embed=True),
                        u: User = Depends(admin_user), db: Session = Depends(get_db)):
    sem = _sem(db, semester_id)
    ok, why = services.can_edit(None, sem)
    if not ok:
        raise HTTPException(403, why)
    if not re.fullmatch(r"pe-[0-9a-f]{12}", batch):
        raise HTTPException(400)
    n = services.revert_batch(db, sem, batch, u.staff_no)
    if n is None:
        raise HTTPException(404, "没有找到这次导入")
    services.log(db, u.staff_no, "回退导入", "体测成绩", sem.id, ip=client_ip(request), batch=batch, reverted=n)
    db.commit()
    return {"ok": True, "reverted": n}


# ================================================================= 管理端：待确认清单与标准档

@app.get("/api/admin/semesters/{sid}/pending")
def pending_list(sid: int, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    sem = _sem(db, sid)
    snaps = db.scalars(select(StandardSnapshot).where(StandardSnapshot.semester_id == sid,
                                                      StandardSnapshot.pending.is_(True))).all()
    nos = [s.student_no for s in snaps]
    enr = db.execute(select(Enrollment, TeachingClass).join(TeachingClass, TeachingClass.id == Enrollment.class_id)
                     .where(Enrollment.semester_id == sid, Enrollment.student_no.in_(nos))).all() if nos else []
    per = {}
    for e, c in enr:
        per.setdefault(e.student_no, []).append({"enrollment_id": e.id, "active": e.active, "class_id": c.id,
                                                 "class_name": c.class_name, "teacher": c.teacher_name,
                                                 "class_label": e.class_label, "grade_field": e.grade_field,
                                                 "name": e.name})
    stus = {s.student_no: s for s in db.scalars(select(Student).where(Student.student_no.in_(nos)))} if nos else {}
    out = []
    for s in snaps:
        st = stus.get(s.student_no)
        es = per.get(s.student_no, [])
        out.append({**_snap_dict(s), "name": (es[0]["name"] if es else "") or (st.name if st else ""),
                    "gender": {"M": "男", "F": "女"}.get(st.gender if st else None, "待确认"),
                    "enrollments": es})
    out.sort(key=lambda x: x["student_no"])
    reasons = Counter(r.split("（")[0] for x in out for r in x["reasons"])
    teacher_pending = [class_brief(c) for c in sorted(db.scalars(select(TeachingClass).where(
        TeachingClass.semester_id == sid, TeachingClass.teacher_pending.is_(True))), key=services.class_sort_key)]
    info_errors = [{"enrollment_id": e.id, "student_no": e.student_no, "name": e.name, "class_name": c.class_name,
                    "teacher": c.teacher_name, "note": e.info_error, "resolved": bool(e.info_error_resolved)}
                   for e, c in db.execute(select(Enrollment, TeachingClass).join(
                       TeachingClass, TeachingClass.id == Enrollment.class_id).where(
                       Enrollment.semester_id == sid, Enrollment.active.is_(True), Enrollment.info_error.is_not(None))
                       .order_by(Enrollment.info_error_resolved, Enrollment.student_no))]
    return {"semester": sem_dict(sem), "students": out, "summary": reasons, "teacher_pending": teacher_pending,
            "info_errors": info_errors, "teacher_edits": _teacher_edits(db, sid)}


def _teacher_edits(db: Session, sid: int, limit: int = 500) -> list[dict]:
    """教师在录入页改过的学生（姓名、性别、学院、标准档），供体育部抽查。"""
    names = {x.staff_no: x.name for x in db.scalars(select(User))}
    logs = db.scalars(select(AuditLog).where(AuditLog.semester_id == sid, AuditLog.action == "教师修改学生信息")
                      .order_by(AuditLog.id.desc()).limit(limit)).all()
    label = {"name": "姓名", "gender": "性别", "college": "学院", "band": "标准档"}
    show = {"gender": lambda v: {"M": "男", "F": "女"}.get(v, "待确认"), "band": services.band_label}
    out = []
    for l in logs:
        d = l.detail or {}
        old, new = d.get("old") or {}, d.get("new") or {}
        changes = [f"{label.get(f, f)}：{show.get(f, lambda v: v or '空')(old.get(f))} → {show.get(f, lambda v: v or '空')(new.get(f))}"
                   for f in d.get("fields", [])]
        out.append({"at": l.at.strftime("%Y-%m-%d %H:%M"), "teacher": f"{names.get(l.actor, '')}({l.actor})",
                    "student_no": d.get("student_no", ""), "name": new.get("name", ""),
                    "class_name": d.get("class_name", ""), "changes": changes})
    return out


@app.post("/api/admin/enrollments/{eid}/issue-resolved")
def resolve_issue(eid: int, resolved: bool = Body(True, embed=True), u: User = Depends(admin_user),
                  db: Session = Depends(get_db)):
    """体育部把教师提交的“其他问题”标为已处理（教师端显示已处理）。"""
    e, c, sem = _enrollment_for_admin(db, eid)
    if not e.info_error:
        raise HTTPException(400, "这名学生没有待处理的问题")
    e.info_error_resolved = bool(resolved)
    e.info_error_resolved_by, e.info_error_resolved_at = (u.staff_no, now()) if resolved else (None, None)
    services.log(db, u.staff_no, "处理其他问题", f"{e.student_no} {e.name}", sem.id, resolved=resolved,
                 note=e.info_error, class_no=c.class_no)
    db.commit()
    return {"ok": True}


@app.get("/api/admin/semesters/{sid}/students/{no}")
def student_standard(sid: int, no: str, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    snap = db.scalar(select(StandardSnapshot).where(StandardSnapshot.semester_id == sid,
                                                    StandardSnapshot.student_no == no))
    corrs = db.scalars(select(GradeCorrection).where(GradeCorrection.student_no == no)
                       .order_by(GradeCorrection.id.desc())).all()
    st = db.get(Student, no)
    return {"standard": _snap_dict(snap),
            "student": None if not st else {"name": st.name, "gender": st.gender, "college": st.college},
            "corrections": [{"cohort": c.cohort, "band": c.band, "change_type": c.change_type,
                             "effective_semester": c.effective_semester, "note": c.note, "operator": c.operator,
                             "at": c.created_at.strftime("%Y-%m-%d %H:%M")} for c in corrs]}


@app.post("/api/admin/semesters/{sid}/students/{no}/standard")
def set_standard(sid: int, no: str, request: Request, body: dict = Body(...), u: User = Depends(admin_user),
                 db: Session = Depends(get_db)):
    """体育部确认年级/标准档：写入年级校正名单（第一优先），重算该生成绩。"""
    sem = _sem(db, sid)
    cohort = body.get("cohort")
    band = body.get("band")
    if not cohort and band not in ("12", "34"):
        raise HTTPException(400, "请填写实际跟读年级（如2024）或直接指定标准档")
    if cohort:
        cohort = int(cohort)
        if not (sem.start_year - 10 <= cohort <= sem.start_year):
            raise HTTPException(400, "年级不在合理范围内")
    note = str(body.get("note", "")).strip()
    if not note:
        raise HTTPException(400, "请注明依据")
    old = db.scalar(select(StandardSnapshot).where(StandardSnapshot.semester_id == sid,
                                                   StandardSnapshot.student_no == no))
    old_band = old.band if old else None
    db.add(GradeCorrection(student_no=no, cohort=cohort or None, band=None if cohort else band,
                           change_type=body.get("change_type") or "体育部确认",
                           effective_semester=body.get("effective_semester") or sem.code, note=note,
                           operator=u.staff_no))
    db.flush()
    grading.rebuild_snapshots(db, sem, u.staff_no, only={no})
    new = db.scalar(select(StandardSnapshot).where(StandardSnapshot.semester_id == sid,
                                                   StandardSnapshot.student_no == no))
    if new and old_band and new.band != old_band:
        new.changed_notice = True
    services.recompute_students(db, sem, {no})
    services.log(db, u.staff_no, "改档", no, sid, ip=client_ip(request), old_band=old_band,
                 new_band=new.band if new else None, cohort=cohort, note=note)
    db.commit()
    return {"ok": True, "standard": _snap_dict(new)}


# ---------------------------------------------------------------- 管理端：学生信息与选课的手动维护

STUDENT_FIELDS = ("name", "college")  # 班级、专业、年级不再存（以选课表为准）
ENROLL_FIELDS = ("name", "class_label", "major", "grade_field", "college", "campus")


def _student_dict(st: Student | None) -> dict | None:
    if not st:
        return None
    return {"student_no": st.student_no, "name": st.name, "gender": st.gender, "college": st.college,
            "manual": st.manual}


def _enroll_dict(e: Enrollment, c: TeachingClass) -> dict:
    return {"enrollment_id": e.id, "active": e.active, "manual": e.manual, "class_id": c.id, "class_no": c.class_no,
            "class_name": c.class_name, "course_name": c.course_name, "teacher": c.teacher_name,
            "class_status": c.status, **{f: getattr(e, f) for f in ENROLL_FIELDS}, "info_error": e.info_error,
            "info_error_resolved": bool(e.info_error_resolved)}


def _refresh_student(db: Session, sem: Semester, no: str, operator: str):
    grading.rebuild_snapshots(db, sem, operator, only={no})
    services.recompute_students(db, sem, {no})


@app.get("/api/admin/semesters/{sid}/students")
def search_students(sid: int, q: str = "", u: User = Depends(admin_user), db: Session = Depends(get_db)):
    q = q.strip()
    if len(q) < 2:
        return {"students": []}
    like = f"%{q}%"
    nos = set(db.scalars(select(Enrollment.student_no).where(
        Enrollment.semester_id == sid, (Enrollment.student_no.like(like)) | (Enrollment.name.like(like))).limit(50)))
    nos |= set(db.scalars(select(Student.student_no).where(
        (Student.student_no.like(like)) | (Student.name.like(like))).limit(50)))
    out = []
    for no in sorted(nos)[:50]:
        st = db.get(Student, no)
        es = db.execute(select(Enrollment, TeachingClass).join(TeachingClass, TeachingClass.id == Enrollment.class_id)
                        .where(Enrollment.semester_id == sid, Enrollment.student_no == no)).all()
        out.append({"student_no": no, "name": (st.name if st else "") or (es[0][0].name if es else ""),
                    "gender": {"M": "男", "F": "女"}.get(st.gender if st else None, "待确认"),
                    "classes": [c.class_name for e, c in es if e.active]})
    return {"students": out}


@app.get("/api/admin/semesters/{sid}/student/{no}")
def student_detail(sid: int, no: str, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    _sem(db, sid)
    es = db.execute(select(Enrollment, TeachingClass).join(TeachingClass, TeachingClass.id == Enrollment.class_id)
                    .where(Enrollment.semester_id == sid, Enrollment.student_no == no)
                    .order_by(Enrollment.active.desc(), TeachingClass.course_name)).all()
    snap = db.scalar(select(StandardSnapshot).where(StandardSnapshot.semester_id == sid,
                                                    StandardSnapshot.student_no == no))
    classes = [{"id": c.id, "label": f"{c.course_name} · {c.class_name}（{c.teacher_name or '教师待补'}）{c.class_no}"}
               for c in sorted(db.scalars(select(TeachingClass).where(TeachingClass.semester_id == sid)),
                               key=services.class_sort_key)]
    return {"student_no": no, "student": _student_dict(db.get(Student, no)), "standard": _snap_dict(snap),
            "enrollments": [_enroll_dict(e, c) for e, c in es], "classes": classes}


@app.put("/api/admin/semesters/{sid}/student/{no}")
def save_student(sid: int, no: str, request: Request, body: dict = Body(...), u: User = Depends(admin_user),
                 db: Session = Depends(get_db)):
    """补录或修改学生基础信息（性别、姓名、学院等）。保存后重新判定标准档并重算成绩。"""
    sem = _sem(db, sid)
    if not re.fullmatch(r"[0-9A-Za-z]{4,16}", no):
        raise HTTPException(400, "学号格式不正确")
    gender = {"男": "M", "女": "F", "M": "M", "F": "F"}.get(str(body.get("gender", "")).strip())
    if not gender:
        raise HTTPException(400, "请选择性别")
    st = db.get(Student, no)
    old = _student_dict(st)
    if st is None:
        st = Student(student_no=no)
        db.add(st)
    st.gender = gender
    for f in STUDENT_FIELDS:
        if f in body:
            setattr(st, f, str(body[f] or "").strip()[:128])
    st.manual = True
    db.flush()
    _refresh_student(db, sem, no, u.staff_no)
    services.log(db, u.staff_no, "修改学生信息", no, sid, ip=client_ip(request), old=old, new=_student_dict(st))
    db.commit()
    return student_detail(sid, no, u, db)


def _enrollment_for_admin(db: Session, eid: int) -> tuple[Enrollment, TeachingClass, Semester]:
    e = db.get(Enrollment, eid)
    if not e:
        raise HTTPException(404, "选课记录不存在")
    c = db.get(TeachingClass, e.class_id)
    return e, c, c.semester


@app.patch("/api/admin/enrollments/{eid}")
def update_enrollment(eid: int, request: Request, body: dict = Body(...), u: User = Depends(admin_user),
                      db: Session = Depends(get_db)):
    """修改一条选课记录：上课班级、专业、上课年级、院系、校区，或调到本学期另一个教学班。已有成绩跟着走。"""
    e, c, sem = _enrollment_for_admin(db, eid)
    old = _enroll_dict(e, c)
    target = c
    if body.get("class_id") and int(body["class_id"]) != c.id:
        target = db.get(TeachingClass, int(body["class_id"]))
        if not target or target.semester_id != sem.id:
            raise HTTPException(400, "目标教学班不在本学期")
        dup = db.scalar(select(Enrollment).where(Enrollment.class_id == target.id, Enrollment.student_no == e.student_no))
        if dup and dup.active:
            raise HTTPException(400, f"该生已在 {target.class_name} 里")
        if dup:  # 以前从目标班移除过：删掉那条空记录（有成绩的不删）
            if db.scalar(select(ScoreRecord).where(ScoreRecord.enrollment_id == dup.id)):
                raise HTTPException(400, f"该生在 {target.class_name} 留有以前的成绩记录，请先联系开发人员处理")
            db.delete(dup)
            db.flush()
        # 原班留一条停用记录，防止重新导入时又被加回原班；成绩随人转到新班
        ghost = Enrollment(semester_id=sem.id, class_id=c.id, student_no=e.student_no, name=e.name,
                           class_label=e.class_label, major=e.major, grade_field=e.grade_field, college=e.college,
                           campus=e.campus, course_category=e.course_category, active=False, manual=True, seq=e.seq)
        e.class_id = target.id
        e.seq = (db.scalar(select(func.max(Enrollment.seq)).where(Enrollment.class_id == target.id)) or 0) + 1
        db.flush()
        db.add(ghost)
    for f in ENROLL_FIELDS:
        if f in body:
            setattr(e, f, str(body[f] or "").strip()[:128])
    e.active, e.manual = True, True
    if e.info_error and not e.info_error_resolved:  # 体育部改了这条选课，教师提的问题视为已处理
        e.info_error_resolved, e.info_error_resolved_by, e.info_error_resolved_at = True, u.staff_no, now()
    db.flush()
    _refresh_student(db, sem, e.student_no, u.staff_no)
    if target.id != c.id:
        services.recompute_class(db, target, sem)
    services.log(db, u.staff_no, "修改选课", f"{e.student_no} {e.name}", sem.id, ip=client_ip(request),
                 old=old, new=_enroll_dict(e, target))
    db.commit()
    return {"ok": True}


@app.delete("/api/admin/enrollments/{eid}")
def remove_enrollment(eid: int, request: Request, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    """把学生移出教学班（停用，不删除；已有成绩保留，可在原班恢复）。"""
    e, c, sem = _enrollment_for_admin(db, eid)
    e.active, e.manual = False, True
    db.flush()
    _refresh_student(db, sem, e.student_no, u.staff_no)
    services.log(db, u.staff_no, "移出教学班", f"{e.student_no} {e.name}", sem.id, ip=client_ip(request),
                 class_no=c.class_no, class_name=c.class_name)
    db.commit()
    return {"ok": True}


@app.post("/api/admin/enrollments/{eid}/restore")
def restore_enrollment(eid: int, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    e, c, sem = _enrollment_for_admin(db, eid)
    e.active, e.manual = True, True
    db.flush()
    _refresh_student(db, sem, e.student_no, u.staff_no)
    services.log(db, u.staff_no, "恢复选课", f"{e.student_no} {e.name}", sem.id, class_no=c.class_no)
    db.commit()
    return {"ok": True}


@app.post("/api/admin/semesters/{sid}/enrollments")
def add_enrollment(sid: int, request: Request, body: dict = Body(...), u: User = Depends(admin_user),
                   db: Session = Depends(get_db)):
    """把学生加进本学期某个教学班。"""
    sem = _sem(db, sid)
    no = str(body.get("student_no", "")).strip()
    if not re.fullmatch(r"[0-9A-Za-z]{4,16}", no):
        raise HTTPException(400, "学号格式不正确")
    c = db.get(TeachingClass, int(body.get("class_id") or 0))
    if not c or c.semester_id != sid:
        raise HTTPException(400, "请选择本学期的教学班")
    e = db.scalar(select(Enrollment).where(Enrollment.class_id == c.id, Enrollment.student_no == no))
    if e and e.active:
        raise HTTPException(400, f"该生已在 {c.class_name} 里")
    st = db.get(Student, no)
    if e is None:
        e = Enrollment(semester_id=sid, class_id=c.id, student_no=no,
                       seq=(db.scalar(select(func.max(Enrollment.seq)).where(Enrollment.class_id == c.id)) or 0) + 1)
        db.add(e)
    e.name = str(body.get("name") or (st.name if st else "")).strip()[:64]
    for f in ("class_label", "major", "grade_field", "college", "campus"):
        setattr(e, f, str(body.get(f) or "").strip()[:128])
    if not e.college and st:
        e.college = st.college
    if not e.campus:
        e.campus = c.campus
    e.active, e.manual = True, True
    db.flush()
    _refresh_student(db, sem, no, u.staff_no)
    services.log(db, u.staff_no, "加入教学班", f"{no} {e.name}", sid, ip=client_ip(request),
                 class_no=c.class_no, class_name=c.class_name)
    db.commit()
    return {"ok": True, "enrollment_id": e.id}


@app.post("/api/admin/semesters/{sid}/students/{no}/keep")
def keep_enrollment(sid: int, no: str, enrollment_id: int = Body(..., embed=True), u: User = Depends(admin_user),
                    db: Session = Depends(get_db)):
    """一人多条选课：保留一条有效记录，其余停用（不删除，已有成绩保留）。"""
    sem = _sem(db, sid)
    es = db.scalars(select(Enrollment).where(Enrollment.semester_id == sid, Enrollment.student_no == no)).all()
    if enrollment_id not in [e.id for e in es]:
        raise HTTPException(404)
    for e in es:
        e.active = e.id == enrollment_id
    db.flush()
    grading.rebuild_snapshots(db, sem, u.staff_no, only={no})
    services.log(db, u.staff_no, "确认选课记录", no, sid, keep=enrollment_id)
    db.commit()
    return {"ok": True}


@app.patch("/api/admin/classes/{cid}")
def admin_update_class(cid: int, body: dict = Body(...), u: User = Depends(admin_user), db: Session = Depends(get_db)):
    c = db.get(TeachingClass, cid)
    if not c:
        raise HTTPException(404)
    old = {"staff_no": c.staff_no, "teacher_name": c.teacher_name}
    if "staff_no" in body:
        # 只能从平台已有账号（含公共账号）里选，不再手填工号姓名临时建账号
        staff_no = str(body["staff_no"] or "").strip()
        x = db.get(User, staff_no) if staff_no else None
        if not x:
            raise HTTPException(400, f"平台里没有工号为 {staff_no} 的账号，请从账号列表中选择" if staff_no else "请选择教师")
        c.staff_no, c.teacher_name, c.teacher_pending = x.staff_no, x.name or c.teacher_name, False
    services.log(db, u.staff_no, "指定教师", f"{c.class_no} {c.class_name}", c.semester_id, old=old, new=body)
    db.commit()
    return class_brief(c)


# ================================================================= 管理端：体测锁定、日志、导出、账号

@app.post("/api/admin/semesters/{sid}/pe-lock")
def set_pe_lock(sid: int, request: Request, locked: bool = Body(..., embed=True), u: User = Depends(admin_user),
                db: Session = Depends(get_db)):
    """一键锁定／解锁本学期全部班级的体测成绩。锁定只管两项体测的成绩和状态。"""
    sem = _sem(db, sid)
    if bool(sem.pe_locked) == locked:
        return sem_dict(sem)
    sem.pe_locked = locked
    sem.pe_locked_by, sem.pe_locked_at = u.staff_no, now()
    services.log(db, u.staff_no, "锁定体测" if locked else "解锁体测", sem.name, sem.id, ip=client_ip(request))
    db.commit()
    return sem_dict(sem)

@app.get("/api/admin/logs")
def list_logs(semester_id: str = "", action: str = "", actor: str = "", page: int = 1,
              u: User = Depends(admin_user), db: Session = Depends(get_db)):
    # semester_id 留空 = 全部学期（还没建学期时前端可能传空串，不能 422）。
    # 指定学期时连同不属于任何学期的记录（登录、账号等）一起列出，否则这些记录永远看不到。
    semester_id = semester_id.strip()
    if semester_id and not semester_id.isdigit():
        raise HTTPException(400, "学期参数有误")
    q = select(AuditLog)
    if semester_id:
        q = q.where((AuditLog.semester_id == int(semester_id)) | AuditLog.semester_id.is_(None))
    if action:
        q = q.where(AuditLog.action == action)
    if actor:
        q = q.where(AuditLog.actor == actor)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    logs = db.scalars(q.order_by(AuditLog.id.desc()).offset((page - 1) * 100).limit(100)).all()
    names = {x.staff_no: x.name for x in db.scalars(select(User))}
    return {"total": total, "logs": [{"at": l.at.strftime("%Y-%m-%d %H:%M:%S"), "actor": l.actor,
                                      "actor_name": names.get(l.actor, ""), "action": l.action, "target": l.target,
                                      "detail": l.detail, "ip": l.ip} for l in logs]}


@app.get("/api/admin/semesters/{sid}/export")
def export_semester(sid: int, request: Request, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    sem = _sem(db, sid)
    classes = sorted(db.scalars(select(TeachingClass).where(TeachingClass.semester_id == sid)),
                     key=services.class_sort_key)
    sections = [(c, services.load_class_rows(db, c, sem), services.class_config(c, sem)) for c in classes]
    data = exporter.build_workbook(sem, sections, f"{u.name}({u.staff_no})")
    services.log(db, u.staff_no, "导出", f"{sem.name} 全部教学班", sid, ip=client_ip(request), classes=len(classes))
    db.commit()
    return _xlsx(data, f"{sem.name}-全部成绩.xlsx")


@app.get("/api/admin/semesters/{sid}/export-pe")
def export_semester_pe(sid: int, request: Request, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    """全校体测成绩表：教学班号、学号、姓名、性别、本学期两项体测成绩，按学号排序。"""
    sem = _sem(db, sid)
    classes = db.scalars(select(TeachingClass).where(TeachingClass.semester_id == sid)).all()
    sections = [(c, services.load_class_rows(db, c, sem), services.class_config(c, sem)) for c in classes]
    data = exporter.build_pe_workbook(sem, sections)
    services.log(db, u.staff_no, "导出", f"{sem.name} 全校体测成绩表", sid, ip=client_ip(request), classes=len(classes))
    db.commit()
    return _xlsx(data, f"{sem.name}-全校体测成绩表.xlsx")


@app.get("/api/admin/semesters/{sid}/overview")
def semester_overview(sid: int, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    sem = _sem(db, sid)
    st = dict(db.execute(select(TeachingClass.status, func.count()).where(TeachingClass.semester_id == sid)
                         .group_by(TeachingClass.status)).all())
    cls = sorted(db.scalars(select(TeachingClass).where(TeachingClass.semester_id == sid)),
                 key=services.class_sort_key)
    # 各班体测填了 0 的人数（状态为正常、成绩等于 0）：多半是没来测被填了 0
    zeros = dict(db.execute(select(Enrollment.class_id, func.count()).join(
        ScoreRecord, ScoreRecord.enrollment_id == Enrollment.id).where(
        Enrollment.semester_id == sid, Enrollment.active.is_(True),
        ((ScoreRecord.a_raw == 0) & (ScoreRecord.a_status == "正常")) |
        ((ScoreRecord.b_raw == 0) & (ScoreRecord.b_status == "正常"))).group_by(Enrollment.class_id)).all())
    locker = db.get(User, sem.pe_locked_by) if sem.pe_locked_by else None
    return {"semester": sem_dict(sem), "classes": sum(st.values()), "status": st,
            "pe_submitted": st.get("已提交", 0), "pe_locked_by_name": locker.name if locker else "",
            "class_list": [{"id": c.id, "class_no": c.class_no, "course_name": c.course_name, "class_name": c.class_name,
                            "teacher_name": c.teacher_name, "staff_no": c.staff_no, "zeros": zeros.get(c.id, 0),
                            "submitted": c.status == "已提交",
                            "submitted_at": c.submitted_at.strftime("%Y-%m-%d %H:%M") if c.submitted_at and c.status == "已提交" else None}
                           for c in cls],
            "teacher_edits": db.scalar(select(func.count()).select_from(AuditLog).where(
                AuditLog.semester_id == sid, AuditLog.action == "教师修改学生信息")),
            "enrollments": db.scalar(select(func.count()).select_from(Enrollment).where(
                Enrollment.semester_id == sid, Enrollment.active.is_(True))),
            "pending": db.scalar(select(func.count()).select_from(StandardSnapshot).where(
                StandardSnapshot.semester_id == sid, StandardSnapshot.pending.is_(True))),
            "info_errors": db.scalar(select(func.count()).select_from(Enrollment).where(
                Enrollment.semester_id == sid, Enrollment.active.is_(True), Enrollment.info_error.is_not(None),
                Enrollment.info_error_resolved.is_not(True))),
            "teacher_pending": db.scalar(select(func.count()).select_from(TeachingClass).where(
                TeachingClass.semester_id == sid, TeachingClass.teacher_pending.is_(True)))}


@app.get("/api/admin/users")
def list_users(u: User = Depends(admin_user), db: Session = Depends(get_db)):
    return [{"staff_no": x.staff_no, "name": x.name, "is_admin": is_admin(x), "is_public": bool(x.is_public),
             "must_change_password": bool(x.must_change_password) if x.is_public else False}
            for x in db.scalars(select(User).order_by(User.staff_no))]


@app.post("/api/admin/public-accounts")
def create_public_account(request: Request, name: str = Body(...), staff_no: str = Body(...),
                          u: User = Depends(admin_user), db: Session = Depends(get_db)):
    """新建体育部公共账号：生成初始口令（只显示这一次），首次登录须修改。可以建多个。"""
    name, staff_no = name.strip()[:64], staff_no.strip()
    if not name:
        raise HTTPException(400, "请填写账号名称")
    if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", staff_no):
        raise HTTPException(400, "工号只能用字母、数字、下划线，3–32 位")
    if staff_no in config.ADMIN_STAFF_NOS or db.get(User, staff_no):
        raise HTTPException(409, f"工号 {staff_no} 已被使用，请换一个")
    pw = auth.gen_password(12)
    db.add(User(staff_no=staff_no, name=name, is_public=True, is_admin=False,
                password_hash=auth.hash_password(pw), must_change_password=True))
    services.log(db, u.staff_no, "公共账号", staff_no, ip=client_ip(request), op="新建", name=name)
    db.commit()
    return {"ok": True, "staff_no": staff_no, "name": name, "password": pw}


@app.post("/api/admin/public-accounts/{staff_no}/reset")
def reset_public_password(staff_no: str, request: Request, u: User = Depends(admin_user),
                          db: Session = Depends(get_db)):
    x = db.get(User, staff_no)
    if not x or not x.is_public:
        raise HTTPException(404, "公共账号不存在")
    pw = auth.gen_password(12)
    x.password_hash, x.must_change_password = auth.hash_password(pw), True
    services.log(db, u.staff_no, "公共账号", staff_no, ip=client_ip(request), op="重置口令")
    db.commit()
    return {"ok": True, "staff_no": staff_no, "password": pw}


@app.post("/api/admin/users/{staff_no}")
def set_admin(staff_no: str, is_admin_flag: bool = Body(..., embed=True, alias="is_admin"),
              u: User = Depends(admin_user), db: Session = Depends(get_db)):
    """设置或取消管理员。账号页“添加学生管理员”也走这里：输入10位学号即建账号并设为管理员，
    管理员登录不受统一认证人员类别（只放行教职工）的限制。8位工号必须是平台里已有的账号。"""
    staff_no = staff_no.strip()
    existing = db.get(User, staff_no)
    if existing and existing.is_public and is_admin_flag:
        raise HTTPException(400, "公共账号不能设为管理员")
    if not existing:
        if not is_admin_flag:
            raise HTTPException(404, "账号不存在")
        if re.fullmatch(r"\d{8}", staff_no):
            raise HTTPException(400, f"平台里没有工号为 {staff_no} 的教师账号，不能设为管理员")
        if not re.fullmatch(r"\d{10}", staff_no):
            raise HTTPException(400, "请输入10位学号，或平台里已有的8位教师工号")
    x = auth.ensure_user(db, staff_no)
    if x.staff_no == u.staff_no and not is_admin_flag:
        raise HTTPException(400, "不能取消自己的管理员身份")
    x.is_admin = is_admin_flag
    services.log(db, u.staff_no, "账号", staff_no, is_admin=is_admin_flag)
    db.commit()
    return {"ok": True}


@app.exception_handler(HTTPException)
async def http_exc(request: Request, exc: HTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
