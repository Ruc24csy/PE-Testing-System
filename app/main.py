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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import auth, config, exporter, grading, importer, scoring, services
from .auth import admin_user, client_ip, current_user, get_db, is_admin
from .models import (AuditLog, Enrollment, GradeCorrection, ScoreHistory, ScoreRecord, Semester, StandardSnapshot,
                     Student, TeachingClass, UnlockRequest, User, init_db, now)

STATIC = Path(__file__).parent / "static"
WEEKDAYS = {"星期一": 1, "星期二": 2, "星期三": 3, "星期四": 4, "星期五": 5, "星期六": 6, "星期日": 7, "星期天": 7}

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
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if config.HTTPS_ONLY:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    path = request.url.path
    if path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    elif path == "/" or path.startswith("/static/"):
        # 每次都向服务器核对版本（有 ETag，没变时只回 304），避免浏览器拿缓存里的旧脚本
        resp.headers["Cache-Control"] = "no-cache"
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
    return HTMLResponse(_index_html())


def _index_html() -> str:
    """首页里的脚本和样式地址带上文件版本号，更新后浏览器一定会取新文件。"""
    html = (STATIC / "index.html").read_text("utf-8")
    for name in ("app.js", "style.css"):
        v = int((STATIC / name).stat().st_mtime)
        html = html.replace(f"/static/{name}", f"/static/{name}?v={v}")
    return html


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
    if not (u and is_admin(u)) and ident["staff_no"] not in config.ADMIN_STAFF_NOS \
            and ident["category"] not in config.SSO_ALLOWED_CATEGORIES:
        auth.record_attempt(db, ident["staff_no"], ip, False)
        return RedirectResponse("/#/login?error=category")
    u = auth.ensure_user(db, ident["staff_no"], ident["name"])
    auth.record_attempt(db, u.staff_no, ip, True)
    request.session.clear()
    request.session["staff_no"] = u.staff_no
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
    """应急管理员本地口令登录（统一认证不可用时）。连续失败5次锁定15分钟。"""
    ip = client_ip(request)
    if auth.locked(db, staff_no):
        raise HTTPException(429, f"连续登录失败次数过多，账号已锁定{config.LOGIN_LOCK_MINUTES}分钟")
    u = db.get(User, staff_no)
    if u and config.AUTH_MODE == "sso" and not is_admin(u):
        u = None  # 接入统一认证后，本地口令只留给应急管理员
    if not u or not auth.verify_password(password, u.password_hash):
        auth.record_attempt(db, staff_no, ip, False)
        raise HTTPException(401, "工号或口令错误")
    auth.record_attempt(db, staff_no, ip, True)
    request.session.clear()
    request.session["staff_no"] = u.staff_no
    services.log(db, u.staff_no, "登录", ip=ip, method="local")
    db.commit()
    return {"ok": True, "must_change_password": u.must_change_password}


@app.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True, "redirect": config.SSO_LOGOUT_URL if config.AUTH_MODE == "sso" else "/"}


@app.get("/api/me")
def me(u: User = Depends(current_user)):
    return {"staff_no": u.staff_no, "name": u.name, "is_admin": is_admin(u),
            "must_change_password": u.must_change_password, "auth_mode": config.AUTH_MODE}


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
            "entry_open": s.entry_open, "config": s.config, "standard_version": s.standard_version}


@app.get("/api/semesters")
def list_semesters(u: User = Depends(current_user), db: Session = Depends(get_db)):
    sems = db.scalars(select(Semester).order_by(Semester.code.desc())).all()
    sems.sort(key=lambda s: (not s.is_current, ))
    return [sem_dict(s) for s in sems]


@app.post("/api/admin/semesters")
def create_semester(label: str = Body(..., embed=True), u: User = Depends(admin_user), db: Session = Depends(get_db)):
    p = importer.parse_semester(label)
    if not p:
        raise HTTPException(400, "学期写法应为“2026-2027学年秋季学期”或“2026-2027-1”")
    code, name, y, season = p
    if db.scalar(select(Semester).where(Semester.code == code)):
        raise HTTPException(400, f"{name} 已存在")
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
            "pending": (counts or {}).get("pending")}


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

    def key(c):
        p = re.findall(r"\d{2}", c.periods or "")
        return (WEEKDAYS.get(c.weekday, 9), int(p[0]) if p else 99, c.course_name, c.class_name)

    classes.sort(key=key)
    all_courses = sorted(set(db.scalars(select(TeachingClass.course_name).where(TeachingClass.semester_id == sid))))
    has_any = db.scalar(select(func.count()).select_from(TeachingClass).where(TeachingClass.semester_id == sid)) > 0
    return {"semester": sem_dict(sem), "imported": has_any, "courses": all_courses,
            "classes": [class_brief(c, {"roster": roster.get(c.id, 0), "entered": entered.get(c.id, 0),
                                        "pending": pend.get(c.id, 0)}) for c in classes]}


