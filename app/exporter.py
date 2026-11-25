"""Excel 导出与批量导入模板。"""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import scoring
from .models import Semester, TeachingClass, now


def _status_text(d):
    return d["status_display"] or ("" if d["status"] == "正常" else d["status"])


def score_headers(cfg):
    heads = ["学年学期", "教学班号", "教学班名称", "学号", "姓名", "性别", "学院", "专业", "上课班级", "当前年级", "适用标准档"]
    for slot in cfg["slots"]:
        lab = slot.get("label", slot["key"])
        heads += [f"{lab}项目", f"{lab}原始成绩", f"{lab}特殊状态", f"{lab}单项得分（含加分）", f"{lab}加分",
                  f"{lab}折算分"]
    heads += ["体测小计", "运动技能评定", "平时成绩", "课程总成绩", "成绩状态", "备注"]
    return heads


def row_values(sem: Semester, c: TeachingClass, r: dict, cfg) -> list:
    vals = [sem.name, c.class_no, c.class_name, r["student_no"], r["name"], r["gender"], r["college"], r["major"],
            r["class_label"], f"大{r['grade_num']}" if r["grade_num"] else "待确认", r["band_label"]]
    statuses = []
    for slot in cfg["slots"]:
        d = r["display"][slot["key"]]
        st = _status_text(d)
        if st:
            statuses.append(f"{d['item_label'] or slot.get('label')}{st}")
        vals += [d["item_label"], d["raw_text"], st, d["score"], d["bonus"] or None, d["folded"]]
    course = r.get("course_status") or "正常"
    if course != "正常":
        statuses.insert(0, f"课程{course}")
    overall = "正常" if not statuses else "；".join(statuses)
    if not r["complete"] and not statuses:
        overall = "未录完"
    vals += [r["subtotal"], r["values"]["skill"], r["values"]["regular"], total_cell(r), overall,
             r["values"].get("note") or ""]
    return vals


def total_cell(r: dict):
    """总成绩格：课程成绩标了缓考／缺考／退课／休学时直接写状态。"""
    course = r.get("course_status") or "正常"
    return course if course != "正常" else r["total"]


