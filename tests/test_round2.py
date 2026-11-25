"""第二轮修改：教师改学生信息与标准档、其他问题、课程成绩特殊情况、体育部锁定体测、公共账号、两份新导出。"""
import io

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import app
from app.models import SessionLocal, User, init_db
from tests.test_api import do_import, xlsx

T = "T2030"
BASE = [["学号", "姓名", "性别", "院系所名称", "班级", "年级", "学校", "专业"],
        ["2030100001", "甲一", "男", "法学院", None, "2030", "人大", None],
        ["2029100002", "乙二", "女", "经济学院", None, "2029", "人大", None],
        ["2027100003", "丙三", "男", "信息学院", None, "2027", "人大", None]]
SCHED = [["学年学期", "教学班号", "课程性质", "课程编号", "课程名称", "开课单位", "教学班名称", "授课对象", "授课教师", "教工号",
          "周次", "星期", "节次", "上课地点", "选课人数"],
         ["2030-2031学年秋季学期", "R001", "公共体育", "21000773", "篮球", "体育部", "篮球1班", "", "王老师", T,
          "1-16周", "星期二", "0304", "世纪馆", "4"],
         ["2030-2031学年秋季学期", "R002", "公共体育", "21000774", "排球", "体育部", "排球1班", "", "外聘老师", "",
          "1-16周", "星期三", "0304", "世纪馆", "1"]]
ENROLL = [["学年学期", "学号", "姓名", "上课班级", "上课专业", "上课年级", "上课院系", "教学班号", "教学班名称", "上课教师",
           "课程名称", "课程类别", "上课校区"],
          ["2030-2031-1", "2030100001", "甲一", "2030法学1班", "法学", "2030", "法学院", "R001", "篮球1班", "王老师", "篮球", "公共体育", "中关村校区"],
          ["2030-2031-1", "2029100002", "乙二", "2029经济1班", "经济学", "2029", "经济学院", "R001", "篮球1班", "王老师", "篮球", "公共体育", "中关村校区"],
          ["2030-2031-1", "2027100003", "丙三", "2027信息1班", "计算机", "2027", "信息学院", "R001", "篮球1班", "王老师", "篮球", "公共体育", "中关村校区"],
          # 学生基础数据里没有 → 性别未知、待确认
          ["2030-2031-1", "2029100004", "丁四", "2029经济1班", "经济学", "2029", "经济学院", "R001", "篮球1班", "王老师", "篮球", "公共体育", "中关村校区"],
          ["2030-2031-1", "2030100005", "戊五", "2030法学1班", "法学", "2030", "法学院", "R002", "排球1班", "外聘老师", "排球", "公共体育", "中关村校区"]]


@pytest.fixture(scope="module")
def env():
    init_db()
    admin = TestClient(app)
    assert admin.post("/api/auth/dev-login", json={"staff_no": "admin01"}).status_code == 200
    sems = {s["code"]: s for s in admin.get("/api/semesters").json()}
    sem = sems.get("2030-2031-1") or admin.post("/api/admin/semesters", json={"start_year": 2030, "season": "fall"}).json()
    sid = sem["id"]
    admin.patch(f"/api/admin/semesters/{sid}", json={"entry_open": True})
    do_import(admin, "base", xlsx(BASE), sid)
    do_import(admin, "schedule", xlsx(SCHED), sid)
    do_import(admin, "enroll", xlsx(ENROLL), sid)
    teacher = TestClient(app)
    teacher.post("/api/auth/dev-login", json={"staff_no": T})
    classes = {c["class_no"]: c["id"] for c in admin.get(f"/api/semesters/{sid}/classes?scope=all").json()["classes"]}
    return {"admin": admin, "teacher": teacher, "sid": sid, "cid": classes["R001"], "cid2": classes["R002"]}


def rows_of(client, cid):
    return {r["student_no"]: r for r in client.get(f"/api/classes/{cid}").json()["rows"]}