@app.get("/api/classes/{cid}")
def get_class(cid: int, u: User = Depends(current_user), db: Session = Depends(get_db)):
    c, sem = _class(db, cid, u)
    rows = services.load_class_rows(db, c, sem)
    cfg = services.class_config(c, sem)
    editable, why = services.can_edit(c, sem)
    unlock = db.scalars(select(UnlockRequest).where(UnlockRequest.class_id == c.id)
                        .order_by(UnlockRequest.id.desc())).first()
    return {"semester": sem_dict(sem), "class": class_brief(c), "config": cfg,
            "config_labels": {s["key"]: {g: scoring.item_label(it) for g, it in s["items"].items()} for s in cfg["slots"]},
            "editable": editable, "readonly_reason": why, "rows": rows,
            "statuses": scoring.STATUSES, "standard_version": sem.standard_version,
            "unlock_request": None if not unlock else {"status": unlock.status, "reason": unlock.reason,
                                                       "review_note": unlock.review_note}}


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
    return {"results": res["results"], "rows": [rows[i] for i in res["touched"] if i in rows],
            "status": c.status}


@app.put("/api/classes/{cid}/config")
def update_class_config(cid: int, request: Request, cfg: dict = Body(..., embed=True),
                        u: User = Depends(current_user), db: Session = Depends(get_db)):
    c, sem = _class(db, cid, u)
    ok, why = services.can_edit(c, sem)
    if not ok:
        raise HTTPException(403, why)
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
    c, sem = _class(db, cid, u)
    e = db.get(Enrollment, enrollment_id)
    if not e or e.class_id != c.id:
        raise HTTPException(404, "学生不在本班")
    e.info_error = note.strip()[:256] or None
    services.log(db, u.staff_no, "标记信息有误", f"{e.student_no} {e.name}", sem.id, note=note)
    db.commit()
    return {"ok": True}


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
    chk = services.submit_check(services.load_class_rows(db, c, sem), services.class_config(c, sem))
    if chk["blocking"]:
        raise HTTPException(400, f"本班还有 {len(chk['pending'])} 名学生的年级或性别待体育部确认，确认后才能提交")
    c.status, c.submitted_at, c.submitted_by = "已提交", now(), u.staff_no
    services.log(db, u.staff_no, "提交", f"{c.class_no} {c.class_name}", sem.id, ip=client_ip(request),
                 missing=len(chk["missing"]), sudden=len(chk["sudden"]))
    db.commit()
    return {"ok": True}


