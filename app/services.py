"""成绩录入相关的业务逻辑：班级配置、行数据、保存、校验、提交。"""
from __future__ import annotations

import copy
import re
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import scoring
from .models import (AuditLog, Enrollment, ScoreHistory, ScoreRecord, Semester, StandardSnapshot, Student,
                     TeachingClass, now)

EXCELLENT_LINE = 89.5  # 总评四舍五入后≥90分即为优秀
EXCELLENT_WARN = 0.30  # 班级优秀率超过30%提醒


def _natural(text: str) -> list:
    """按数字分段：数字段按数值比较（前导零不影响），其余按文字比较。"""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", text)]


def class_sort_key(c: TeachingClass) -> tuple:
    """教学班的统一顺序：按课程分组，组内按班名自然排序。
    班名写法不统一（乒乓球1 / 乒乓球10班 / 五禽戏04班），去掉“班”字、数字按大小比，
    得到 乒乓球1、2…9、10班；五禽戏1、2、04班、05班。同名再按教学班号。"""
    name = (c.class_name or "").replace("班", "").strip()
    return (_natural(c.course_name or ""), _natural(name), _natural(c.class_no or ""))


def is_excellent(total) -> bool:
    return total is not None and total >= EXCELLENT_LINE


def excellence(rows: list[dict]) -> dict:
    n = len(rows)
    ex = sum(1 for r in rows if is_excellent(r["total"]))
    return {"excellent": ex, "roster": n, "rate": round(ex / n, 4) if n else 0.0,
            "over": bool(n) and ex / n > EXCELLENT_WARN}


INPUT_FIELDS = ("a_raw", "a_status", "b_raw", "b_status", "skill", "regular", "note", "course_status")
# 只有体测需要提交：提交后锁定的只是两项体测的成绩和特殊状态
PE_FIELDS = ("a_raw", "a_status", "b_raw", "b_status")
PE_LOCKED_MSG = "本班体测成绩已提交并锁定，如需更正请提交撤回申请"


def log(db: Session, actor: str, action: str, target: str = "", semester_id: int | None = None,
        ip: str = "", **detail):
    db.add(AuditLog(actor=actor, action=action, target=target, semester_id=semester_id, ip=ip, detail=detail))


def class_config(c: TeachingClass, sem: Semester) -> dict:
    return copy.deepcopy(c.config or sem.config)


def can_edit(c: TeachingClass, sem: Semester) -> tuple[bool, str]:
    """学期层面能不能录入。体测提交后的锁定按字段判断，见 pe_locked。"""
    if not sem.is_current and not sem.entry_open:
        return False, "以往学期为只读；需补录或更正时请联系体育部开放录入窗口"
    return True, ""


def pe_locked(c: TeachingClass) -> bool:
    return c.status == "已提交"


def student_display(e: Enrollment, stu: Student | None) -> dict:
    """姓名、学院：教师或体育部改过学生基础数据（manual）时以学生基础数据为准，否则以选课表为准。"""
    manual = bool(stu and stu.manual)
    name = (stu.name if manual and stu.name else "") or e.name or (stu.name if stu else "")
    college = (stu.college if manual and stu.college else "") or e.college or (stu.college if stu else "")
    return {"name": name, "college": college}


def load_class_rows(db: Session, c: TeachingClass, sem: Semester) -> list[dict]:
    enr = db.scalars(select(Enrollment).where(Enrollment.class_id == c.id, Enrollment.active.is_(True))
                     .order_by(Enrollment.seq, Enrollment.student_no)).all()
    nos = [e.student_no for e in enr]
    students = {s.student_no: s for s in db.scalars(select(Student).where(Student.student_no.in_(nos)))} if nos else {}
    snaps = {s.student_no: s for s in db.scalars(select(StandardSnapshot).where(
        StandardSnapshot.semester_id == sem.id, StandardSnapshot.student_no.in_(nos)))} if nos else {}
    scores = {s.enrollment_id: s for s in db.scalars(select(ScoreRecord).where(
        ScoreRecord.enrollment_id.in_([e.id for e in enr])))} if enr else {}
    prev = previous_scores(db, sem, nos)
    cfg = class_config(c, sem)
    rows = []
    for i, e in enumerate(enr, start=1):
        rows.append(build_row(i, e, students.get(e.student_no), snaps.get(e.student_no), scores.get(e.id),
                              cfg, sem, prev.get(e.student_no)))
    return rows


