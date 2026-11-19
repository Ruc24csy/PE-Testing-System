"""体育部从表格导入体测成绩（如运动会、集中测试的成绩单），同一学生同一项目取最优。

表格的写法（体育部样表《新生运动会各项目名单》）：
- 每个工作表一个项目，表名就是项目（立定跳远、仰卧起坐（女）、引体向上（男）、50米、800米、1000米）；
  表名认得出项目时只读这一项对应的那一列。表名认不出项目时，按列名读出每一列的项目，一行可以有多项。
- 表头里有“学号”；“引体向上-男/仰卧起坐-女”“800米-女/1000米-男”这种合并列按学生性别取项目。
- 耐力跑写“分.秒”：3.24 是 3分24秒；Excel 存成数字后末尾的 0 会丢，4.1 是 4分10秒，3 是 3分00秒。
- 肺活量、坐位体前屈、身高、体重平台不算分，不读。

只写进学生在这一学期的体测项目：表里的项目不是该生本学期要测的，跳过。
一人多条有效选课时每条都写（各班各自比较）；体测已提交的班、该项标了免测缓测等特殊状态的不改。
同一学生同一项目，表里多条与系统里已有的成绩一起比，取最好的一个，不会用更差的成绩盖掉已有成绩。
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import openpyxl
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import scoring, services
from .importer import clean
from .models import Enrollment, ScoreRecord, Semester, Student, TeachingClass

GENDER_ITEMS = {"pull_up", "sit_up", "run1000", "run800"}
PULL_SIT = {"M": "pull_up", "F": "sit_up"}
RUNS = {"M": "run1000", "F": "run800"}


def _norm(text) -> str:
    return re.sub(r"\s+", "", clean(text))


def item_of(text) -> str | dict | None:
    """从表名或列名认项目：返回项目代码，男女合并列返回 {性别: 项目}，认不出返回 None。"""
    s = _norm(text)
    if not s:
        return None
    if "引体" in s and "仰卧" in s:
        return dict(PULL_SIT)
    if "800" in s and "1000" in s:
        return dict(RUNS)
    if "引体" in s:
        return "pull_up"
    if "仰卧" in s:
        return "sit_up"
    if "1000" in s:
        return "run1000"
    if "800" in s:
        return "run800"
    if "50米" in s or s in ("50", "50m"):
        return "run50"
    if "跳远" in s:
        return "long_jump"
    return None


def _covers(col_item, item: str) -> bool:
    return col_item == item or (isinstance(col_item, dict) and item in col_item.values())


def read_entries(path: Path) -> tuple[list[dict], list[str]]:
    """逐个工作表读出成绩：[{sheet,row,student_no,name,item,raw}]；item 可能是男女合并列的 dict。"""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    entries, notes = [], []
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        hr = next((i for i, r in enumerate(rows[:20]) if any(_norm(v) == "学号" for v in r)), None)
        if hr is None:
            if any(any(v not in (None, "") for v in r) for r in rows):
                notes.append(f"工作表“{ws.title}”找不到“学号”列，未读取")
            continue
        head = rows[hr]
        sno_col = next(i for i, v in enumerate(head) if _norm(v) == "学号")
        name_col = next((i for i, v in enumerate(head) if "姓名" in _norm(v)), None)
        col_items = {i: item_of(v) for i, v in enumerate(head) if i != sno_col}
        col_items = {i: it for i, it in col_items.items() if it}
        sheet_item = item_of(ws.title)
        if isinstance(sheet_item, str):
            cols = {i: sheet_item for i, it in col_items.items() if _covers(it, sheet_item)}
            if not cols:
                notes.append(f"工作表“{ws.title}”没有{scoring.item_label(sheet_item)}这一列，未读取")
                continue
        else:
            cols = col_items
            if not cols:
                notes.append(f"工作表“{ws.title}”认不出体测项目列，未读取")
                continue
        for n, r in enumerate(rows[hr + 1:], start=hr + 2):
            cell = lambda i: r[i] if i is not None and i < len(r) else None  # noqa: E731
            sno = clean(cell(sno_col)).replace(" ", "")
            name = clean(cell(name_col))
            for i, it in cols.items():
                raw = cell(i)
                if raw is None or clean(raw) == "":
                    continue
                entries.append({"sheet": ws.title, "row": n, "student_no": sno, "name": name, "item": it,
                                "raw": raw})
    return entries, notes


def parse_value(item: str, raw) -> float:
    """解析一格成绩，返回存储值（时间为秒）。耐力跑的纯数字按“分.秒”理解。"""
    if item in ("run1000", "run800"):
        s = f"{raw:.2f}" if isinstance(raw, float) else (str(raw) if isinstance(raw, int) else scoring.clean(raw))
        m = re.fullmatch(r"(\d{1,2})(?:\.(\d{1,2}))?", s)
        if m:
            sec = int((m.group(2) or "0").ljust(2, "0"))
            if sec >= 60:
                raise scoring.ParseError("秒数不能大于等于60（耐力跑写“分.秒”，如 3.24）")
            return float(int(m.group(1)) * 60 + sec)
        return scoring.parse_time(s, item)
    if item == "run50":
        return scoring.parse_time(f"{raw}" if isinstance(raw, (int, float)) else raw, item)
    return float(raw) if isinstance(raw, (int, float)) else scoring.parse_number(raw)


def better(item: str, a: float, b: float) -> bool:
    """a 是否严格好于 b：跑步用时短为好，其余大为好。"""
    return a < b if item in scoring.TIME_ITEMS else a > b


def _raw_text(raw) -> str:
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    return clean(raw)


def plan(db: Session, sem: Semester, entries: list[dict]) -> dict:
    """算出要写哪些成绩、跳过哪些及原因，不写数据库。"""
    nos = sorted({e["student_no"] for e in entries if e["student_no"]})
    students = {s.student_no: s for s in db.scalars(select(Student).where(Student.student_no.in_(nos)))} if nos else {}
    enr_by_no: dict[str, list[tuple[Enrollment, TeachingClass]]] = defaultdict(list)
    if nos:
        q = (select(Enrollment, TeachingClass).join(TeachingClass, TeachingClass.id == Enrollment.class_id)
             .where(Enrollment.semester_id == sem.id, Enrollment.active.is_(True), Enrollment.student_no.in_(nos)))
        for e, c in db.execute(q):
            enr_by_no[e.student_no].append((e, c))
    eids = [e.id for lst in enr_by_no.values() for e, _ in lst]
    recs = {r.enrollment_id: r for r in db.scalars(select(ScoreRecord).where(ScoreRecord.enrollment_id.in_(eids)))} \
        if eids else {}

    skips: list[dict] = []
    cands: dict[tuple[int, str], list[dict]] = defaultdict(list)
    targets: dict[tuple[int, str], tuple[Enrollment, TeachingClass, str]] = {}

    def skip(ent, reason, c: TeachingClass | None = None, item=None):
        it = item or (ent["item"] if isinstance(ent["item"], str) else None)
        skips.append({"sheet": ent["sheet"], "row": ent["row"], "student_no": ent["student_no"],
                      "name": ent["name"], "item_label": scoring.item_label(it) if it else "",
                      "value": _raw_text(ent["raw"]), "class": f"{c.class_no} {c.class_name}" if c else "",
                      "reason": reason})

    for ent in entries:
        sno = ent["student_no"]
        if not sno:
            skip(ent, "缺学号")
            continue
        pairs = enr_by_no.get(sno)
        if not pairs:
            skip(ent, "本学期没有这个学号的有效选课记录")
            continue
        stu = students.get(sno)
        gender = stu.gender if stu else None
        item = ent["item"]
        if isinstance(item, dict):
            if not gender:
                skip(ent, "性别待确认，认不出是哪个项目")
                continue
            item = item[gender]
        if not gender:
            skip(ent, "该生性别待确认，暂不能写入体测成绩", item=item)
            continue
        need = scoring.ITEM_GENDER.get(item)
        if need and need != gender:
            skip(ent, f"该生是{'男' if gender == 'M' else '女'}生，不测{scoring.item_label(item)}", item=item)
            continue
        try:
            val = parse_value(item, ent["raw"])
        except scoring.ParseError as ex:
            skip(ent, f"成绩格式不对：{ex}", item=item)
            continue
        if val < 0:
            skip(ent, "成绩不能是负数", item=item)
            continue
        for e, c in pairs:
            cfg = services.class_config(c, sem)
            slot = next((s for s in cfg["slots"] if s["items"].get(gender) == item), None)
            if slot is None:
                mine = "、".join(scoring.item_label(s["items"][gender]) for s in cfg["slots"])
                skip(ent, f"不是该生本学期的体测项目（本学期测{mine}）", c, item)
                continue
            field = f"{slot['key']}_raw"
            if services.pe_locked(c):
                skip(ent, "本班体测成绩已提交锁定，未改", c, item)
                continue
            rec = recs.get(e.id)
            status = (getattr(rec, f"{slot['key']}_status") if rec else None) or "正常"
            if status != "正常":
                skip(ent, f"该项已标为“{status}”，未改", c, item)
                continue
            cands[(e.id, field)].append({**ent, "item": item, "value": val})
            targets[(e.id, field)] = (e, c, item)

    updates = []
    for key, lst in cands.items():
        e, c, item = targets[key]
        best = lst[0]
        for x in lst[1:]:
            if better(item, x["value"], best["value"]):
                best = x
        for x in lst:
            if x is not best:
                skip(x, f"表里有更好的一条（{best['sheet']} 第{best['row']}行 "
                        f"{scoring.fmt_raw(item, best['value'])}）", c, item)
        rec = recs.get(e.id)
        old = getattr(rec, key[1]) if rec else None
        if old is not None and not better(item, best["value"], old):
            same = "相同" if old == best["value"] else "更好"
            skip(best, f"系统里已有{same}的成绩 {scoring.fmt_raw(item, old)}，保留原成绩", c, item)
            continue
        updates.append({"class_id": c.id, "class": f"{c.class_no} {c.class_name}", "enrollment_id": e.id,
                        "field": key[1], "student_no": e.student_no, "name": best["name"] or e.name,
                        "item": item, "item_label": scoring.item_label(item),
                        "old": scoring.fmt_raw(item, old), "new": scoring.fmt_raw(item, best["value"]),
                        "value": best["value"], "sheet": best["sheet"], "row": best["row"]})
    updates.sort(key=lambda u: (u["class"], u["student_no"], u["field"]))
    skips.sort(key=lambda s: (s["sheet"], s["row"]))
    reasons: dict[str, int] = defaultdict(int)
    for s in skips:
        reasons[re.sub(r"[（(].*", "", s["reason"])] += 1
    return {"updates": updates, "skips": skips, "reason_counts": dict(reasons),
            "students": len({u["student_no"] for u in updates}), "entries": len(entries)}


def commit(db: Session, sem: Semester, updates: list[dict], operator: str, batch: str) -> int:
    """按班调用录入保存（同样的校验、算分、留痕），返回写入的格数。"""
    by_class: dict[int, list[dict]] = defaultdict(list)
    for u in updates:
        # 时间项目以“秒”传入，避免录入解析把纯数字当成分秒连写
        v = f"{u['value']:g}秒" if u["item"] in scoring.TIME_ITEMS else u["value"]
        by_class[u["class_id"]].append({"enrollment_id": u["enrollment_id"], "field": u["field"], "value": v})
    saved = 0
    for cid, changes in by_class.items():
        c = db.get(TeachingClass, cid)
        res = services.save_cells(db, c, sem, changes, operator, batch=batch)
        saved += sum(1 for r in res["results"] if r.get("ok") and not r.get("unchanged"))
    return saved