@app.post("/api/classes/{cid}/unlock-request")
def unlock_request(cid: int, reason: str = Body(..., embed=True), u: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    c, sem = _class(db, cid, u)
    if c.status != "已提交":
        raise HTTPException(400, "本班尚未提交，无需撤回")
    if db.scalar(select(UnlockRequest).where(UnlockRequest.class_id == c.id, UnlockRequest.status == "待审核")):
        raise HTTPException(400, "已有待审核的撤回申请")
    if not reason.strip():
        raise HTTPException(400, "请填写撤回原因")
    db.add(UnlockRequest(class_id=c.id, reason=reason.strip()[:1000], requester=u.staff_no))
    services.log(db, u.staff_no, "撤回申请", f"{c.class_no} {c.class_name}", sem.id, reason=reason)
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
    return {"history": [{"field": h.field, "old": h.old, "new": h.new, "computed": h.computed,
                         "version": h.standard_version, "operator": f"{names.get(h.operator, '')}({h.operator})",
                         "at": h.at.strftime("%Y-%m-%d %H:%M:%S"), "batch": h.batch} for h in hs],
            "standard": _snap_dict(snap)}


def _snap_dict(snap: StandardSnapshot | None):
    if not snap:
        return None
    return {"student_no": snap.student_no, "grade_num": snap.grade_num, "band": snap.band,
            "band_label": services.band_label(snap.band), "source": snap.source, "detail": snap.detail,
            "pending": snap.pending, "reasons": snap.pending_reasons, "decided_by": snap.decided_by,
            "decided_at": snap.decided_at.strftime("%Y-%m-%d %H:%M")}


@app.get("/api/classes/{cid}/export")
def export_class(cid: int, request: Request, u: User = Depends(current_user), db: Session = Depends(get_db)):
    c, sem = _class(db, cid, u)
    rows = services.load_class_rows(db, c, sem)
    data = exporter.build_workbook(sem, [(c, rows, services.class_config(c, sem))], f"{u.name}({u.staff_no})")
    services.log(db, u.staff_no, "导出", f"{c.class_no} {c.class_name}", sem.id, ip=client_ip(request))
    db.commit()
    return _xlsx(data, f"{sem.name}-{c.class_name}-成绩.xlsx")


@app.get("/api/classes/{cid}/template")
def class_template(cid: int, u: User = Depends(current_user), db: Session = Depends(get_db)):
    c, sem = _class(db, cid, u)
    rows = services.load_class_rows(db, c, sem)
    return _xlsx(exporter.build_template(sem, c, rows, services.class_config(c, sem)),
                 f"{c.class_name}-成绩录入模板.xlsx")


def _xlsx(data: bytes, filename: str) -> Response:
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


# ---------------------------------------------------------------- 教师整班上传（备用路径）

TEMPLATE_COLS = {"student_no": ["学号"], "a_raw": ["原始成绩"], "a_status": ["状态"],
                 "skill": ["运动技能评定", "运动技能"], "regular": ["平时成绩", "平时"]}


@app.post("/api/classes/{cid}/upload")
async def upload_scores(cid: int, file: UploadFile = File(...), u: User = Depends(current_user),
                        db: Session = Depends(get_db)):
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
    la, lb = cfg["slots"][0].get("label", "项目一"), cfg["slots"][1].get("label", "项目二")
    colmap = {}
    for i, h in enumerate(head):
        if h == "学号":
            colmap["student_no"] = i
        elif h == f"{la}原始成绩":
            colmap["a_raw"] = i
        elif h == f"{la}状态":
            colmap["a_status"] = i
        elif h == f"{lb}原始成绩":
            colmap["b_raw"] = i
        elif h == f"{lb}状态":
            colmap["b_status"] = i
        elif h.startswith("运动技能"):
            colmap["skill"] = i
        elif h.startswith("平时"):
            colmap["regular"] = i
    if "student_no" not in colmap:
        raise HTTPException(400, "找不到“学号”列，请使用平台下载的模板")
    roster = {r["student_no"]: r for r in services.load_class_rows(db, c, sem)}
    changes, errors, unknown = [], [], []
    for n, r in enumerate(rows[1:], start=2):
        sno = importer.clean(r[colmap["student_no"]]) if colmap["student_no"] < len(r) else ""
        if not sno:
            continue
        row = roster.get(sno)
        if not row:
            unknown.append(f"第{n}行 {sno}")
            continue
        for f, i in colmap.items():
            if f == "student_no" or i >= len(r):
                continue
            v = r[i]
            if v is None or importer.clean(v) == "":
                continue
            v = importer.clean(v) if not isinstance(v, (int, float)) else v
            try:
                services.parse_cell(f, v, cfg, row["gender_code"])
            except scoring.ParseError as e:
                errors.append({"row": n, "student_no": sno, "name": row["name"], "field": f, "value": str(v),
                               "error": str(e)})
                continue
            changes.append({"enrollment_id": row["enrollment_id"], "field": f, "value": v,
                            "student_no": sno, "name": row["name"]})
    token = uuid.uuid4().hex
    (importer.UPLOAD_DIR / f"scores-{token}.json").write_text(
        __import__("json").dumps({"class_id": c.id, "changes": changes}, ensure_ascii=False))
    touched = len({ch["enrollment_id"] for ch in changes})
    return {"token": token, "cells": len(changes), "students": touched, "errors": errors, "unknown": unknown,
            "roster": len(roster)}


@app.post("/api/classes/{cid}/upload/commit")
def upload_commit(cid: int, request: Request, token: str = Body(..., embed=True), u: User = Depends(current_user),
                  db: Session = Depends(get_db)):
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
    batch = f"imp-{token[:12]}"
    res = services.save_cells(db, c, sem, payload["changes"], u.staff_no, batch=batch)
    services.log(db, u.staff_no, "导入", f"{c.class_no} {c.class_name}", sem.id, ip=client_ip(request),
                 batch=batch, cells=len(payload["changes"]))
    db.commit()
    p.unlink(missing_ok=True)
    return {"ok": True, "batch": batch, "saved": len([r for r in res["results"] if r.get("ok")])}


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
    result = importer.commit(db, kind, rows, sem, mode, u.staff_no)
    importer.remember_mapping(db, kind, mapping)
    services.log(db, u.staff_no, "导入", importer.KIND_NAMES[kind], sem.id if sem else None, ip=client_ip(request),
                 mode=importer.MODES[mode], rows=len(rows), result=result,
                 warnings=[i["message"] for i in rep.items if i["level"] == "warn"][:50])
    db.commit()
    return {"ok": True, "result": result}


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
    teacher_pending = [class_brief(c) for c in db.scalars(select(TeachingClass).where(
        TeachingClass.semester_id == sid, TeachingClass.teacher_pending.is_(True)))]
    info_errors = [{"student_no": e.student_no, "name": e.name, "class_name": c.class_name,
                    "teacher": c.teacher_name, "note": e.info_error}
                   for e, c in db.execute(select(Enrollment, TeachingClass).join(
                       TeachingClass, TeachingClass.id == Enrollment.class_id).where(
                       Enrollment.semester_id == sid, Enrollment.info_error.is_not(None)))]
    return {"semester": sem_dict(sem), "students": out, "summary": reasons, "teacher_pending": teacher_pending,
            "info_errors": info_errors}


@app.get("/api/admin/semesters/{sid}/students/{no}")
def student_standard(sid: int, no: str, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    snap = db.scalar(select(StandardSnapshot).where(StandardSnapshot.semester_id == sid,
                                                    StandardSnapshot.student_no == no))
    corrs = db.scalars(select(GradeCorrection).where(GradeCorrection.student_no == no)
                       .order_by(GradeCorrection.id.desc())).all()
    st = db.get(Student, no)
    return {"standard": _snap_dict(snap),
            "student": None if not st else {"name": st.name, "gender": st.gender, "college": st.college,
                                            "grade": st.grade, "class_name": st.class_name},
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
        c.staff_no = str(body["staff_no"]).strip()
        c.teacher_name = str(body.get("teacher_name") or c.teacher_name).strip()
        c.teacher_pending = not c.staff_no
        if c.staff_no:
            auth.ensure_user(db, c.staff_no, c.teacher_name)
    services.log(db, u.staff_no, "指定教师", f"{c.class_no} {c.class_name}", c.semester_id, old=old, new=body)
    db.commit()
    return class_brief(c)


# ================================================================= 管理端：撤回审核、日志、导出、账号

@app.get("/api/admin/unlock-requests")
def list_unlock(u: User = Depends(admin_user), db: Session = Depends(get_db)):
    rs = db.execute(select(UnlockRequest, TeachingClass).join(TeachingClass, TeachingClass.id == UnlockRequest.class_id)
                    .order_by(UnlockRequest.id.desc()).limit(200)).all()
    return [{"id": r.id, "class_id": c.id, "class_name": c.class_name, "teacher": c.teacher_name,
             "reason": r.reason, "status": r.status, "requester": r.requester, "reviewer": r.reviewer,
             "review_note": r.review_note, "at": r.created_at.strftime("%Y-%m-%d %H:%M")} for r, c in rs]


@app.post("/api/admin/unlock-requests/{rid}")
def review_unlock(rid: int, approve: bool = Body(...), note: str = Body(""), u: User = Depends(admin_user),
                  db: Session = Depends(get_db)):
    r = db.get(UnlockRequest, rid)
    if not r or r.status != "待审核":
        raise HTTPException(404, "申请不存在或已处理")
    r.status = "已同意" if approve else "已驳回"
    r.reviewer, r.review_note, r.reviewed_at = u.staff_no, note, now()
    c = db.get(TeachingClass, r.class_id)
    if approve:
        c.status = "录入中"
    services.log(db, u.staff_no, "撤回审核", f"{c.class_no} {c.class_name}", c.semester_id, approve=approve, note=note)
    db.commit()
    return {"ok": True}


@app.get("/api/admin/logs")
def list_logs(semester_id: int | None = None, action: str = "", actor: str = "", page: int = 1,
              u: User = Depends(admin_user), db: Session = Depends(get_db)):
    q = select(AuditLog)
    if semester_id:
        q = q.where(AuditLog.semester_id == semester_id)
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
    classes = db.scalars(select(TeachingClass).where(TeachingClass.semester_id == sid)
                         .order_by(TeachingClass.course_name, TeachingClass.class_no)).all()
    sections = [(c, services.load_class_rows(db, c, sem), services.class_config(c, sem)) for c in classes]
    data = exporter.build_workbook(sem, sections, f"{u.name}({u.staff_no})")
    services.log(db, u.staff_no, "导出", f"{sem.name} 全部教学班", sid, ip=client_ip(request), classes=len(classes))
    db.commit()
    return _xlsx(data, f"{sem.name}-全部成绩.xlsx")


@app.get("/api/admin/semesters/{sid}/overview")
def semester_overview(sid: int, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    sem = _sem(db, sid)
    st = dict(db.execute(select(TeachingClass.status, func.count()).where(TeachingClass.semester_id == sid)
                         .group_by(TeachingClass.status)).all())
    return {"semester": sem_dict(sem), "classes": sum(st.values()), "status": st,
            "enrollments": db.scalar(select(func.count()).select_from(Enrollment).where(
                Enrollment.semester_id == sid, Enrollment.active.is_(True))),
            "pending": db.scalar(select(func.count()).select_from(StandardSnapshot).where(
                StandardSnapshot.semester_id == sid, StandardSnapshot.pending.is_(True))),
            "unlock_waiting": db.scalar(select(func.count()).select_from(UnlockRequest).join(
                TeachingClass, TeachingClass.id == UnlockRequest.class_id).where(
                TeachingClass.semester_id == sid, UnlockRequest.status == "待审核")),
            "teacher_pending": db.scalar(select(func.count()).select_from(TeachingClass).where(
                TeachingClass.semester_id == sid, TeachingClass.teacher_pending.is_(True)))}


@app.get("/api/admin/users")
def list_users(u: User = Depends(admin_user), db: Session = Depends(get_db)):
    return [{"staff_no": x.staff_no, "name": x.name, "is_admin": is_admin(x), "has_password": bool(x.password_hash),
             "must_change_password": x.must_change_password}
            for x in db.scalars(select(User).order_by(User.staff_no))]


@app.post("/api/admin/users/passwords")
def issue_passwords(request: Request, body: dict = Body(...), u: User = Depends(admin_user),
                    db: Session = Depends(get_db)):
    """生成初始口令并导出为表格。给 staff_nos 时重置这些账号；否则为本学期全部任课教师中还没有口令的生成。"""
    from openpyxl import Workbook
    targets: list[User] = []
    if body.get("staff_nos"):
        for no in body["staff_nos"]:
            no = str(no).strip()
            if no:
                targets.append(auth.ensure_user(db, no))
    else:
        sem = _sem(db, int(body["semester_id"]))
        rows = db.execute(select(TeachingClass.staff_no, TeachingClass.teacher_name).where(
            TeachingClass.semester_id == sem.id, TeachingClass.teacher_pending.is_(False))).all()
        for no, name in sorted(set(rows)):
            x = auth.ensure_user(db, no, name)
            if not x.password_hash:
                targets.append(x)
    if not targets:
        raise HTTPException(400, "没有需要生成口令的账号（已有口令的账号请单独重置）")
    wb = Workbook()
    ws = wb.active
    ws.title = "初始口令"
    ws.append(["工号", "姓名", "初始口令", "说明"])
    for x in targets:
        pw = auth.gen_password()
        x.password_hash, x.must_change_password = auth.hash_password(pw), True
        ws.append([x.staff_no, x.name, pw, "首次登录须修改口令"])
    for col, w in zip("ABCD", (14, 12, 16, 20)):
        ws.column_dimensions[col].width = w
    services.log(db, u.staff_no, "生成口令", f"{len(targets)} 个账号", ip=client_ip(request),
                 staff_nos=[x.staff_no for x in targets])
    db.commit()
    buf = io.BytesIO()
    wb.save(buf)
    return _xlsx(buf.getvalue(), f"教师初始口令-{now():%Y%m%d%H%M}.xlsx")


@app.post("/api/admin/users/{staff_no}")
def set_admin(staff_no: str, is_admin_flag: bool = Body(..., embed=True, alias="is_admin"),
              u: User = Depends(admin_user), db: Session = Depends(get_db)):
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