def previous_scores(db: Session, sem: Semester, nos: list[str]) -> dict:
    """上一学期同学生的成绩，用于“成绩突变”校验。"""
    prev_sem = db.scalars(select(Semester).where(Semester.code < sem.code).order_by(Semester.code.desc())).first()
    if not prev_sem or not nos:
        return {}
    q = (select(Enrollment.student_no, ScoreRecord, TeachingClass)
         .join(ScoreRecord, ScoreRecord.enrollment_id == Enrollment.id)
         .join(TeachingClass, TeachingClass.id == Enrollment.class_id)
         .where(Enrollment.semester_id == prev_sem.id, Enrollment.student_no.in_(nos)))
    out = {}
    for no, rec, tc in db.execute(q):
        cfg = tc.config or prev_sem.config
        items = {}
        for slot in cfg["slots"]:
            raw = getattr(rec, f"{slot['key']}_raw")
            if raw is not None:
                for g, it in slot["items"].items():
                    items[(g, it)] = raw
        out[no] = items
    return out


def build_row(seq, e: Enrollment, stu: Student | None, snap: StandardSnapshot | None, rec: ScoreRecord | None,
              cfg: dict, sem: Semester, prev: dict | None) -> dict:
    gender = stu.gender if stu else None
    band = snap.band if snap else None
    values = {f: getattr(rec, f, None) if rec else None for f in INPUT_FIELDS}
    for k in ("a_status", "b_status", "course_status"):
        values[k] = values[k] or "正常"
    comp = scoring.compute_row(gender=gender, band=band, config=cfg, values=values,
                               version=rec.standard_version if rec else sem.standard_version)
    issues = list(comp["issues"])
    flags = []
    pending = bool(snap and snap.pending)
    if pending:
        flags.append("pending")
    if gender is None:
        flags.append("gender_unknown")
    if snap and snap.changed_notice:
        flags.append("band_changed")
    sudden = []
    if prev and gender:
        for slot in cfg["slots"]:
            it = slot["items"].get(gender)
            old = prev.get((gender, it))
            new = values.get(f"{slot['key']}_raw")
            if old and new and abs(new - old) / old > 0.25:
                sudden.append(f"{scoring.item_label(it)}与上学期（{scoring.fmt_raw(it, old)}）相差超过25%")
    shown = student_display(e, stu)
    row = {
        "enrollment_id": e.id, "seq": seq, "student_no": e.student_no, "name": shown["name"],
        "gender": {"M": "男", "F": "女"}.get(gender, "待确认"), "gender_code": gender,
        "college": shown["college"], "major": e.major, "class_label": e.class_label,
        "grade_num": snap.grade_num if snap else None,
        "band": band, "band_label": band_label(band, pending),
        "band_source": snap.source if snap else "", "pending": pending,
        "pending_reasons": snap.pending_reasons if snap else ["尚未生成标准档"],
        "info_error": e.info_error, "info_error_resolved": bool(e.info_error_resolved), "flags": flags,
        "course_status": values["course_status"],
        "values": values,
        "display": {}, "subtotal": comp["subtotal"], "total": comp["total"], "complete": comp["complete"],
        "issues": issues, "sudden": sudden,
        "updated_by": rec.updated_by if rec else "", "updated_at": rec.updated_at.isoformat() if rec and rec.updated_at else None,
    }
    for k, info in comp["slots"].items():
        r = info["result"]
        row["display"][k] = {
            "item": info["item"], "item_label": scoring.item_label(info["item"]),
            "raw_text": scoring.fmt_raw(info["item"], values.get(f"{k}_raw")) if info["item"] else
            ("" if values.get(f"{k}_raw") is None else f"{values.get(f'{k}_raw'):g}"),
            "status": info["status"], "status_display": info["display"],
            "score": info["score"], "bonus": r["bonus"] if r else 0, "folded": info["folded"],
            "hint": info["hint"], "level": scoring.level_name(r["score"]) if r else None,
            "below_min": r["below_min"] if r else False,
            "explain": explain(info, r, cfg, k),
        }
    return row


