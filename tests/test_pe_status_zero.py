"""体测特殊情况改名（课上未测）与旧数据迁移、模板上传认旧写法、体测填 0 的提醒、课程成绩休学。"""
import io

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import Enrollment, ScoreHistory, ScoreRecord, SessionLocal, TeachingClass, _migrate_data, init_db
from tests.test_api import do_import, xlsx

T = "T2040"
SEM = "2040-2041学年秋季学期"
NOS = ["2040100001", "2040100002", "2040100003", "2040100004"]
BASE = [["学号", "姓名", "性别", "院系所名称", "班级", "年级", "学校", "专业"]] + [
    [no, f"学生{i}", "男", "法学院", None, "2040", "人大", None] for i, no in enumerate(NOS, 1)]
SCHED = [["学年学期", "教学班号", "课程性质", "课程编号", "课程名称", "开课单位", "教学班名称", "授课对象", "授课教师", "教工号",
          "周次", "星期", "节次", "上课地点", "选课人数"],
         [SEM, "Z001", "公共体育", "21000999", "网球", "体育部", "网球1班", "", "周老师", T, "1-16周", "星期四", "0304", "世纪馆", "4"]]
ENROLL = [["学年学期", "学号", "姓名", "上课班级", "上课专业", "上课年级", "上课院系", "教学班号", "教学班名称", "上课教师",
           "课程名称", "课程类别", "上课校区"]] + [
    ["2040-2041-1", no, f"学生{i}", "2040法学1班", "法学", "2040", "法学院", "Z001", "网球1班", "周老师", "网球", "公共体育",
     "中关村校区"] for i, no in enumerate(NOS, 1)]


@pytest.fixture(scope="module")
def env():
    init_db()
    admin = TestClient(app)
    assert admin.post("/api/auth/dev-login", json={"staff_no": "admin01"}).status_code == 200
    sems = {s["code"]: s for s in admin.get("/api/semesters").json()}
    sem = sems.get("2040-2041-1") or admin.post("/api/admin/semesters", json={"start_year": 2040, "season": "fall"}).json()
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


def test_statuses_offered_and_old_ones_mapped(env):
    t, cid = env["teacher"], env["cid"]
    d = t.get(f"/api/classes/{cid}").json()
    assert d["statuses"] == ["正常", "免测", "缓测", "课上未测", "违纪"]
    ids = {no: r["enrollment_id"] for no, r in rows_of(t, cid).items()}
    res = t.post(f"/api/classes/{cid}/cells", json={"changes": [
        {"enrollment_id": ids[NOS[0]], "field": "a_status", "value": "课上未测"},
        {"enrollment_id": ids[NOS[1]], "field": "a_status", "value": "缺考"},   # 旧写法照样认，存成新写法
        {"enrollment_id": ids[NOS[2]], "field": "a_status", "value": "作弊"},
        {"enrollment_id": ids[NOS[3]], "field": "a_status", "value": "请假"}]}).json()
    assert [x["ok"] for x in res["results"]] == [True, True, True, False]
    rows = rows_of(t, cid)
    assert rows[NOS[0]]["values"]["a_status"] == "课上未测" and rows[NOS[0]]["display"]["a"]["score"] == 0
    assert rows[NOS[1]]["values"]["a_status"] == "课上未测"
    assert rows[NOS[2]]["values"]["a_status"] == "违纪"
    t.post(f"/api/classes/{cid}/cells", json={"changes": [
        {"enrollment_id": ids[no], "field": "a_status", "value": "正常"} for no in NOS[:3]]})


def test_migrate_old_statuses(env):
    """库里已有的缺考改成课上未测、作弊改成违纪，备注不动，修改记录不改；可重复执行。"""
    t, cid = env["teacher"], env["cid"]
    ids = {no: r["enrollment_id"] for no, r in rows_of(t, cid).items()}
    t.post(f"/api/classes/{cid}/cells", json={"changes": [
        {"enrollment_id": ids[NOS[0]], "field": "b_raw", "value": "250"},
        {"enrollment_id": ids[NOS[1]], "field": "note", "value": "替考"}]})
    with SessionLocal() as db:
        r0 = db.scalar(select(ScoreRecord).where(ScoreRecord.enrollment_id == ids[NOS[0]]))
        r1 = db.scalar(select(ScoreRecord).where(ScoreRecord.enrollment_id == ids[NOS[1]]))
        r0.a_status, r1.b_status = "缺考", "作弊"
        db.add(ScoreHistory(enrollment_id=ids[NOS[0]], field="a_status", old="正常", new="缺考", operator="old"))
        db.get(TeachingClass, cid).status = "已提交"
        db.commit()
    _migrate_data()
    _migrate_data()
    rows = rows_of(t, cid)
    assert rows[NOS[0]]["values"]["a_status"] == "课上未测" and rows[NOS[0]]["display"]["a"]["score"] == 0
    assert rows[NOS[1]]["values"]["b_status"] == "违纪" and rows[NOS[1]]["values"]["note"] == "替考"
    with SessionLocal() as db:
        assert db.get(TeachingClass, cid).status == "已提交"  # 迁移不动班级的提交状态
        assert db.scalar(select(ScoreHistory).where(ScoreHistory.enrollment_id == ids[NOS[0]],
                                                    ScoreHistory.operator == "old")).new == "缺考"
    assert t.post(f"/api/classes/{cid}/withdraw").status_code == 200
    t.post(f"/api/classes/{cid}/cells", json={"changes": [
        {"enrollment_id": ids[NOS[0]], "field": "a_status", "value": "正常"},
        {"enrollment_id": ids[NOS[1]], "field": "b_status", "value": "正常"},
        {"enrollment_id": ids[NOS[1]], "field": "note", "value": ""}]})


