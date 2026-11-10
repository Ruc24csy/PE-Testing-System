"""每学期课程信息与名单导入（需求文档三（三））。

流程：上传文件 → 识别工作表与表头行 → 字段映射（记住上次映射）→ 预检报告 → 确认导入。
"""
from __future__ import annotations

import re
import uuid
from collections import Counter, defaultdict
from pathlib import Path

import openpyxl
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import DATA_DIR
from .grading import rebuild_snapshots
from .models import (Enrollment, ScoreRecord, Semester, Setting, StandardSnapshot, Student, TeachingClass,
                     User, now)

UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 字段：(代码, 中文名, 是否必填, 可识别的表头写法)
FIELDS = {
    "schedule": [
        ("semester", "学年学期", False, ["学年学期", "学期"]),
        ("class_no", "教学班号", True, ["教学班号", "教学班编号", "课堂号"]),
        ("course_name", "课程名称", True, ["课程名称", "课程名"]),
        ("course_code", "课程编号", False, ["课程编号", "课程号", "课程代码"]),
        ("class_name", "教学班名称", False, ["教学班名称", "教学班"]),
        ("teacher_name", "授课教师", False, ["授课教师", "任课教师", "教师", "上课教师"]),
        ("staff_no", "教工号", False, ["教工号", "职工号", "工号", "教师工号"]),
        ("location", "上课地点", False, ["上课地点", "地点", "教室"]),
        ("weekday", "星期", False, ["星期", "上课星期"]),
        ("periods", "节次", False, ["节次", "上课节次"]),
        ("weeks", "周次", False, ["周次", "上课周次"]),
        ("enrolled_count", "选课人数", False, ["选课人数", "人数"]),
        ("audience", "授课对象", False, ["授课对象"]),
        ("course_nature", "课程性质", False, ["课程性质", "课程类别"]),
        ("dept", "开课单位", False, ["开课单位", "开课院系"]),
    ],
    "enroll": [
        ("semester", "学年学期", False, ["学年学期", "学期"]),
        ("student_no", "学号", True, ["学号"]),
        ("name", "姓名", False, ["姓名", "学生姓名"]),
        ("class_label", "上课班级", False, ["上课班级", "班级", "行政班"]),
        ("major", "上课专业", False, ["上课专业", "专业"]),
        ("grade_field", "上课年级", False, ["上课年级", "年级"]),
        ("college", "上课院系", False, ["上课院系", "院系", "学院"]),
        ("class_no", "教学班号", True, ["教学班号", "教学班编号"]),
        ("class_name", "教学班名称", False, ["教学班名称"]),
        ("teacher_name", "上课教师", False, ["上课教师", "授课教师"]),
        ("course_name", "课程名称", False, ["课程名称"]),
        ("course_category", "课程类别", False, ["课程类别", "课程性质"]),
        ("campus", "上课校区", False, ["上课校区", "校区"]),
    ],
    "base": [
        ("student_no", "学号", True, ["学号"]),
        ("name", "姓名", False, ["姓名"]),
        ("gender", "性别", True, ["性别"]),
        ("college", "院系所名称", False, ["院系所名称", "院系", "学院"]),
        ("class_name", "班级", False, ["班级"]),
        ("grade", "年级", False, ["年级"]),
        ("school", "学校", False, ["学校"]),
        ("major", "专业", False, ["专业"]),
    ],
}
KIND_NAMES = {"schedule": "课程安排表", "enroll": "学生选课表", "base": "学生基础数据表"}
MODES = {"overwrite": "覆盖", "new": "新增", "rebuild": "重建", "append": "追加"}

_FW = str.maketrans({chr(0xFF01 + i): chr(0x21 + i) for i in range(94)} | {"　": " "})


def clean(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).translate(_FW)
    return re.sub(r"\s+", " ", s).strip()


def clean_int(v) -> int:
    s = re.sub(r"[^\d]", "", clean(v))
    return int(s) if s else 0


def _norm_header(h) -> str:
    return re.sub(r"[\s*＊:：]", "", clean(h))


def parse_semester(text: str) -> tuple[str, str, int, str] | None:
    """把“2026-2027学年秋季学期”或“2026-2027-1”统一为 (code, name, start_year, season)。"""
    s = clean(text).replace("—", "-").replace("–", "-").replace("－", "-")
    m = re.search(r"(\d{4})-(\d{4})-([12])", s)
    if m:
        y, term = int(m.group(1)), m.group(3)
    else:
        m = re.search(r"(\d{4})-(\d{4})学年(秋|春)", s)
        if not m:
            return None
        y, term = int(m.group(1)), "1" if m.group(3) == "秋" else "2"
    season = "fall" if term == "1" else "spring"
    name = f"{y}-{y + 1}学年{'秋季' if term == '1' else '春季'}学期"
    return f"{y}-{y + 1}-{term}", name, y, season