def band_label(band, pending=False):
    s = {"12": "大一大二", "34": "大三大四"}.get(band, "待确认")
    return s + ("（待确认）" if pending and band else "")


def explain(info, r, cfg, k):
    if r is None:
        return None
    slot = next(s for s in cfg["slots"] if s["key"] == k)
    unit = "秒" if r["item"] in scoring.TIME_ITEMS else r["unit"]
    steps = [f"原始值：{scoring.fmt_raw(r['item'], r['raw'])}（{r['raw']:g}{unit}）",
             f"评分表：{scoring.item_label(r['item'])}·{'男' if r['gender'] == 'M' else '女'}·"
             f"{band_label(r['band'])}·{r['version']}"]
    if r["below_min"]:
        steps.append("命中档位：低于最低档（10分档），单项得分记0分")
    else:
        th = scoring.fmt_raw(r["item"], r["threshold"])
        steps.append(f"命中档位：{r['level_score']}分档（阈值 {th}）")
    steps.append(f"单项得分：{r['score']:g}")
    if r.get("bonus"):
        steps.append(f"加分：超出100分档 {r.get('bonus_diff', 0):g}{unit} → +{r['bonus']:g}分")
    steps.append(f"折算分：{r['total']:g} × {slot['weight'] * 100:g}% = {info['folded']:.2f}")
    return steps


# ---------------------------------------------------------------- 保存


def parse_cell(field: str, text, cfg: dict, gender: str | None):
    """解析一个输入单元格，返回存储值；非法时抛 ParseError。"""
    if text is None or (isinstance(text, str) and text.strip() == ""):
        return None
    if field in ("a_raw", "b_raw"):
        slot = next(s for s in cfg["slots"] if s["key"] == field[0])
        if not gender:
            raise scoring.ParseError("该生性别待确认，暂不能录入体测成绩，请联系体育部核对")
        item = slot["items"][gender]
        if isinstance(text, (int, float)) and item not in scoring.TIME_ITEMS:
            return float(text)
        return scoring.parse_raw(item, text)
    if field == "skill":
        return scoring.score_parse_simple(text, 0, round(cfg.get("skill_weight", 0.6) * 100, 1), "运动技能评定")
    if field == "regular":
        return scoring.score_parse_simple(text, 0, round(cfg.get("regular_weight", 0.1) * 100, 1), "平时成绩")
    if field in ("a_status", "b_status"):
        if text not in scoring.STATUSES:
            raise scoring.ParseError("成绩状态只能是：" + "／".join(scoring.STATUSES))
        return text
    if field == "note":
        return str(text)[:256]
    if field == "course_status":
        if text not in scoring.COURSE_STATUSES:
            raise scoring.ParseError("课程成绩只能是：" + "／".join(scoring.COURSE_STATUSES))
        return text
    raise scoring.ParseError("未知字段")


def soft_warnings(field: str, value, cfg: dict, gender: str | None, band: str | None, version: str) -> list[str]:
    """不阻断录入的复核提示：超出合理区间、低于最低档。"""
    if value is None or field not in ("a_raw", "b_raw") or not gender:
        return []
    slot = next(s for s in cfg["slots"] if s["key"] == field[0])
    item = slot["items"][gender]
    ranges = {"long_jump": (100, 330), "run1000": (150, 600), "run800": (150, 600), "run50": (5.5, 15),
              "pull_up": (0, 40), "sit_up": (0, 80)}
    w = []
    lo, hi = ranges.get(item, (None, None))
    if lo is not None and not (lo <= value <= hi):
        w.append(f"{scoring.item_label(item)} {scoring.fmt_raw(item, value)} 明显超出合理区间，请复核")
    if band:
        r = scoring.item_score(item, gender, band, value, version)
        if r["below_min"]:
            w.append("低于评分表最低档，请复核")
    return w