def test_teacher_edits_student_and_band(env):
    t, admin, cid, sid = env["teacher"], env["admin"], env["cid"], env["sid"]
    rows = rows_of(t, cid)
    e1 = rows["2030100001"]["enrollment_id"]
    t.post(f"/api/classes/{cid}/cells", json={"changes": [{"enrollment_id": e1, "field": "a_raw", "value": "3:40"}]})
    before = rows_of(t, cid)["2030100001"]["display"]["a"]["score"]

    # 改姓名、学院、性别：立即生效、重算，标为手动修改，记日志
    r = t.post(f"/api/classes/{cid}/students/{e1}", json={"name": "甲壹", "college": "国际关系学院", "gender": "女"})
    assert r.status_code == 200 and set(r.json()["changed"]) == {"name", "college", "gender"}
    row = rows_of(t, cid)["2030100001"]
    assert row["name"] == "甲壹" and row["college"] == "国际关系学院" and row["gender_code"] == "F"
    assert row["display"]["a"]["item"] == "run800" and row["display"]["a"]["hint"]
    st = admin.get(f"/api/admin/semesters/{sid}/student/2030100001").json()["student"]
    assert st["manual"] and st["name"] == "甲壹"
    # 之后导入学生基础数据表不覆盖
    do_import(admin, "base", xlsx(BASE), sid)
    assert rows_of(t, cid)["2030100001"]["name"] == "甲壹"
    # 管理端“教师改过的学生”清单
    edits = admin.get(f"/api/admin/semesters/{sid}/pending").json()["teacher_edits"]
    mine = [x for x in edits if x["student_no"] == "2030100001"]
    assert mine and any(c.startswith("性别：男 → 女") for c in mine[0]["changes"])
    assert admin.get(f"/api/admin/semesters/{sid}/overview").json()["teacher_edits"] >= 1

    # 待确认的学生：教师补性别并定档后不再待确认
    e4 = rows["2029100004"]["enrollment_id"]
    assert rows["2029100004"]["pending"]
    r = t.post(f"/api/classes/{cid}/students/{e4}", json={"name": "丁四", "gender": "男", "band": "34"})
    assert r.status_code == 200 and "band" in r.json()["changed"]
    row = rows_of(t, cid)["2029100004"]
    assert not row["pending"] and row["band"] == "34" and row["gender_code"] == "M"
    assert all(s["student_no"] != "2029100004" for s in admin.get(f"/api/admin/semesters/{sid}/pending").json()["students"])

    # 非法值
    assert t.post(f"/api/classes/{cid}/students/{e1}", json={"band": "56"}).status_code == 400
    assert t.post(f"/api/classes/{cid}/students/{e1}", json={"name": ""}).status_code == 400
    # 别的老师改不了
    other = TestClient(app)
    other.post("/api/auth/dev-login", json={"staff_no": "T2031"})
    assert other.post(f"/api/classes/{cid}/students/{e1}", json={"name": "x"}).status_code == 403


def test_teacher_band_only_this_semester():
    from app.grading import correction_for
    from app.models import GradeCorrection, Semester
    s1 = Semester(code="2030-2031-1", start_year=2030)
    s2 = Semester(code="2030-2031-2", start_year=2030)
    c = GradeCorrection(id=1, band="34", effective_semester="2030-2031-1", this_semester_only=True)
    assert correction_for([c], s1) is c and correction_for([c], s2) is None
    c.this_semester_only = False
    assert correction_for([c], s2) is c


def test_other_issue_flow(env):
    t, admin, cid, sid = env["teacher"], env["admin"], env["cid"], env["sid"]
    e2 = rows_of(t, cid)["2029100002"]["enrollment_id"]
    assert t.post(f"/api/classes/{cid}/info-error", json={"enrollment_id": e2, "note": "该生已转学"}).status_code == 200
    items = [x for x in admin.get(f"/api/admin/semesters/{sid}/pending").json()["info_errors"] if x["enrollment_id"] == e2]
    assert items and not items[0]["resolved"]
    n = admin.get(f"/api/admin/semesters/{sid}/overview").json()["info_errors"]
    assert admin.post(f"/api/admin/enrollments/{e2}/issue-resolved", json={"resolved": True}).status_code == 200
    assert admin.get(f"/api/admin/semesters/{sid}/overview").json()["info_errors"] == n - 1
    h = t.get(f"/api/classes/{cid}/history/{e2}").json()
    assert h["issue"] == {"note": "该生已转学", "resolved": True, "resolved_at": h["issue"]["resolved_at"]}
    assert rows_of(t, cid)["2029100002"]["info_error_resolved"] is True
    assert t.post(f"/api/admin/enrollments/{e2}/issue-resolved", json={"resolved": True}).status_code == 403