# ---------------------------------------------------------------- 读取文件

def save_upload(data: bytes, filename: str) -> str:
    token = uuid.uuid4().hex
    suffix = Path(filename).suffix.lower()
    if suffix not in (".xlsx", ".xlsm"):
        raise ValueError("请上传 .xlsx 格式的 Excel 文件（老格式 .xls 请先另存为 .xlsx）")
    (UPLOAD_DIR / f"{token}.xlsx").write_bytes(data)
    return token


def _path(token: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError("无效的文件标识")
    p = UPLOAD_DIR / f"{token}.xlsx"
    if not p.exists():
        raise ValueError("上传的文件已过期，请重新上传")
    return p


def detect_header(rows: list[tuple], kind: str) -> int:
    """在前 20 行里找表头：命中已知字段名最多的那一行。"""
    names = {_norm_header(a) for f in FIELDS[kind] for a in f[3]}
    best, best_hits = 0, -1
    for i, r in enumerate(rows[:20]):
        hits = sum(1 for c in r if _norm_header(c) in names)
        if hits > best_hits:
            best, best_hits = i, hits
    return best


def suggest_mapping(headers: list[str], kind: str, remembered: dict | None) -> dict:
    norm = [_norm_header(h) for h in headers]
    mapping = {}
    for code, _, _, aliases in FIELDS[kind]:
        if remembered and remembered.get(code) in headers:
            mapping[code] = remembered[code]
            continue
        for a in aliases:
            a = _norm_header(a)
            if a in norm:
                mapping[code] = headers[norm.index(a)]
                break
    return mapping


def inspect_file(db: Session, token: str, kind: str) -> dict:
    wb = openpyxl.load_workbook(_path(token), read_only=True, data_only=True)
    remembered = (db.get(Setting, f"mapping:{kind}") or Setting(value={})).value
    sheets = []
    for ws in wb.worksheets:
        head_rows = []
        for i, r in enumerate(ws.iter_rows(values_only=True)):
            head_rows.append(r)
            if i >= 20:
                break
        if not head_rows:
            continue
        hi = detect_header(head_rows, kind)
        headers = [clean(c) for c in head_rows[hi]]
        mapping = suggest_mapping([h for h in headers if h], kind, remembered)
        sheets.append({"name": ws.title, "header_row": hi + 1, "headers": [h for h in headers if h],
                       "rows": max((ws.max_row or 0) - hi - 1, 0), "mapping": mapping,
                       "score": len(mapping)})
    wb.close()
    sheets.sort(key=lambda s: -s["score"])
    return {"token": token, "kind": kind, "sheets": sheets,
            "fields": [{"code": c, "label": l, "required": r} for c, l, r, _ in FIELDS[kind]]}


def read_rows(token: str, sheet: str, header_row: int, mapping: dict, kind: str) -> tuple[list[dict], int]:
    """返回 (数据行, 跳过的表尾行数)。必填字段全空的行视为“制表单位”“打印日期”之类的表尾，跳过。"""
    wb = openpyxl.load_workbook(_path(token), read_only=True, data_only=True)
    ws = wb[sheet]
    rows = ws.iter_rows(values_only=True)
    headers = None
    out = []
    footers = 0
    required = [c for c, _, req, _ in FIELDS[kind] if req]
    for i, r in enumerate(rows, start=1):
        if i < header_row:
            continue
        if i == header_row:
            headers = [clean(c) for c in r]
            idx = {code: headers.index(h) for code, h in mapping.items() if h in headers}
            continue
        if all(clean(c) == "" for c in r):
            continue
        rec = {"_row": i}
        for code, j in idx.items():
            rec[code] = clean(r[j]) if j < len(r) else ""
        if required and all(not rec.get(c) for c in required):
            footers += 1
            continue
        out.append(rec)
    wb.close()
    return out, footers


# ---------------------------------------------------------------- 预检

class Report:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, level: str, msg: str, rows: list[int] | None = None, advice: str = "", key: str = ""):
        self.items.append({"level": level, "message": msg, "rows": (rows or [])[:200],
                           "row_count": len(rows or []), "advice": advice, "key": key})

    @property
    def blocking(self) -> bool:
        return any(i["level"] == "error" for i in self.items)