def save_cells(db: Session, c: TeachingClass, sem: Semester, changes: list[dict], operator: str,
               batch: str | None = None) -> dict:
    """changes: [{"enrollment_id", "field", "value"}]。返回每个单元格的结果和刷新后的行。"""
    cfg = class_config(c, sem)
    eids = {ch["enrollment_id"] for ch in changes}
    enr = {e.id: e for e in db.scalars(select(Enrollment).where(Enrollment.id.in_(eids), Enrollment.class_id == c.id))}
    recs = {r.enrollment_id: r for r in db.scalars(select(ScoreRecord).where(ScoreRecord.enrollment_id.in_(eids)))}
    stus = {s.student_no: s for s in db.scalars(select(Student).where(
        Student.student_no.in_([e.student_no for e in enr.values()])))}
    snaps = {s.student_no: s for s in db.scalars(select(StandardSnapshot).where(
        StandardSnapshot.semester_id == sem.id, StandardSnapshot.student_no.in_([e.student_no for e in enr.values()])))}
    results = []
    touched = set()
    locked = pe_locked(c)
    for ch in changes:
        e = enr.get(ch["enrollment_id"])
        field = ch["field"]
        if e is None or field not in INPUT_FIELDS:
            results.append({**ch, "ok": False, "error": "行或字段不存在"})
            continue
        if locked and field in PE_FIELDS:
            results.append({**ch, "ok": False, "error": PE_LOCKED_MSG})
            continue
        stu = stus.get(e.student_no)
        gender = stu.gender if stu else None
        rec = recs.get(e.id)
        try:
            val = parse_cell(field, ch.get("value"), cfg, gender)
        except scoring.ParseError as ex:
            results.append({**ch, "ok": False, "error": str(ex)})
            continue
        if field in ("a_status", "b_status", "course_status") and val is None:
            val = "正常"
        raw_field = field.replace("_status", "_raw")
        if field in ("a_raw", "b_raw") and val is not None and rec and (getattr(rec, field[0] + "_status") or "正常") != "正常":
            results.append({**ch, "ok": False, "error": "该项已选择特殊状态，数值与状态互斥；请先把状态改回“正常实测”"})
            continue
        if rec is None:
            rec = ScoreRecord(enrollment_id=e.id, standard_version=sem.standard_version, computed={},
                              a_status="正常", b_status="正常", course_status="正常")
            db.add(rec)
            recs[e.id] = rec
        old = getattr(rec, field)
        if field == "course_status":
            old = old or "正常"
        if old == val:
            results.append({**ch, "ok": True, "unchanged": True})
            continue
        setattr(rec, field, val)
        if field in ("a_status", "b_status") and val != "正常" and getattr(rec, raw_field) is not None:
            # 选择特殊状态后数值列只读：清掉原数值并留痕
            db.add(ScoreHistory(enrollment_id=e.id, field=raw_field, old=_s(getattr(rec, raw_field)), new=None,
                                operator=operator, batch=batch, standard_version=rec.standard_version,
                                computed={"reason": f"选择特殊状态“{val}”"}))
            setattr(rec, raw_field, None)
        rec.updated_by, rec.updated_at = operator, now()
        snap = snaps.get(e.student_no)
        values = {f: getattr(rec, f) for f in INPUT_FIELDS}
        comp = scoring.compute_row(gender=gender, band=snap.band if snap else None, config=cfg,
                                   values=values, version=rec.standard_version)
        rec.computed = _summ(comp)
        slot_info = comp["slots"].get(field[0]) if field[0] in "ab" else None
        db.add(ScoreHistory(enrollment_id=e.id, field=field, old=_s(old), new=_s(val), operator=operator,
                            batch=batch, standard_version=rec.standard_version,
                            computed={"slot": _slot_summ(slot_info), "subtotal": comp["subtotal"],
                                      "total": comp["total"]}))
        touched.add(e.id)
        results.append({**ch, "ok": True, "warnings": soft_warnings(field, val, cfg, gender,
                                                                   snap.band if snap else None,
                                                                   rec.standard_version)})
    if touched and c.status == "未开始":
        c.status = "录入中"
    db.flush()
    return {"results": results, "touched": sorted(touched)}


def _s(v):
    return None if v is None else str(v)