def test_course_status_and_pe_lock(env):
    t, admin, cid, sid = env["teacher"], env["admin"], env["cid"], env["sid"]
    rows = rows_of(t, cid)
    ids = {no: r["enrollment_id"] for no, r in rows.items()}
    ch = []
    for no, eid in ids.items():
        ch += [{"enrollment_id": eid, "field": "a_status", "value": "正常"},
               {"enrollment_id": eid, "field": "a_raw", "value": "3:50"},
               {"enrollment_id": eid, "field": "b_raw", "value": "230"}]
    ch += [{"enrollment_id": ids["2030100001"], "field": "skill", "value": "50"},
           {"enrollment_id": ids["2030100001"], "field": "regular", "value": "8"},
           {"enrollment_id": ids["2027100003"], "field": "course_status", "value": "退课"},
           {"enrollment_id": ids["2029100002"], "field": "course_status", "value": "请假"}]
    res = t.post(f"/api/classes/{cid}/cells", json={"changes": ch}).json()
    bad = [x for x in res["results"] if not x["ok"]]
    assert [x["field"] for x in bad] == ["course_status"]  # “请假”不是可选项
    rows = rows_of(t, cid)
    r3 = rows["2027100003"]
    assert r3["course_status"] == "退课" and r3["total"] is None and r3["complete"]  # 技能、平时可以不填
    assert rows["2030100001"]["total"] is not None

    # 体育部锁定本学期体测
    assert admin.post(f"/api/admin/semesters/{sid}/pe-lock", json={"locked": True}).status_code == 200
    ov = admin.get(f"/api/admin/semesters/{sid}/overview").json()
    assert ov["semester"]["pe_locked"] and "class_list" in ov
    d = t.get(f"/api/classes/{cid}").json()
    assert d["editable"] and d["pe_locked"]
    # 锁定后：课程特殊情况、技能、备注能改；体测不能改；性别、标准档不能改，姓名能改
    res = t.post(f"/api/classes/{cid}/cells", json={"changes": [
        {"enrollment_id": ids["2027100003"], "field": "course_status", "value": "缓考"},
        {"enrollment_id": ids["2029100002"], "field": "skill", "value": "40"},
        {"enrollment_id": ids["2029100002"], "field": "note", "value": "x"},
        {"enrollment_id": ids["2029100002"], "field": "b_raw", "value": "240"}]}).json()
    assert [x["ok"] for x in res["results"]] == [True, True, True, False]
    assert t.post(f"/api/classes/{cid}/students/{ids['2029100002']}", json={"gender": "男"}).status_code == 403
    assert t.post(f"/api/classes/{cid}/students/{ids['2029100002']}", json={"band": "34"}).status_code == 403
    assert t.post(f"/api/classes/{cid}/students/{ids['2029100002']}", json={"name": "乙贰"}).status_code == 200
    assert admin.post(f"/api/admin/semesters/{sid}/pe-lock", json={"locked": False}).status_code == 200

    # 教师导出：照网页列顺序，课程特殊情况的总成绩格写状态
    wb = openpyxl.load_workbook(io.BytesIO(t.get(f"/api/classes/{cid}/export?sort=no").content))
    ws = wb["成绩"]
    assert [c.value for c in ws[1]] == ["序号", "学号", "性别", "姓名", "总成绩", "1000米跑/800米跑", None,
                                        "立定跳远", None, "体测小计", "运动技能", "平时成绩"]
    body = [[c.value for c in row] for row in ws.iter_rows(min_row=3)]
    assert [x[1] for x in body] == sorted(x[1] for x in body)
    r3 = next(x for x in body if x[1] == "2027100003")
    assert r3[4] == "缓考" and r3[5] == "3'50\""

    # 全校体测成绩表
    x = admin.get(f"/api/admin/semesters/{sid}/export-pe")
    assert x.status_code == 200
    ws = openpyxl.load_workbook(io.BytesIO(x.content)).active
    assert [c.value for c in ws[1]] == ["教学班号", "学号", "姓名", "性别", "长跑", "立定跳远"]
    body = [[c.value for c in row] for row in ws.iter_rows(min_row=2)]
    assert [b[1] for b in body] == sorted(b[1] for b in body)
    r1 = next(b for b in body if b[1] == "2030100001")
    assert r1[0] == "R001" and r1[4] == "3.50" and r1[5] == 230
    assert ws.cell(row=2, column=5).number_format == "@"
    # 全部成绩导出仍在，总成绩格写课程状态
    x = admin.get(f"/api/admin/semesters/{sid}/export")
    ws = openpyxl.load_workbook(io.BytesIO(x.content))["成绩"]
    heads = [c.value for c in ws[1]]
    r3 = next(dict(zip(heads, [c.value for c in row])) for row in ws.iter_rows(min_row=2) if row[3].value == "2027100003")
    assert r3["课程总成绩"] == "缓考" and "课程缓考" in r3["成绩状态"]