def test_template_upload_reads_old_names(env):
    t, cid = env["teacher"], env["cid"]
    tpl = openpyxl.load_workbook(io.BytesIO(t.get(f"/api/classes/{cid}/template").content))
    ws = tpl.worksheets[0]
    head = [c.value for c in ws[1]]
    assert any("课上未测" in str(c.value) for c in ws["A"])  # 底部说明列出体测特殊情况的写法
    col = {h: i for i, h in enumerate(head)}
    a_st = head[3]  # 体测特殊情况直接写在成绩格里
    for r in ws.iter_rows(min_row=2):
        no = r[col["学号"]].value
        if no == NOS[2]:
            r[col[a_st]].value = "缺考"
        elif no == NOS[3]:
            r[col[a_st]].value = "作弊"
    buf = io.BytesIO()
    tpl.save(buf)
    up = t.post(f"/api/classes/{cid}/upload", files={"file": ("t.xlsx", buf.getvalue())}).json()
    assert up["errors"] == [] and len(up["fills"]) + len(up["conflicts"]) >= 2
    assert t.post(f"/api/classes/{cid}/upload/commit", json={"token": up["token"]}).status_code == 200
    rows = rows_of(t, cid)
    assert rows[NOS[2]]["values"]["a_status"] == "课上未测" and rows[NOS[3]]["values"]["a_status"] == "违纪"
    ids = {no: r["enrollment_id"] for no, r in rows.items()}
    t.post(f"/api/classes/{cid}/cells", json={"changes": [
        {"enrollment_id": ids[no], "field": "a_status", "value": "正常"} for no in NOS[2:]]})


def test_zero_scores_listed_and_marking_missed(env):
    t, admin, cid, sid = env["teacher"], env["admin"], env["cid"], env["sid"]
    ids = {no: r["enrollment_id"] for no, r in rows_of(t, cid).items()}
    res = t.post(f"/api/classes/{cid}/cells", json={"changes": [
        {"enrollment_id": ids[NOS[0]], "field": "b_raw", "value": "0"},
        {"enrollment_id": ids[NOS[1]], "field": "b_raw", "value": "0"},
        {"enrollment_id": ids[NOS[2]], "field": "b_raw", "value": "180"}]}).json()
    assert all(x["ok"] for x in res["results"])
    # 保存后返回最新的 0 分清单，录入页据此刷新常驻提示
    assert {z["student_no"] for z in res["pe_zeros"]} == {NOS[0], NOS[1]}
    zs = t.get(f"/api/classes/{cid}").json()["pe_zeros"]
    assert [(z["student_no"], [i["key"] for i in z["items"]]) for z in zs] == [(NOS[0], ["b"]), (NOS[1], ["b"])]
    assert zs[0]["items"][0]["label"] == "立定跳远"
    ov = admin.get(f"/api/admin/semesters/{sid}/overview").json()
    assert next(c for c in ov["class_list"] if c["id"] == cid)["zeros"] == 2
    # 点“标为课上未测”：清掉这格成绩、状态改为课上未测、留痕，0 分清单里不再有他
    res = t.post(f"/api/classes/{cid}/cells", json={"changes": [
        {"enrollment_id": ids[NOS[0]], "field": "b_status", "value": "课上未测"}]}).json()
    assert res["results"][0]["ok"] and [z["student_no"] for z in res["pe_zeros"]] == [NOS[1]]
    row = rows_of(t, cid)[NOS[0]]
    assert row["values"]["b_raw"] is None and row["values"]["b_status"] == "课上未测" and row["display"]["b"]["score"] == 0
    h = t.get(f"/api/classes/{cid}/history/{ids[NOS[0]]}").json()["history"]
    assert any(x["field"] == "b_raw" and x["old"] == "0.0" and x["new"] is None for x in h)


def test_course_status_leave_of_absence(env):
    """课程成绩特殊情况加了休学：和退课一样总成绩不算、显示状态，技能和平时可以不填。"""
    t, cid = env["teacher"], env["cid"]
    assert "休学" in t.get(f"/api/classes/{cid}").json()["course_statuses"]
    eid = rows_of(t, cid)[NOS[3]]["enrollment_id"]
    res = t.post(f"/api/classes/{cid}/cells", json={"changes": [
        {"enrollment_id": eid, "field": "a_raw", "value": "3:50"},
        {"enrollment_id": eid, "field": "b_raw", "value": "230"},
        {"enrollment_id": eid, "field": "course_status", "value": "休学"}]}).json()
    assert all(x["ok"] for x in res["results"])
    row = rows_of(t, cid)[NOS[3]]
    assert row["course_status"] == "休学" and row["total"] is None and row["complete"]
    wb = openpyxl.load_workbook(io.BytesIO(t.get(f"/api/classes/{cid}/export?sort=no").content))
    body = [[c.value for c in r] for r in wb["成绩"].iter_rows(min_row=3)]
    assert next(b for b in body if b[1] == NOS[3])[4] == "休学"
