"""教师整班上传取最优、课程成绩「选课未修」、录入模板的示例行和底部填写说明。"""
import io

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import init_db
from tests.test_api import do_import, xlsx

T = "T2041"
SEM = "2041-2042学年秋季学期"
NOS = ["2041100001", "2041100002", "2041100003", "2041100004", "2041100005"]
BASE = [["学号", "姓名", "性别", "院系所名称", "班级", "年级", "学校", "专业"]] + [
    [no, f"学生{i}", "男", "法学院", None, "2041", "人大", None] for i, no in enumerate(NOS, 1)]
SCHED = [["学年学期", "教学班号", "课程性质", "课程编号", "课程名称", "开课单位", "教学班名称", "授课对象", "授课教师", "教工号",
          "周次", "星期", "节次", "上课地点", "选课人数"],
         [SEM, "Y001", "公共体育", "21000998", "篮球", "体育部", "篮球1班", "", "吴老师", T, "1-16周", "星期二", "0304", "世纪馆", "5"]]
ENROLL = [["学年学期", "学号", "姓名", "上课班级", "上课专业", "上课年级", "上课院系", "教学班号", "教学班名称", "上课教师",
           "课程名称", "课程类别", "上课校区"]] + [
    ["2041-2042-1", no, f"学生{i}", "2041法学1班", "法学", "2041", "法学院", "Y001", "篮球1班", "吴老师", "篮球", "公共体育",
     "中关村校区"] for i, no in enumerate(NOS, 1)]


@pytest.fixture(scope="module")
def env():
    init_db()
    admin = TestClient(app)
    assert admin.post("/api/auth/dev-login", json={"staff_no": "admin01"}).status_code == 200
    sems = {s["code"]: s for s in admin.get("/api/semesters").json()}
    sem = sems.get("2041-2042-1") or admin.post("/api/admin/semesters", json={"start_year": 2041, "season": "fall"}).json()
    sid = sem["id"]
    admin.patch(f"/api/admin/semesters/{sid}", json={"entry_open": True})
    do_import(admin, "base", xlsx(BASE), sid)
    do_import(admin, "schedule", xlsx(SCHED), sid)
    do_import(admin, "enroll", xlsx(ENROLL), sid)
    t = TestClient(app)
    assert t.post("/api/auth/dev-login", json={"staff_no": T}).status_code == 200
    cid = next(c["id"] for c in t.get(f"/api/semesters/{sid}/classes").json()["classes"])
    return {"admin": admin, "teacher": t, "sid": sid, "cid": cid}


def rows_of(client, cid):
    return {r["student_no"]: r for r in client.get(f"/api/classes/{cid}").json()["rows"]}


def set_cells(t, cid, cells):
    ids = {no: r["enrollment_id"] for no, r in rows_of(t, cid).items()}
    res = t.post(f"/api/classes/{cid}/cells", json={"changes": [
        {"enrollment_id": ids[no], "field": f, "value": v} for no, f, v in cells]}).json()
    assert all(x["ok"] for x in res["results"]), res


def template(t, cid):
    wb = openpyxl.load_workbook(io.BytesIO(t.get(f"/api/classes/{cid}/template").content))
    ws = wb.worksheets[0]
    col = {h: i for i, h in enumerate(c.value for c in ws[1])}
    return wb, ws, col


def fill(ws, col, edits):
    """edits: {学号: {表头: 值}}"""
    for r in ws.iter_rows(min_row=2):
        for h, v in edits.get(r[0].value, {}).items():
            r[col[h]].value = v


def upload(t, cid, wb):
    buf = io.BytesIO()
    wb.save(buf)
    r = t.post(f"/api/classes/{cid}/upload", files={"file": ("t.xlsx", buf.getvalue())})
    assert r.status_code == 200, r.text
    return r.json()


def commit(t, cid, up):
    r = t.post(f"/api/classes/{cid}/upload/commit", json={"token": up["token"]})
    assert r.status_code == 200, r.text
    return r.json()