def _slot_summ(info):
    if not info:
        return None
    r = info["result"]
    return {"item": info["item"], "status": info["status"], "score": info["score"], "folded": info["folded"],
            "level": r["level_score"] if r else None, "bonus": r["bonus"] if r else None}


def _summ(comp):
    return {"slots": {k: _slot_summ(v) for k, v in comp["slots"].items()},
            "subtotal": comp["subtotal"], "total": comp["total"], "complete": comp["complete"]}


def preview_rows(db: Session, c: TeachingClass, sem: Semester, changes: list[dict]) -> dict:
    """按还没保存的修改即时算出这些行的得分和总成绩，不写数据库（录入页“离开单元格即换算”用）。"""
    from types import SimpleNamespace
    cfg = class_config(c, sem)
    by_eid: dict[int, list[dict]] = defaultdict(list)
    for ch in changes:
        by_eid[int(ch["enrollment_id"])].append(ch)
    enr = {e.id: e for e in db.scalars(select(Enrollment).where(Enrollment.id.in_(list(by_eid)),
                                                                Enrollment.class_id == c.id))}
    rows, errors = [], []
    for eid, chs in by_eid.items():
        e = enr.get(eid)
        if e is None:
            continue
        stu = db.get(Student, e.student_no)
        snap = db.scalar(select(StandardSnapshot).where(StandardSnapshot.semester_id == sem.id,
                                                        StandardSnapshot.student_no == e.student_no))
        rec = db.scalar(select(ScoreRecord).where(ScoreRecord.enrollment_id == eid))
        vals = {f: (getattr(rec, f) if rec else None) for f in INPUT_FIELDS}
        for k in ("a_status", "b_status", "course_status"):
            vals[k] = vals[k] or "正常"
        gender = stu.gender if stu else None
        for ch in chs:
            f = ch.get("field")
            if f not in INPUT_FIELDS or (pe_locked(c) and f in PE_FIELDS):
                continue
            try:
                v = parse_cell(f, ch.get("value"), cfg, gender)
            except scoring.ParseError as ex:
                errors.append({"enrollment_id": eid, "field": f, "error": str(ex)})
                continue
            if f == "course_status":
                v = v or "正常"
            if f in ("a_status", "b_status"):
                v = v or "正常"
                if v != "正常":
                    vals[f.replace("_status", "_raw")] = None
            vals[f] = v
        fake = SimpleNamespace(**vals, standard_version=rec.standard_version if rec else sem.standard_version,
                               updated_by=rec.updated_by if rec else "", updated_at=rec.updated_at if rec else None)
        row = build_row(0, e, stu, snap, fake, cfg, sem, None)
        if fake.updated_at is None:
            row["updated_at"] = None
        rows.append(row)
    return {"rows": rows, "errors": errors}


def recompute_class(db: Session, c: TeachingClass, sem: Semester):
    cfg = class_config(c, sem)
    for row in db.execute(select(ScoreRecord, Enrollment).join(Enrollment, Enrollment.id == ScoreRecord.enrollment_id)
                          .where(Enrollment.class_id == c.id)):
        rec, e = row
        stu = db.get(Student, e.student_no)
        snap = db.scalar(select(StandardSnapshot).where(StandardSnapshot.semester_id == sem.id,
                                                        StandardSnapshot.student_no == e.student_no))
        comp = scoring.compute_row(gender=stu.gender if stu else None, band=snap.band if snap else None,
                                   config=cfg, values={f: getattr(rec, f) for f in INPUT_FIELDS},
                                   version=rec.standard_version)
        rec.computed = _summ(comp)


def recompute_students(db: Session, sem: Semester, nos: set[str]):
    classes = {}
    for e in db.scalars(select(Enrollment).where(Enrollment.semester_id == sem.id, Enrollment.student_no.in_(nos))):
        classes[e.class_id] = True
    for cid in classes:
        recompute_class(db, db.get(TeachingClass, cid), sem)


# ---------------------------------------------------------------- 提交前校验

