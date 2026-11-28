"""教师整班上传：平台里空着的直接写、不一致的让老师逐格选；课程成绩「选课未修」；录入模板版式（状态并入成绩格、示例行、底部说明）。"""
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
A, B, SK, RG = 3, 4, 5, 6  # 新模板列：学号 姓名 性别 项目一 项目二 运动技能评定 平时成绩


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


def reset(t, cid):
    set_cells(t, cid, [(no, f, "正常") for no in NOS for f in ("a_status", "b_status", "course_status")]
              + [(no, f, "") for no in NOS for f in ("a_raw", "b_raw", "skill", "regular")])


def template(t, cid):
    wb = openpyxl.load_workbook(io.BytesIO(t.get(f"/api/classes/{cid}/template").content))
    return wb, wb.worksheets[0]


def fill(ws, edits):
    """edits: {学号: {列号: 值}}"""
    for r in ws.iter_rows(min_row=2):
        for i, v in edits.get(r[0].value, {}).items():
            r[i].value = v


def upload(t, cid, wb):
    buf = io.BytesIO()
    wb.save(buf)
    r = t.post(f"/api/classes/{cid}/upload", files={"file": ("t.xlsx", buf.getvalue())})
    assert r.status_code == 200, r.text
    return r.json()


def commit(t, cid, up, use_table=None):
    body = {"token": up["token"]}
    if use_table is not None:
        body["use_table"] = use_table
    r = t.post(f"/api/classes/{cid}/upload/commit", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def key(xs):
    return {(x["student_no"], x["label"]): x for x in xs}


def test_template_layout(env):
    t, cid = env["teacher"], env["cid"]
    reset(t, cid)
    set_cells(t, cid, [(NOS[0], "a_status", "免测"), (NOS[0], "b_raw", "230"),
                       (NOS[1], "course_status", "选课未修"), (NOS[2], "skill", "50")])
    wb, ws = template(t, cid)
    assert "填写说明" not in wb.sheetnames  # 说明挪到录入表最下面
    head = [c.value for c in ws[1]]
    assert head[:3] == ["学号", "姓名", "性别"] and head[SK:] == ["运动技能评定", "平时成绩"] and len(head) == 7
    assert not any("状态" in h or h.startswith("课程成绩") for h in head)  # 状态并入成绩列
    ex = [c.value for c in ws[2]]
    assert ex == ["示例", "示例", "男", "3'40\"（也可写 3:40、340）", "230", "51（0—60分，可到0.5分）",
                  "8.5（0—10分，可到0.5分）"]
    assert ws.cell(row=2, column=1).fill.fill_type is None and not ws.cell(row=2, column=4).font.italic
    assert ws.freeze_panes is None
    body = {r[0].value: [c.value for c in r] for r in ws.iter_rows(min_row=3, max_row=2 + len(NOS))}
    assert list(body) == NOS
    assert body[NOS[0]][A] == "免测" and body[NOS[0]][B] == "230"          # 和平台显示一致
    assert body[NOS[1]][SK] == body[NOS[1]][RG] == "选课未修" and body[NOS[2]][SK] == 50
    assert ws.cell(row=3 + len(NOS), column=1).value is None
    notes = [ws.cell(row=i, column=1).value for i in range(4 + len(NOS), ws.max_row + 1)]
    assert notes[0].startswith("填写说明") and not any("3'40" in n for n in notes)
    assert any("免测" in n for n in notes) and any("选课未修" in n for n in notes)  # 特殊情况写法在底部说明
    # 原样上传：示例行、说明跳过；和平台一样的格子不显示
    up = upload(t, cid, wb)
    assert up["fills"] == [] and up["conflicts"] == [] and up["errors"] == [] and up["unknown"] == []


def test_upload_fills_empty_and_asks_on_differences(env):
    t, cid = env["teacher"], env["cid"]
    reset(t, cid)
    s1, s2 = NOS[0], NOS[1]
    set_cells(t, cid, [(s1, "a_raw", "3:50"), (s1, "b_raw", "230"), (s1, "skill", "50"), (s1, "regular", "8")])
    wb, ws = template(t, cid)
    fill(ws, {s1: {A: "3'40\"", B: 220, SK: 50, RG: 9},   # 技能一样不显示
              s2: {A: "4:00", SK: 40}})                    # 平台里空着：直接写
    up = upload(t, cid, wb)
    assert up["errors"] == []
    assert set(key(up["fills"])) == {(s2, "耐力跑"), (s2, "运动技能评定")}
    cf = key(up["conflicts"])
    assert set(cf) == {(s1, "耐力跑"), (s1, "立定跳远"), (s1, "平时成绩")}
    assert cf[(s1, "耐力跑")]["sys"] == "3'50\"" and cf[(s1, "耐力跑")]["default"] == "table"   # 表里跑得快
    assert cf[(s1, "立定跳远")]["default"] == "system"                                        # 平台跳得远
    # 老师选：耐力跑用表里的，跳远、平时保留平台的
    res = commit(t, cid, up, use_table=[cf[(s1, "耐力跑")]["id"]])
    assert res["saved"] == 3 and res["kept"] == 2 and res["failed"] == []
    v1, v2 = rows_of(t, cid)[s1]["values"], rows_of(t, cid)[s2]["values"]
    assert v1["a_raw"] == 220 and v1["b_raw"] == 230 and v1["regular"] == 8
    assert v2["a_raw"] == 240 and v2["skill"] == 40
    assert t.post(f"/api/classes/{cid}/upload/{res['batch']}/revert").status_code == 200
    assert rows_of(t, cid)[s1]["values"]["a_raw"] == 230 and rows_of(t, cid)[s2]["values"]["a_raw"] is None


def test_upload_default_choice_when_not_given(env):
    """不传选择时按预检的默认：表里更好的用表里的，平台更好的保留平台的。"""
    t, cid = env["teacher"], env["cid"]
    reset(t, cid)
    s = NOS[4]
    set_cells(t, cid, [(s, "a_raw", "3:30"), (s, "b_raw", "200")])
    wb, ws = template(t, cid)
    fill(ws, {s: {A: "3:45", B: 245}})
    up = upload(t, cid, wb)
    cf = key(up["conflicts"])
    assert cf[(s, "耐力跑")]["default"] == "system" and cf[(s, "立定跳远")]["default"] == "table"
    commit(t, cid, up)
    v = rows_of(t, cid)[s]["values"]
    assert v["a_raw"] == 210 and v["b_raw"] == 245


def test_upload_status_in_score_cells(env):
    t, cid = env["teacher"], env["cid"]
    reset(t, cid)
    s1, s2, s3 = NOS[0], NOS[1], NOS[2]
    set_cells(t, cid, [(s1, "a_status", "免测"), (s2, "b_raw", "230"), (s3, "skill", "50"), (s3, "regular", "8")])
    wb, ws = template(t, cid)
    fill(ws, {s1: {A: "3:35"},                      # 平台免测、表里写成绩
              s2: {B: "缓测", SK: "选课未修"},       # 平台有成绩、表里写特殊情况；课程成绩平台里空着
              s3: {SK: "退课", RG: "退课"}})          # 平台有技能平时、表里写课程成绩特殊情况
    up = upload(t, cid, wb)
    assert up["errors"] == []
    cf = key(up["conflicts"])
    assert set(cf) == {(s1, "耐力跑"), (s2, "立定跳远")}
    assert cf[(s1, "耐力跑")]["sys"] == "免测" and cf[(s1, "耐力跑")]["new"] == "3:35"
    assert cf[(s2, "立定跳远")]["sys"] == "230" and cf[(s2, "立定跳远")]["new"] == "缓测"
    fl = key(up["fills"])
    assert fl[(s2, "课程成绩")]["new"] == "选课未修" and fl[(s3, "课程成绩")]["new"] == "退课"
    commit(t, cid, up, use_table=[x["id"] for x in up["conflicts"]])
    rows = rows_of(t, cid)
    assert rows[s1]["values"]["a_status"] == "正常" and rows[s1]["values"]["a_raw"] == 215
    assert rows[s2]["values"]["b_status"] == "缓测" and rows[s2]["values"]["b_raw"] is None
    assert rows[s2]["course_status"] == "选课未修" and rows[s3]["course_status"] == "退课"
    # 平台里是选课未修、表里技能写了分数：要老师选，选表里的就改回正常
    wb, ws = template(t, cid)
    fill(ws, {s2: {SK: 45, RG: None}})
    up = upload(t, cid, wb)
    cf = key(up["conflicts"])
    assert cf[(s2, "运动技能评定")]["sys"] == "选课未修"
    commit(t, cid, up, use_table=[cf[(s2, "运动技能评定")]["id"]])
    r = rows_of(t, cid)[s2]
    assert r["course_status"] == "正常" and r["values"]["skill"] == 45


def test_upload_course_status_mixed_is_error(env):
    t, cid = env["teacher"], env["cid"]
    reset(t, cid)
    wb, ws = template(t, cid)
    fill(ws, {NOS[0]: {SK: "选课未修", RG: 9}, NOS[1]: {SK: "退课", RG: "休学"}})
    up = upload(t, cid, wb)
    assert {e["student_no"] for e in up["errors"]} == {NOS[0], NOS[1]}
    assert up["fills"] == [] and up["conflicts"] == []


def test_course_status_not_taken(env):
    """选课未修：和退课、休学一样总成绩不算、显示状态，技能平时可不填，导出总成绩格写状态。"""
    t, cid = env["teacher"], env["cid"]
    reset(t, cid)
    assert "选课未修" in t.get(f"/api/classes/{cid}").json()["course_statuses"]
    s = NOS[1]
    set_cells(t, cid, [(s, "a_raw", "3:50"), (s, "b_raw", "230"), (s, "course_status", "选课未修")])
    row = rows_of(t, cid)[s]
    assert row["course_status"] == "选课未修" and row["total"] is None and row["complete"]
    wb = openpyxl.load_workbook(io.BytesIO(t.get(f"/api/classes/{cid}/export?sort=no").content))
    body = [[c.value for c in r] for r in wb["成绩"].iter_rows(min_row=3)]
    assert next(b for b in body if b[1] == s)[4] == "选课未修"


def test_old_template_still_uploads(env):
    """老版式：第1行表头、第2行起就是学生，体测状态单独成列，填写说明在另一页。"""
    t, cid = env["teacher"], env["cid"]
    reset(t, cid)
    old = openpyxl.Workbook()
    o = old.active
    o.append(["学号", "姓名", "性别", "耐力跑原始成绩", "耐力跑状态", "立定跳远原始成绩", "立定跳远状态", "运动技能评定", "平时成绩"])
    for no in NOS:
        o.append([no, "x", "男", None, "缺考" if no == NOS[0] else None, 231 if no == NOS[1] else None,
                  None, 55 if no == NOS[1] else None, None])
    old.create_sheet("填写说明").append(["按学号匹配，请勿修改学号列。"])
    up = upload(t, cid, old)
    assert up["errors"] == [] and up["unknown"] == [] and up["conflicts"] == []
    assert set(key(up["fills"])) == {(NOS[1], "立定跳远"), (NOS[1], "运动技能评定")} | (
        {(NOS[0], "耐力跑")})
    commit(t, cid, up)
    rows = rows_of(t, cid)
    assert rows[NOS[0]]["values"]["a_status"] == "课上未测"
    assert rows[NOS[1]]["values"]["b_raw"] == 231 and rows[NOS[1]]["values"]["skill"] == 55