def test_template_layout_example_and_notes(env):
    t, cid = env["teacher"], env["cid"]
    wb, ws, col = template(t, cid)
    assert "填写说明" not in wb.sheetnames  # 说明挪到录入表最下面，不再单独一页
    head = [c.value for c in ws[1]]
    assert head[0] == "学号" and head[-1] == "课程成绩特殊情况"
    ex = [c.value for c in ws[2]]
    assert ex[0] == "示例" and ws.cell(row=2, column=1).fill.fgColor.rgb.endswith("FFF2CC")
    assert "3'40\"" in ex[col["耐力跑原始成绩"]] if "耐力跑原始成绩" in col else True
    assert "选课未修" in ex[col["课程成绩特殊情况"]] and "课上未测" in ex[4]
    nos = [ws.cell(row=i, column=1).value for i in range(3, 3 + len(NOS))]
    assert nos == NOS
    assert ws.cell(row=3 + len(NOS), column=1).value is None  # 名单下面空一行
    notes = [ws.cell(row=i, column=1).value for i in range(4 + len(NOS), ws.max_row + 1)]
    assert notes[0].startswith("填写说明") and any("取用时短" in n for n in notes)
    assert not any("3'40" in n for n in notes)  # 示例里写过的写法不再重复
    # 原样上传：示例行和说明都被跳过，没有要改的、没有报错、没有“学号不在本班”
    up = upload(t, cid, wb)
    assert up["cells"] == 0 and up["errors"] == [] and up["unknown"] == [] and up["skipped"] == []


def test_upload_keeps_better_scores(env):
    t, cid = env["teacher"], env["cid"]
    s1, s2 = NOS[0], NOS[1]
    set_cells(t, cid, [(s1, "a_raw", "3:50"), (s1, "b_raw", "230"), (s1, "skill", "50"), (s1, "regular", "8")])
    wb, ws, col = template(t, cid)
    la = next(h for h in col if h.endswith("原始成绩"))
    lb = [h for h in col if h.endswith("原始成绩")][1]
    fill(ws, col, {s1: {la: "3'40\"", lb: 220, "运动技能评定": 45, "平时成绩": 9},
                   s2: {la: "4:00", "运动技能评定": 40}})
    up = upload(t, cid, wb)
    assert up["errors"] == []
    got = {(x["student_no"], x["field"]): x for x in up["updates"]}
    # 跑步用时短的更好；系统里没有的直接写
    assert set(got) == {(s1, "a_raw"), (s1, "regular"), (s2, "a_raw"), (s2, "skill")}
    assert got[(s1, "a_raw")]["old"] == "3'50\""
    sk = {(x["student_no"], x["field"]): x for x in up["skipped"]}
    assert set(sk) == {(s1, "b_raw"), (s1, "skill")}  # 跳远取大的、技能取高的
    assert all(x["reason"] == "系统已有更好成绩" for x in sk.values()) and up["better_kept"] == 2
    assert up["cells"] == 4 and up["students"] == 2
    cm = commit(t, cid, up)
    rows = rows_of(t, cid)
    v1, v2 = rows[s1]["values"], rows[s2]["values"]
    assert v1["a_raw"] == 220 and v1["b_raw"] == 230 and v1["skill"] == 50 and v1["regular"] == 9
    assert v2["a_raw"] == 240 and v2["skill"] == 40
    # 整批回退照旧
    assert t.post(f"/api/classes/{cid}/upload/{cm['batch']}/revert").status_code == 200
    assert rows_of(t, cid)[s1]["values"]["a_raw"] == 230


def test_upload_lower_jump_and_pull_direction(env):
    """跳远：表里更远才写；跑步：表里更慢不写。"""
    t, cid = env["teacher"], env["cid"]
    s = NOS[4]
    set_cells(t, cid, [(s, "a_raw", "3:30"), (s, "b_raw", "200")])
    wb, ws, col = template(t, cid)
    la, lb = [h for h in col if h.endswith("原始成绩")]
    fill(ws, col, {s: {la: "3:45", lb: 245}})
    up = upload(t, cid, wb)
    assert [(x["field"], x["new"]) for x in up["updates"]] == [("b_raw", "245")]
    assert [(x["field"], x["current"]) for x in up["skipped"]] == [("a_raw", "3'30\"")]