def _check_required(kind, rows, mapping, rep: Report):
    for code, label, req, _ in FIELDS[kind]:
        if req and code not in mapping:
            rep.add("error", f"未映射必填字段“{label}”", advice="在字段映射里为它选一列")
    for code, label, req, _ in FIELDS[kind]:
        if req and code in mapping:
            bad = [r["_row"] for r in rows if not r.get(code)]
            if bad and not (kind == "base" and code == "student_no"):
                rep.add("error", f"{len(bad)} 行“{label}”为空", bad, "补全后重新上传，或删除这些行")


def _semester_check(rows, semester: Semester, rep: Report):
    vals = Counter(r.get("semester", "") for r in rows if r.get("semester"))
    wrong = [v for v in vals if (parse_semester(v) or ("",))[0] != semester.code]
    if wrong:
        bad = [r["_row"] for r in rows if r.get("semester") in wrong]
        rep.add("error", f"文件中的学年学期（{'、'.join(wrong[:3])}）与导入目标学期 {semester.name} 不一致",
                bad, "确认选对了学期或文件")


def precheck(db: Session, kind: str, rows: list[dict], mapping: dict, semester: Semester | None,
             mode: str) -> tuple[Report, dict]:
    rep = Report()
    _check_required(kind, rows, mapping, rep)
    plan: dict = {}
    if kind == "schedule":
        _semester_check(rows, semester, rep)
        cnt = Counter(r["class_no"] for r in rows if r.get("class_no"))
        dup = [r["_row"] for r in rows if cnt.get(r.get("class_no"), 0) > 1]
        if dup:
            rep.add("warn", f"教学班号重复 {sum(1 for v in cnt.values() if v > 1)} 个", dup, "只保留每个教学班号的第一行")
        pend = [r["_row"] for r in rows if r.get("class_no") and (
            not r.get("staff_no") or "体育部" in r.get("teacher_name", "") or not r.get("teacher_name"))]
        if pend:
            rep.add("warn", f"{len(pend)} 个教学班的授课教师为机构名称或缺少教工号，标记为“教师待补”", pend,
                    "导入后在管理端为这些教学班指定责任教师", key="teacher_pending")
        natures = Counter(r.get("course_nature", "") for r in rows)
        if len(natures) > 1:
            rep.add("info", "课程性质取值不统一：" + "、".join(f"{k or '空'} {v} 条" for k, v in natures.items()),
                    advice="按教学班号导入，不依赖课程性质的单一取值")
        noted = [r["_row"] for r in rows if re.search(r"[（(].+[)）]", r.get("class_name", ""))]
        if noted:
            rep.add("info", f"{len(noted)} 个教学班名称带括号备注，名称仅用于展示，一律以教学班号关联", noted)
        existing = set(db.scalars(select(TeachingClass.class_no).where(TeachingClass.semester_id == semester.id)))
        nos = list(dict.fromkeys(r["class_no"] for r in rows if r.get("class_no")))
        plan = {"total": len(nos), "new": len([n for n in nos if n not in existing]),
                "update": len([n for n in nos if n in existing]),
                "absent": len(existing - set(nos))}
        if mode == "new" and existing:
            rep.add("error", f"本学期已有 {len(existing)} 个教学班，“新增”只用于首次导入", advice="改用“覆盖”或“追加”")
        if mode == "append":
            plan["update"] = 0
    elif kind == "enroll":
        _semester_check(rows, semester, rep)
        classes = set(db.scalars(select(TeachingClass.class_no).where(TeachingClass.semester_id == semester.id)))
        if not classes:
            rep.add("error", "本学期还没有导入课程安排表", advice="先导入课程安排表，再导入学生选课表")
        missing = defaultdict(list)
        for r in rows:
            if r.get("class_no") and r["class_no"] not in classes:
                missing[(r["class_no"], r.get("class_name", ""))].append(r)
        if missing and classes:
            for (no, name), rs in missing.items():
                rep.add("info", f"教学班 {no} {name} 不在课程安排表中，按已关闭处理，不导入；其中的 {len(rs)} 条选课记录一并忽略",
                        [x["_row"] for x in rs], key="closed_class")
        pair = Counter((r.get("student_no"), r.get("class_no")) for r in rows)
        dup = [r["_row"] for r in rows if pair[(r.get("student_no"), r.get("class_no"))] > 1]
        if dup:
            rep.add("warn", "同一学号在同一教学班重复出现", dup, "只保留第一条")
        valid = [r for r in rows if r.get("class_no") in classes and r.get("student_no")]
        per = defaultdict(set)
        for r in valid:
            per[r["student_no"]].add(r["class_no"])
        multi = {k for k, v in per.items() if len(v) > 1}
        if multi:
            rep.add("info", f"{len(multi)} 名学生同时选了多门体育课，每门课各自录成绩",
                    [r["_row"] for r in valid if r["student_no"] in multi], key="multi")
        blank = [r["_row"] for r in valid if not r.get("grade_field") or not r.get("class_label") or not r.get("major")]
        if blank:
            rep.add("info", f"{len(blank)} 条记录上课年级、上课班级或专业为空，照常导入，上课年级为空的按学号推算年级", blank)
        badno = [r["_row"] for r in valid if not re.fullmatch(r"\d{10}", r["student_no"])]
        if badno:
            rep.add("warn", f"{len(badno)} 条记录学号不是10位，进入待确认清单", badno)
        cats = Counter(r.get("course_category", "") for r in valid)
        if len(cats) > 1:
            rep.add("info", "课程类别取值不统一：" + "、".join(f"{k or '空'} {v} 条" for k, v in cats.items()),
                    advice="以教学班号关联，不依赖课程类别")
        known = set()
        nos = list(per)
        for i in range(0, len(nos), 500):
            known |= set(db.scalars(select(Student.student_no).where(Student.student_no.in_(nos[i:i + 500]))))
        unknown = [n for n in nos if n not in known]
        if unknown:
            rep.add("warn", f"{len(unknown)} 名学生在学生基础数据中找不到，性别无法补全，进入待确认清单"
                    + ("（如果还没导入学生基础数据表，请先导入）" if not known else ""),
                    [r["_row"] for r in valid if r["student_no"] in set(unknown)])
        plan = {"rows": len(valid), "students": len(per), "classes": len({r["class_no"] for r in valid}),
                "skipped_closed": sum(len(v) for v in missing.values())}
    elif kind == "base":
        blank = [r["_row"] for r in rows if not r.get("student_no")]
        if blank:
            rep.add("warn", f"{len(blank)} 行学号为空，导入时剔除", blank)
        g = Counter(r.get("gender", "") for r in rows if r.get("student_no"))
        bad = [r["_row"] for r in rows if r.get("student_no") and r.get("gender") not in ("男", "女")]
        if bad:
            rep.add("warn", f"{len(bad)} 行性别为空或取值异常，这些学生进入待确认清单、不参与换算", bad)
        cnt = Counter(r.get("student_no") for r in rows if r.get("student_no"))
        dup = [r["_row"] for r in rows if cnt.get(r.get("student_no"), 0) > 1]
        if dup:
            rep.add("warn", "学号重复，保留最后一行", dup)
        plan = {"students": len(cnt), "male": g.get("男", 0), "female": g.get("女", 0)}
    return rep, plan


