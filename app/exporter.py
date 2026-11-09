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
    overall = "正常" if not statuses else "；".join(statuses)
    if not r["complete"] and not statuses:
        overall = "未录完"
    vals += [r["subtotal"], r["values"]["skill"], r["values"]["regular"], r["total"], overall,
             r["values"].get("note") or ""]
    return vals


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
    info.append(["导出时间", now().strftime("%Y-%m-%d %H:%M:%S")])
    info.append(["导出人", exporter])
    info.append(["口径", "折算分保留两位小数；体测小计保留两位小数；课程总成绩四舍五入保留一位小数。"
                        "免测不参与换算；缓测计为缺项；缺考、作弊、违纪记0分。"])
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
                 "状态留空表示正常实测；可填 免测／缓测／缺考／作弊／违纪。",
                 f"运动技能评定 0—{cfg['skill_weight'] * 100:g} 分，平时成绩 0—{cfg['regular_weight'] * 100:g} 分，支持0.5分步进。",
                 "空单元格表示不修改该项。"]:
        note.append([line])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
