"""指定教师从账号列表选、添加学生管理员、教学班名称自然排序。"""
import io
from types import SimpleNamespace

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app import services
from app.main import app
from app.models import init_db
from tests.test_api import do_import, xlsx
from tests.test_sso import sso, start  # noqa: F401  统一认证的假接口

T = "20320001"  # 课程安排表里的教师，从没登录过
HEAD = ["学年学期", "教学班号", "课程性质", "课程编号", "课程名称", "开课单位", "教学班名称", "授课对象", "授课教师", "教工号",
        "周次", "星期", "节次", "上课地点", "选课人数"]
NAMES = [("乒乓球", "乒乓球1"), ("乒乓球", "乒乓球10班"), ("乒乓球", "乒乓球2"), ("乒乓球", "乒乓球9"),
         ("五禽戏", "五禽戏04班"), ("五禽戏", "五禽戏05班"), ("五禽戏", "五禽戏1"), ("五禽戏", "五禽戏2")]
SCHED = [HEAD] + [["2034-2035学年秋季学期", f"S{i:03d}", "公共体育", "21000999", course, "体育部", name, "", "赵老师", T,
                   "1-16周", "星期二", "0304", "世纪馆", "0"] for i, (course, name) in enumerate(NAMES)]
EXPECT = ["乒乓球1", "乒乓球2", "乒乓球9", "乒乓球10班", "五禽戏1", "五禽戏2", "五禽戏04班", "五禽戏05班"]


@pytest.fixture(scope="module")
def env():
    init_db()
    admin = TestClient(app)
    assert admin.post("/api/auth/dev-login", json={"staff_no": "admin01"}).status_code == 200
    sems = {s["code"]: s for s in admin.get("/api/semesters").json()}
    sem = sems.get("2034-2035-1") or admin.post("/api/admin/semesters", json={"start_year": 2034, "season": "fall"}).json()
    sid = sem["id"]
    do_import(admin, "schedule", xlsx(SCHED), sid)
    classes = admin.get(f"/api/semesters/{sid}/classes?scope=all").json()["classes"]
    return {"admin": admin, "sid": sid, "classes": {c["class_name"]: c["id"] for c in classes}}


def test_class_sort_key_natural():
    cs = [SimpleNamespace(course_name=c, class_name=n, class_no="") for c, n in NAMES]
    assert [c.class_name for c in sorted(cs, key=services.class_sort_key)] == EXPECT


def test_class_lists_use_natural_order(env):
    admin, sid = env["admin"], env["sid"]
    assert [c["class_name"] for c in admin.get(f"/api/semesters/{sid}/classes?scope=all").json()["classes"]] == EXPECT
    assert [c["class_name"] for c in admin.get(f"/api/admin/semesters/{sid}/overview").json()["class_list"]] == EXPECT
    r = admin.get(f"/api/admin/semesters/{sid}/export")
    assert r.status_code == 200
    ws = openpyxl.load_workbook(io.BytesIO(r.content)).worksheets[0]
    seen = []
    for row in ws.iter_rows(values_only=True):
        for v in row:
            if v in EXPECT and v not in seen:
                seen.append(v)
    assert seen in ([], EXPECT)  # 没有学生时成绩表可能为空；有就必须按这个顺序


def test_assign_teacher_from_accounts(env):
    admin, cid = env["admin"], env["classes"]["乒乓球1"]
    users = {u["staff_no"]: u for u in admin.get("/api/admin/users").json()}
    assert T in users  # 导入课程安排表就建了账号，没登录过也选得到
    pub = admin.post("/api/admin/public-accounts", json={"name": "体育部公共3", "staff_no": "tyb32"})
    assert pub.status_code == 200
    r = admin.patch(f"/api/admin/classes/{cid}", json={"staff_no": "tyb32"})
    assert r.status_code == 200 and r.json()["staff_no"] == "tyb32" and r.json()["teacher_name"] == "体育部公共3"
    assert not r.json()["teacher_pending"]
    # 平台里没有的工号不能指定，也不会顺手建出账号
    r = admin.patch(f"/api/admin/classes/{cid}", json={"staff_no": "20329999", "teacher_name": "某某"})
    assert r.status_code == 400
    assert "20329999" not in {u["staff_no"] for u in admin.get("/api/admin/users").json()}
    assert admin.patch(f"/api/admin/classes/{cid}", json={"staff_no": T}).json()["teacher_name"] == "赵老师"


def test_add_student_admin_rules(env):
    admin = env["admin"]
    nos = lambda: {u["staff_no"]: u for u in admin.get("/api/admin/users").json()}  # noqa: E731
    r = admin.post("/api/admin/users/20329998", json={"is_admin": True})  # 不存在的8位工号
    assert r.status_code == 400 and "没有" in r.json()["detail"]
    assert "20329998" not in nos()
    for bad in ("123", "20329", "abcdefghij", "203299981"):
        assert admin.post(f"/api/admin/users/{bad}", json={"is_admin": True}).status_code == 400
    assert admin.post(f"/api/admin/users/{T}", json={"is_admin": True}).status_code == 200  # 已有的8位工号照常
    assert nos()[T]["is_admin"]
    assert admin.post(f"/api/admin/users/{T}", json={"is_admin": False}).status_code == 200
    assert admin.post("/api/admin/users/tyb32", json={"is_admin": True}).status_code == 400  # 公共账号不行
    assert admin.post("/api/admin/users/2032100777", json={"is_admin": True}).status_code == 200
    assert nos()["2032100777"]["is_admin"]


def test_student_admin_can_sso_login(sso):  # noqa: F811
    admin = TestClient(app)
    from app import config
    config.AUTH_MODE = "dev"
    assert admin.post("/api/auth/dev-login", json={"staff_no": "admin01"}).status_code == 200
    assert admin.post("/api/admin/users/2032100888", json={"is_admin": True}).status_code == 200
    config.AUTH_MODE = "sso"
    sso["profile_body"] = {"loginName": "2032100888", "name": "学生管理员", "categoryName": "本科生"}
    c = TestClient(app)
    state = start(c)
    r = c.get(f"/?code=CODE&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/"
    me = c.get("/api/me").json()
    assert me["staff_no"] == "2032100888" and me["is_admin"] and me["name"] == "学生管理员"
    # 普通学生仍然不能登录
    sso["profile_body"] = {"loginName": "2032100999", "name": "学生", "categoryName": "本科生"}
    c2 = TestClient(app)
    state = start(c2)
    assert "error=category" in c2.get(f"/?code=CODE&state={state}", follow_redirects=False).headers["location"]