def build_workbook(sem: Semester, sections: list[tuple[TeachingClass, list[dict], dict]], exporter: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "成绩"
    cfg0 = sections[0][2] if sections else sem.config
    heads = score_headers(cfg0)
    ws.append(heads)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDEBF7")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for c, rows, cfg in sections:
        for r in rows:
            ws.append(row_values(sem, c, r, cfg))
    for i, h in enumerate(heads, start=1):
        ws.column_dimensions[get_column_letter(i)].width = max(10, min(24, len(h) * 2))
    total_col = heads.index("课程总成绩") + 1
    for row in ws.iter_rows(min_row=2, min_col=total_col, max_col=total_col):
        for cell in row:
            cell.number_format = "0.0"
    ws.freeze_panes = "F2"
    info = wb.create_sheet("说明")
    info.append(["学年学期", sem.name])
    info.append(["评分表版本", f"{sem.standard_version}（{scoring.load_standard(sem.standard_version)['name']}）"])
    for c, _, cfg in sections[:300]:
        parts = [f"{s.get('label')} {s['weight'] * 100:g}%" for s in cfg["slots"]]
        parts += [f"运动技能评定 {cfg['skill_weight'] * 100:g}%", f"平时成绩 {cfg['regular_weight'] * 100:g}%"]
        info.append([f"权重配置 {c.class_no} {c.class_name}", "，".join(parts)])
    from .services import excellence
    for c, rows, _ in sections[:300]:
        e = excellence(rows)
        info.append([f"优秀率 {c.class_no} {c.class_name}",
                     f"{e['rate'] * 100:.1f}%（总评≥89.5分 {e['excellent']} 人 / 全班 {e['roster']} 人）"
                     + ("，超过30%" if e["over"] else "")])
    info.append(["导出时间", now().strftime("%Y-%m-%d %H:%M:%S")])
    info.append(["导出人", exporter])
    info.append(["口径", "折算分保留两位小数；体测小计保留两位小数；课程总成绩四舍五入保留一位小数。"
                        "免测不参与换算；缓测计为缺项；课上未测、违纪记0分。"])
    info.column_dimensions["A"].width = 36
    info.column_dimensions["B"].width = 90
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


TEMPLATE_HEADS = ["学号", "姓名", "耐力跑/项目一原始成绩", "项目一状态", "立定跳远/项目二原始成绩", "项目二状态",
                  "运动技能评定", "平时成绩"]


def build_template(sem: Semester, c: TeachingClass, rows: list[dict], cfg) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "成绩录入"
    a, b = cfg["slots"][0].get("label", "项目一"), cfg["slots"][1].get("label", "项目二")
    heads = ["学号", "姓名", "性别", f"{a}原始成绩", f"{a}状态", f"{b}原始成绩", f"{b}状态", "运动技能评定", "平时成绩"]
    ws.append(heads)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for r in rows:
        v = r["values"]
        ws.append([r["student_no"], r["name"], r["gender"], r["display"]["a"]["raw_text"] or None,
                   None if v["a_status"] == "正常" else v["a_status"], r["display"]["b"]["raw_text"] or None,
                   None if v["b_status"] == "正常" else v["b_status"], v["skill"], v["regular"]])
    for i in range(1, len(heads) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 16
    for row in ws.iter_rows(min_row=2, max_col=1):
        for cell in row:
            cell.number_format = "@"
    note = wb.create_sheet("填写说明")
    for line in [f"{sem.name} {c.class_name}（{c.class_no}）", "按学号匹配，请勿修改学号列。",
                 "耐力跑可写 3'40\"、3:40、3分40秒、340、220秒；立定跳远写厘米数。",
                 "状态留空表示正常实测；可填 免测／缓测／课上未测／违纪。",
                 f"运动技能评定 0—{cfg['skill_weight'] * 100:g} 分，平时成绩 0—{cfg['regular_weight'] * 100:g} 分，支持0.5分步进。",
                 "空单元格表示不修改该项。"]:
        note.append([line])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- 教师导出本班（照网页列顺序）

def _head_style(cell):
    cell.font = Font(bold=True)
    cell.fill = PatternFill("solid", fgColor="DDEBF7")
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def build_class_workbook(sem: Semester, c: TeachingClass, rows: list[dict], cfg, exporter: str) -> bytes:
    """序号｜学号｜性别｜姓名｜总成绩｜项目一（成绩、得分）｜项目二（成绩、得分）｜体测小计｜运动技能｜平时成绩。

    rows 已按网页上选的排序排好。成绩格写网页上看到的文字（如 3'40"），特殊情况写状态。
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "成绩"
    names = []
    for slot in cfg["slots"]:
        its = list(dict.fromkeys(slot["items"].values()))
        names.append("/".join(scoring.item_label(it) for it in its) or slot.get("label", slot["key"]))
    top = ["序号", "学号", "性别", "姓名", "总成绩", names[0], None, names[1], None, "体测小计", "运动技能", "平时成绩"]
    ws.append(top)
    ws.append([None] * 5 + ["成绩", "得分", "成绩", "得分"] + [None] * 3)
    for col in (1, 2, 3, 4, 5, 10, 11, 12):
        ws.merge_cells(start_row=1, start_column=col, end_row=2, end_column=col)
    ws.merge_cells(start_row=1, start_column=6, end_row=1, end_column=7)
    ws.merge_cells(start_row=1, start_column=8, end_row=1, end_column=9)
    for row in ws.iter_rows(min_row=1, max_row=2):
        for cell in row:
            _head_style(cell)
    for i, r in enumerate(rows, start=1):
        line = [i, r["student_no"], r["gender"], r["name"], total_cell(r)]
        for slot in cfg["slots"]:
            d = r["display"][slot["key"]]
            st = r["values"].get(f"{slot['key']}_status") or "正常"
            raw = st if st != "正常" else (d["raw_text"] or None)
            line += [raw, d["score"]]
        line += [r["subtotal"], r["values"]["skill"], r["values"]["regular"]]
        ws.append(line)
    for row in ws.iter_rows(min_row=3, min_col=5, max_col=5):
        for cell in row:
            cell.number_format = "0.0"
    for row in ws.iter_rows(min_row=3, min_col=10, max_col=10):
        for cell in row:
            cell.number_format = "0.00"
    for row in ws.iter_rows(min_row=3, min_col=2, max_col=2):
        for cell in row:
            cell.number_format = "@"
    for i, w in enumerate([6, 13, 6, 10, 8, 10, 7, 10, 7, 9, 9, 9], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "C3"
    info = wb.create_sheet("说明")
    info.append(["学年学期", sem.name])
    info.append(["教学班", f"{c.class_name}（{c.class_no}）"])
    info.append(["授课教师", c.teacher_name])
    parts = [f"{s.get('label')} {s['weight'] * 100:g}%" for s in cfg["slots"]]
    parts += [f"运动技能评定 {cfg['skill_weight'] * 100:g}%", f"平时成绩 {cfg['regular_weight'] * 100:g}%"]
    info.append(["权重", "，".join(parts)])
    info.append(["评分表版本", f"{sem.standard_version}（{scoring.load_standard(sem.standard_version)['name']}）"])
    from .services import excellence
    e = excellence(rows)
    info.append([f"优秀率 {c.class_no} {c.class_name}",
                 f"{e['rate'] * 100:.1f}%（总评≥89.5分 {e['excellent']} 人 / 全班 {e['roster']} 人）"
                 + ("，超过30%" if e["over"] else "")])
    info.append(["导出时间", now().strftime("%Y-%m-%d %H:%M:%S")])
    info.append(["导出人", exporter])
    info.append(["口径", "折算分保留两位小数；体测小计保留两位小数；课程总成绩四舍五入保留一位小数。"
                        "课程成绩标了缓考、缺考、退课、休学的，总成绩格写状态。"])
    info.column_dimensions["A"].width = 24
    info.column_dimensions["B"].width = 90
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- 全校体测成绩表

RUN_ITEMS = {"run1000", "run800"}


def pe_value(item: str | None, raw):
    """体测成绩格式：长跑 3.56（分.秒，存文本）、跳远 256、50 米 5.6、引体/仰卧 14。"""
    if raw is None or item is None:
        return None
    if item in RUN_ITEMS:
        sec = round(raw, 1)
        m = int(sec // 60)
        s = round(sec - m * 60, 1)
        return f"{m}.{int(s):02d}" if s == int(s) else f"{m}.{s:04.1f}"
    if item == "run50":
        return round(raw, 1)
    return int(raw) if float(raw).is_integer() else raw


def pe_header(slot: dict) -> str:
    its = set(slot["items"].values())
    if its <= RUN_ITEMS:
        return "长跑"
    if its <= {"pull_up", "sit_up"} and len(its) > 1:
        return "引体向上/仰卧起坐"
    if len(its) == 1:
        return scoring.item_label(next(iter(its)))
    return slot.get("label") or slot["key"]


def build_pe_workbook(sem: Semester, sections: list[tuple[TeachingClass, list[dict], dict]]) -> bytes:
    """教学班号｜学号｜姓名｜性别｜两项体测成绩，按学号排序；特殊情况直接写状态。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "体测成绩"
    heads = ["教学班号", "学号", "姓名", "性别"] + [pe_header(s) for s in sem.config["slots"]]
    ws.append(heads)
    for cell in ws[1]:
        _head_style(cell)
    lines = []
    for c, rows, cfg in sections:
        for r in rows:
            line = [c.class_no, r["student_no"], r["name"], r["gender"]]
            for slot in cfg["slots"]:
                d = r["display"][slot["key"]]
                st = r["values"].get(f"{slot['key']}_status") or "正常"
                line.append(st if st != "正常" else pe_value(d["item"], r["values"].get(f"{slot['key']}_raw")))
            lines.append(line)
    lines.sort(key=lambda x: (str(x[1]), str(x[0])))
    for line in lines:
        ws.append(line)
    for row in ws.iter_rows(min_row=2, max_col=2):
        for cell in row:
            cell.number_format = "@"
    for row in ws.iter_rows(min_row=2, min_col=5, max_col=len(heads)):
        for cell in row:
            if isinstance(cell.value, str):
                cell.number_format = "@"
            elif isinstance(cell.value, float):
                cell.number_format = "0.0"
    for i, w in enumerate([14, 14, 10, 6] + [12] * (len(heads) - 4), start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "C2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
