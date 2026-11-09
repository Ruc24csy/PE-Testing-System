"""学生年级判定与标准档匹配（需求文档二（六））。"""
from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Enrollment, GradeCorrection, Semester, StandardSnapshot, Student, now

SPECIAL_KEYWORDS = ["第二学士", "第二学位", "留学生", "国际学生", "预科", "交换", "港澳台", "港澳", "台湾"]


def band_of(grade_num: int | None) -> str | None:
    if grade_num is None or grade_num < 1:
        return None
    return "12" if grade_num <= 2 else "34"


def semester_start_year(code: str) -> int:
    return int(code[:4])


def correction_for(corrs: list[GradeCorrection], semester: Semester) -> GradeCorrection | None:
    """取对本学期生效的最新一条校正记录。"""
    valid = [c for c in corrs if not c.effective_semester or c.effective_semester <= semester.code]
    if not valid:
        return None
    return sorted(valid, key=lambda c: (c.effective_semester or "", c.id))[-1]


def judge(student_no: str, semester: Semester, *, enrollments: list[Enrollment],
          student: Student | None, corrs: list[GradeCorrection]) -> dict:
    """返回 {grade_num, band, source, detail, pending, reasons}。"""
    S = semester.start_year
    reasons: list[str] = []
    detail: dict = {"学号": student_no}

    # 学号推算
    by_no = None
    if re.fullmatch(r"\d{10}", student_no or ""):
        y = int(student_no[:4])
        if S - 10 <= y <= S:
            by_no = S - y + 1
        else:
            reasons.append("学号前4位超出合理区间")
    else:
        reasons.append("学号不是10位")
    detail["学号推算年级"] = by_no

    # 上课年级
    fields = {e.grade_field.strip() for e in enrollments if e.grade_field and e.grade_field.strip()}
    by_field = None
    if len(fields) == 1:
        f = fields.pop()
        detail["上课年级"] = f
        if re.fullmatch(r"\d{4}", f):
            by_field = S - int(f) + 1
    elif len(fields) > 1:
        detail["上课年级"] = "/".join(sorted(fields))
        reasons.append("多条选课记录的上课年级不一致")
    else:
        detail["上课年级"] = ""
        reasons.append("上课年级为空")
    detail["上课年级推算年级"] = by_field

    if len([e for e in enrollments if e.active]) > 1:
        reasons.append("同一学号对应多条选课记录")
    if student is None:
        reasons.append("无对应学籍信息（学生基础数据中未找到）")
    elif student.gender not in ("M", "F"):
        reasons.append("学生基础数据中性别为空或取值异常")

    labels = " ".join(f"{e.class_label} {e.major}" for e in enrollments)
    hit_kw = [k for k in SPECIAL_KEYWORDS if k in labels]
    if hit_kw:
        reasons.append(f"特殊学籍（{hit_kw[0]}），须体育部指定标准档")

    corr = correction_for(corrs, semester)
    if corr is not None:
        detail["异动记录"] = {"类型": corr.change_type, "生效学期": corr.effective_semester,
                          "依据": corr.note, "操作人": corr.operator,
                          "时间": corr.created_at.strftime("%Y-%m-%d %H:%M") if corr.created_at else ""}
        if corr.band:
            grade_num = None
            band = corr.band
        else:
            grade_num = S - corr.cohort + 1 if corr.cohort else None
            band = band_of(grade_num)
        # 异动记录是第一优先，命中即以记录为准；只保留与年级无关的问题（性别、学籍、多条选课）
        keep = [r for r in reasons if r.startswith(("学生基础数据", "无对应学籍", "同一学号"))]
        return {"grade_num": grade_num, "band": band, "source": "异动记录", "detail": detail,
                "pending": bool(keep), "reasons": keep}

    if by_field is not None:
        grade_num, source = by_field, "上课年级"
        if by_no is not None and band_of(by_no) != band_of(by_field):
            reasons.append(f"上课年级与学号推算冲突（大{by_field} / 大{by_no}），跨越大二/大三分界")
    elif by_no is not None:
        grade_num, source = by_no, "学号推算"
    else:
        grade_num, source = None, ""
    if grade_num is not None and grade_num > 4:
        detail["说明"] = f"超出正常修读年限（大{grade_num}），按大三大四标准"
    if grade_num is not None and grade_num < 1:
        reasons.append("推算年级小于1")
        grade_num = None
    return {"grade_num": grade_num, "band": band_of(grade_num), "source": source, "detail": detail,
            "pending": bool(reasons), "reasons": reasons}


def rebuild_snapshots(db: Session, semester: Semester, operator: str = "system",
                      only: set[str] | None = None) -> dict:
    """为本学期全部（或指定）学生生成/刷新标准档快照。返回变更了档位的学号列表。"""
    q = select(Enrollment).where(Enrollment.semester_id == semester.id)
    enr = db.scalars(q).all()
    by_stu: dict[str, list[Enrollment]] = defaultdict(list)
    for e in enr:
        if only is None or e.student_no in only:
            by_stu[e.student_no].append(e)
    nos = list(by_stu)
    students = {s.student_no: s for s in _chunked(db, Student, Student.student_no, nos)}
    corrs: dict[str, list[GradeCorrection]] = defaultdict(list)
    for c in _chunked(db, GradeCorrection, GradeCorrection.student_no, nos):
        corrs[c.student_no].append(c)
    snaps = {s.student_no: s for s in _chunked(
        db, StandardSnapshot, StandardSnapshot.student_no, nos,
        extra=StandardSnapshot.semester_id == semester.id)}
    changed = []
    for no, es in by_stu.items():
        r = judge(no, semester, enrollments=es, student=students.get(no), corrs=corrs.get(no, []))
        snap = snaps.get(no)
        if snap is None:
            snap = StandardSnapshot(semester_id=semester.id, student_no=no)
            db.add(snap)
        elif snap.band != r["band"] and snap.band is not None:
            changed.append({"student_no": no, "old": snap.band, "new": r["band"]})
            snap.changed_notice = True
        snap.grade_num, snap.band, snap.source = r["grade_num"], r["band"], r["source"]
        snap.detail, snap.pending, snap.pending_reasons = r["detail"], r["pending"], r["reasons"]
        snap.decided_by, snap.decided_at = operator, now()
    db.flush()
    return {"students": len(by_stu), "changed": changed}


def _chunked(db, model, col, keys, extra=None, size=500):
    out = []
    keys = list(keys)
    for i in range(0, len(keys), size):
        q = select(model).where(col.in_(keys[i:i + size]))
        if extra is not None:
            q = q.where(extra)
        out.extend(db.scalars(q).all())
    return out