# ---------------------------------------------------------------- 写入

def _has_scores(db: Session, enrollment_ids: list[int]) -> set[int]:
    out = set()
    for i in range(0, len(enrollment_ids), 500):
        out |= set(db.scalars(select(ScoreRecord.enrollment_id).where(
            ScoreRecord.enrollment_id.in_(enrollment_ids[i:i + 500]))))
    return out


def commit(db: Session, kind: str, rows: list[dict], semester: Semester | None, mode: str,
           operator: str) -> dict:
    if kind == "base":
        seen = {}
        for r in rows:
            if r.get("student_no"):
                seen[r["student_no"]] = r
        existing = {s.student_no: s for s in db.scalars(select(Student))}
        n_new = 0
        for no, r in seen.items():
            s = existing.get(no)
            if s is None:
                s = Student(student_no=no)
                db.add(s)
                n_new += 1
            elif s.manual:
                continue  # 管理员手动改过的不覆盖
            s.name = r.get("name", "")
            s.gender = {"男": "M", "女": "F"}.get(r.get("gender", ""))
            s.college, s.class_name = r.get("college", ""), r.get("class_name", "")
            s.grade, s.major = r.get("grade", ""), r.get("major", "")
        db.flush()
        result = {"students": len(seen), "new": n_new}
        for sem in db.scalars(select(Semester)):
            rebuild_snapshots(db, sem, operator)
        return result

    if kind == "schedule":
        existing = {c.class_no: c for c in db.scalars(
            select(TeachingClass).where(TeachingClass.semester_id == semester.id))}
        seen = {}
        for r in rows:
            if r.get("class_no") and r["class_no"] not in seen:
                seen[r["class_no"]] = r
        created = updated = 0
        for no, r in seen.items():
            c = existing.get(no)
            if c is not None and mode == "append":
                continue
            if c is None:
                c = TeachingClass(semester_id=semester.id, class_no=no)
                db.add(c)
                created += 1
            else:
                updated += 1
            c.course_name, c.course_code = r.get("course_name", ""), r.get("course_code", "")
            c.class_name = r.get("class_name", "") or r.get("course_name", "")
            c.teacher_name, c.staff_no = r.get("teacher_name", ""), r.get("staff_no", "")
            c.location, c.weekday, c.periods = r.get("location", ""), r.get("weekday", ""), r.get("periods", "")
            c.weeks, c.audience = r.get("weeks", ""), r.get("audience", "")
            c.enrolled_count = clean_int(r.get("enrolled_count"))
            c.course_nature = r.get("course_nature", "")
            c.teacher_pending = (not c.staff_no) or "体育部" in c.teacher_name or not c.teacher_name
            if not c.teacher_pending:
                u = db.get(User, c.staff_no)
                if u is None:
                    db.add(User(staff_no=c.staff_no, name=c.teacher_name))
                elif not u.name:
                    u.name = c.teacher_name
            db.flush()
        removed = kept = 0
        if mode == "rebuild":
            for no, c in existing.items():
                if no in seen:
                    continue
                eids = list(db.scalars(select(Enrollment.id).where(Enrollment.class_id == c.id)))
                if _has_scores(db, eids):
                    kept += 1
                    continue
                for e in db.scalars(select(Enrollment).where(Enrollment.class_id == c.id)):
                    db.delete(e)
                db.delete(c)
                removed += 1
        db.flush()
        return {"created": created, "updated": updated, "removed": removed, "kept_with_scores": kept}

    # enroll
    classes = {c.class_no: c for c in db.scalars(
        select(TeachingClass).where(TeachingClass.semester_id == semester.id))}
    by_class: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in rows:
        if r.get("class_no") in classes and r.get("student_no"):
            by_class[r["class_no"]].setdefault(r["student_no"], r)
    added = updated = deactivated = removed = 0
    existing_per_class: dict[int, dict[str, Enrollment]] = defaultdict(dict)
    for e in db.scalars(select(Enrollment).where(Enrollment.semester_id == semester.id)):
        existing_per_class[e.class_id][e.student_no] = e
    target_classes = list(by_class) if mode != "rebuild" else list(classes)
    for no in target_classes:
        c = classes[no]
        cur = existing_per_class.get(c.id, {})
        if mode == "append" and cur:
            continue
        incoming = by_class.get(no, {})
        for i, (sno, r) in enumerate(incoming.items(), start=1):
            e = cur.get(sno)
            if e is None:
                e = Enrollment(semester_id=semester.id, class_id=c.id, student_no=sno)
                db.add(e)
                added += 1
            elif e.manual:
                continue  # 管理员手动改过（含调走、移除）的不覆盖
            else:
                updated += 1
            e.name, e.class_label, e.major = r.get("name", ""), r.get("class_label", ""), r.get("major", "")
            e.grade_field, e.college = r.get("grade_field", ""), r.get("college", "")
            e.campus, e.course_category = r.get("campus", ""), r.get("course_category", "")
            e.seq = i
            e.active = True if e.id is None else e.active
        gone = [e for s, e in cur.items() if s not in incoming and not e.manual]
        scored = _has_scores(db, [e.id for e in gone])
        for e in gone:
            if e.id in scored:
                if e.active:
                    e.active = False
                    deactivated += 1
            else:
                db.delete(e)
                removed += 1
        camp = Counter(r.get("campus", "") for r in incoming.values() if r.get("campus"))
        if camp:
            c.campus = camp.most_common(1)[0][0]
    db.flush()
    snap = rebuild_snapshots(db, semester, operator)
    pending = db.scalar(select(func.count()).select_from(StandardSnapshot).where(
        StandardSnapshot.semester_id == semester.id, StandardSnapshot.pending.is_(True)))
    return {"added": added, "updated": updated, "removed": removed, "deactivated_with_scores": deactivated,
            "students": snap["students"], "pending": pending, "band_changed": len(snap["changed"])}


def remember_mapping(db: Session, kind: str, mapping: dict):
    s = db.get(Setting, f"mapping:{kind}")
    if s is None:
        db.add(Setting(key=f"mapping:{kind}", value=mapping))
    else:
        s.value = dict(mapping)