def test_upload_status_columns_override(env):
    t, cid = env["teacher"], env["cid"]
    s3, s4, s1 = NOS[2], NOS[3], NOS[0]
    set_cells(t, cid, [(s3, "a_status", "免测"), (s4, "a_status", "免测"), (s1, "b_raw", "230")])
    wb, ws, col = template(t, cid)
    la, lb = [h for h in col if h.endswith("原始成绩")]
    sa, sb = [h for h in col if h.endswith("状态")]
    fill(ws, col, {
        s3: {la: "3:30"},                       # 系统里免测、表里状态格还是免测：成绩不写
        s4: {sa: "正常", la: "3:35"},            # 表里把状态改回正常：状态和成绩都写
        s1: {sb: "缓测", "课程成绩特殊情况": "选课未修"},  # 状态列填了就覆盖，即使系统里有成绩
    })
    up = upload(t, cid, wb)
    assert up["errors"] == []
    sk = {(x["student_no"], x["field"]): x["reason"] for x in up["skipped"]}
    assert sk == {(s3, "a_raw"): "该项状态是“免测”，成绩不写入"}
    fields = [(x["student_no"], x["field"]) for x in up["updates"]]
    assert fields.index((s4, "a_status")) < fields.index((s4, "a_raw"))
    assert (s1, "b_status") in fields and (s1, "course_status") in fields
    commit(t, cid, up)
    rows = rows_of(t, cid)
    assert rows[s3]["values"]["a_status"] == "免测" and rows[s3]["values"]["a_raw"] is None
    assert rows[s4]["values"]["a_status"] == "正常" and rows[s4]["values"]["a_raw"] == 215
    assert rows[s1]["values"]["b_status"] == "缓测" and rows[s1]["values"]["b_raw"] is None
    assert rows[s1]["course_status"] == "选课未修"
    set_cells(t, cid, [(s1, "course_status", "正常"), (s1, "b_status", "正常"), (s3, "a_status", "正常")])


def test_course_status_not_taken(env):
    """选课未修：和退课、休学一样总成绩不算、显示状态，技能平时可不填，导出总成绩格写状态。"""
    t, cid = env["teacher"], env["cid"]
    assert "选课未修" in t.get(f"/api/classes/{cid}").json()["course_statuses"]
    s = NOS[1]
    set_cells(t, cid, [(s, "a_raw", "3:50"), (s, "b_raw", "230"), (s, "course_status", "选课未修")])
    row = rows_of(t, cid)[s]
    assert row["course_status"] == "选课未修" and row["total"] is None and row["complete"]
    wb = openpyxl.load_workbook(io.BytesIO(t.get(f"/api/classes/{cid}/export?sort=no").content))
    body = [[c.value for c in r] for r in wb["成绩"].iter_rows(min_row=3)]
    assert next(b for b in body if b[1] == s)[4] == "选课未修"
    # 模板里预填，示例行列出这个写法
    _, ws, col = template(t, cid)
    r = next(r for r in ws.iter_rows(min_row=3) if r[0].value == s)
    assert r[col["课程成绩特殊情况"]].value == "选课未修"
    set_cells(t, cid, [(s, "course_status", "正常")])


def test_old_template_still_uploads(env):
    """老版式：第1行表头、第2行起就是学生，没有示例行和课程成绩列，填写说明在另一页。"""
    t, cid = env["teacher"], env["cid"]
    _, ws, col = template(t, cid)
    la, lb = [h for h in col if h.endswith("原始成绩")]
    sa, sb = [h for h in col if h.endswith("状态")]
    s = NOS[3]
    old = openpyxl.Workbook()
    o = old.active
    o.append(["学号", "姓名", "性别", la, sa, lb, sb, "运动技能评定", "平时成绩"])
    for no in NOS:
        o.append([no, "x", "男", None, None, None, None, 55 if no == s else None, None])
    old.create_sheet("填写说明").append(["按学号匹配，请勿修改学号列。"])
    up = upload(t, cid, old)
    assert up["errors"] == [] and up["unknown"] == []
    assert [(x["student_no"], x["field"]) for x in up["updates"]] == [(s, "skill")]
    commit(t, cid, up)
    assert rows_of(t, cid)[s]["values"]["skill"] == 55