def test_pe_value_format():
    from app.exporter import pe_header, pe_value
    assert pe_value("run1000", 236) == "3.56" and pe_value("run800", 210) == "3.30"
    assert pe_value("run1000", 236.5) == "3.56.5"
    assert pe_value("run50", 5.6) == 5.6 and pe_value("long_jump", 256.0) == 256 and pe_value("pull_up", 14.0) == 14
    assert pe_header({"key": "b", "items": {"M": "pull_up", "F": "sit_up"}}) == "引体向上/仰卧起坐"
    assert pe_header({"key": "a", "items": {"M": "run50", "F": "run50"}}) == "50米跑"


def test_public_account(env):
    admin, sid, cid2 = env["admin"], env["sid"], env["cid2"]
    r = admin.post("/api/admin/public-accounts", json={"name": "体育部公共", "staff_no": "tyb01"})
    assert r.status_code == 200
    pw = r.json()["password"]
    assert auth.password_ok(pw) is None
    assert admin.post("/api/admin/public-accounts", json={"name": "重复", "staff_no": "tyb01"}).status_code == 409
    assert admin.post("/api/admin/public-accounts", json={"name": "第二个", "staff_no": "tyb02"}).status_code == 200
    assert admin.post("/api/admin/public-accounts", json={"name": "冲突", "staff_no": "admin01"}).status_code == 409
    users = {u["staff_no"]: u for u in admin.get("/api/admin/users").json()}
    assert users["tyb01"]["is_public"] and not users["tyb01"]["is_admin"]
    assert admin.post("/api/admin/users/tyb01", json={"is_admin": True}).status_code == 400

    c = TestClient(app)
    r = c.post("/api/auth/local-login", json={"staff_no": "tyb01", "password": pw})
    assert r.status_code == 200 and r.json()["must_change_password"]
    assert c.get(f"/api/semesters/{sid}/classes").status_code == 403  # 先改口令
    assert c.post("/api/me/password", json={"old": pw, "new": "Gongong#2030"}).status_code == 200
    me = c.get("/api/me").json()
    assert me["is_public"] and not me["is_admin"]
    assert c.get(f"/api/semesters/{sid}/classes").json()["classes"] == []
    assert c.get("/api/admin/users").status_code == 403
    # 用“指定教师”把班指派给公共账号后才看得到、录得进
    assert admin.patch(f"/api/admin/classes/{cid2}", json={"staff_no": "tyb01", "teacher_name": "外聘老师"}).status_code == 200
    assert [x["id"] for x in c.get(f"/api/semesters/{sid}/classes").json()["classes"]] == [cid2]
    e = c.get(f"/api/classes/{cid2}").json()["rows"][0]["enrollment_id"]
    assert c.post(f"/api/classes/{cid2}/cells", json={"changes": [{"enrollment_id": e, "field": "skill", "value": "50"}]}).status_code == 200
    assert c.get(f"/api/classes/{env['cid']}").status_code == 403
    # 管理员重置口令：旧口令失效，新口令登录后须再改
    r = admin.post("/api/admin/public-accounts/tyb01/reset")
    new_pw = r.json()["password"]
    assert TestClient(app).post("/api/auth/local-login", json={"staff_no": "tyb01", "password": "Gongong#2030"}).status_code == 401
    assert TestClient(app).post("/api/auth/local-login", json={"staff_no": "tyb01", "password": new_pw}).json()["must_change_password"]
    assert admin.post("/api/admin/public-accounts/T2030/reset").status_code == 404
    # 普通教师仍然不能用本地口令
    db = SessionLocal()
    u = db.get(User, T)
    u.password_hash = auth.hash_password("Teach#2030x")
    db.commit()
    db.close()
    assert TestClient(app).post("/api/auth/local-login", json={"staff_no": T, "password": "Teach#2030x"}).status_code == 401