def submit_check(rows: list[dict], cfg: dict) -> dict:
    """只有体测需要提交，校验只看两项体测；运动技能、平时成绩提交后照样能改，不在这里查。"""
    lists = defaultdict(list)
    for r in rows:
        who = f"{r['seq']}. {r['student_no']} {r['name']}"
        missing = []
        for slot in cfg["slots"]:
            k = slot["key"]
            v = r["values"]
            if v.get(f"{k}_status") == "正常" and v.get(f"{k}_raw") is None:
                missing.append(r["display"][k]["item_label"] or slot.get("label", k))
        if missing:
            lists["missing"].append(f"{who}：{'、'.join(missing)}")
        if r["pending"] or r["gender_code"] is None:
            lists["pending"].append(f"{who}：{'；'.join(r['pending_reasons'] or ['性别待确认'])}")
        for k, d in r["display"].items():
            if d["bonus"] and d["bonus"] > 10:
                lists["bonus"].append(f"{who}：{d['item_label']}加分{d['bonus']:g}超过上限")
        for s in r["sudden"]:
            lists["sudden"].append(f"{who}：{s}")
    # 体测两项都要填了或标了特殊情况，且没有年级/性别待确认的学生
    return {"missing": lists["missing"], "pending": lists["pending"], "bonus": lists["bonus"],
            "sudden": lists["sudden"],
            "blocking": bool(lists["pending"]) or bool(lists["missing"])}


def is_missing(r: dict) -> bool:
    """与录入页“未录入”筛选一致：课程成绩标了特殊情况的只看两项体测。"""
    v = r["values"]
    miss = any(v.get(f"{k}_status") == "正常" and v.get(f"{k}_raw") is None for k in ("a", "b"))
    if r.get("course_status", "正常") != "正常":
        return miss
    return miss or v.get("skill") is None or v.get("regular") is None


def sort_rows(rows: list[dict], by: str) -> list[dict]:
    """与录入页的排序选项一致（学号、姓名、总成绩、未录入在前），教师导出照网页顺序。"""
    import re as _re

    def no_key(r):
        return [int(t) if t.isdigit() else t for t in _re.split(r"(\d+)", str(r["student_no"]))]
    out = sorted(rows, key=no_key)
    if by == "name":
        out.sort(key=lambda r: str(r["name"]))
    elif by == "cls":
        out.sort(key=lambda r: [int(t) if t.isdigit() else t for t in _re.split(r"(\d+)", str(r["class_label"] or ""))])
    elif by == "total":
        out.sort(key=lambda r: -(r["total"] if r["total"] is not None else -1))
    elif by == "missing":
        out.sort(key=lambda r: not is_missing(r))
    return out


# ---------------------------------------------------------------- 回退一次跨班导入

def revert_batch(db: Session, sem: Semester, batch: str, operator: str) -> int | None:
    """把一次导入写入的格子改回导入前的值。导入后又被改过的格子、体测已提交班的体测成绩不动。
    返回改回的格数；没有这次导入的记录时返回 None。"""
    q = (select(ScoreHistory, Enrollment).join(Enrollment, Enrollment.id == ScoreHistory.enrollment_id)
         .where(ScoreHistory.batch == batch, Enrollment.semester_id == sem.id).order_by(ScoreHistory.id.desc()))
    hs = db.execute(q).all()
    if not hs:
        return None
    n, classes = 0, set()
    for h, e in hs:
        c = db.get(TeachingClass, e.class_id)
        if pe_locked(c) and h.field in PE_FIELDS:
            continue
        rec = db.scalar(select(ScoreRecord).where(ScoreRecord.enrollment_id == h.enrollment_id))
        if rec is None or _s(getattr(rec, h.field)) != h.new:
            continue
        old = None if h.old is None else (float(h.old) if h.field.endswith("_raw") else h.old)
        setattr(rec, h.field, old)
        rec.updated_by, rec.updated_at = operator, now()
        db.add(ScoreHistory(enrollment_id=h.enrollment_id, field=h.field, old=h.new, new=h.old, operator=operator,
                            standard_version=rec.standard_version, computed={"reason": f"回退导入 {batch}"}))
        n += 1
        classes.add(c.id)
    db.flush()
    for cid in classes:
        recompute_class(db, db.get(TeachingClass, cid), sem)
    return n
