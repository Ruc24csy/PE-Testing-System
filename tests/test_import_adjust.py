"""教学班按名称排序；覆盖导入动到已有成绩的教学班时须确认；学生基础数据表不再识别班级、专业、年级。"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import SessionLocal, Student, init_db
from tests.test_api import xlsx

T = "T2032"
BASE = [["学号", "姓名", "性别", "院系所名称", "班级", "年级", "学校", "专业"],
        ["2032300001", "甲", "男", "法学院", "2032法学1班", "2032", "人大", "法学"],
        ["2032300002", "乙", "女", "经济学院", "2032经济1班", "2032", "人大", "经济学"],
        ["2032300003", "丙", "男", "信息学院", "2032信息1班", "2032", "人大", "计算机"]]
HEAD = ["学年学期", "教学班号", "课程性质", "课程编号", "课程名称", "开课单位", "教学班名称", "授课对象", "授课教师", "教工号",
        "周次", "星期", "节次", "上课地点", "选课人数"]


def sched_row(no, name, teacher="李老师", staff=T, weekday="星期一"):
    return ["2032-2033学年秋季学期", no, "公共体育", "21000001", "网球", "体育部", name, "", teacher, staff,
            "1-16周", weekday, "0102", "网球场", "3"]


# 导入顺序故意打乱：名称排序应为 网球A班(S003) → 网球A班(S004) → 网球B班 → 网球C班
SCHED = [HEAD, sched_row("S001", "网球C班", weekday="星期一"), sched_row("S002", "网球B班", weekday="星期五"),
         sched_row("S004", "网球A班", weekday="星期三"), sched_row("S003", "网球A班", weekday="星期二")]
EHEAD = ["学年学期", "学号", "姓名", "上课班级", "上课专业", "上课年级", "上课院系", "教学班号", "教学班名称", "上课教师",
         "课程名称", "课程类别", "上课校区"]


def enr(sno, name, cno, label=""):
    return ["2032-2033-1", sno, name, label, "", "2032", "", cno, "", "", "网球", "公共体育", "中关村校区"]


ENROLL = [EHEAD, enr("2032300001", "甲", "S001"), enr("2032300002", "乙", "S001"), enr("2032300003", "丙", "S001"),
          enr("2032300002", "乙", "S002"), enr("2032300003", "丙", "S002")]


def run_import(client, kind, data, sid, mode="overwrite", **extra):
    info = client.post("/api/admin/import/upload", data={"kind": kind}, files={"file": ("a.xlsx", data)}).json()
    sh = info["sheets"][0]
    args = {"token": info["token"], "kind": kind, "sheet": sh["name"], "header_row": sh["header_row"],
            "mapping": sh["mapping"], "semester_id": sid, "mode": mode, **extra}
    pre = client.post("/api/admin/import/precheck", json=args).json()
    return info, pre, args


@pytest.fixture(scope="module")
def env():
    init_db()
    admin = TestClient(app)
    assert admin.post("/api/auth/dev-login", json={"staff_no": "admin01"}).status_code == 200
    sid = admin.post("/api/admin/semesters", json={"start_year": 2032, "season": "fall"}).json()["id"]
    admin.patch(f"/api/admin/semesters/{sid}", json={"entry_open": True})
    for kind, rows in (("base", BASE), ("schedule", SCHED), ("enroll", ENROLL)):
        _, pre, args = run_import(admin, kind, xlsx(rows), sid)
        assert not pre["blocking"], pre
        assert admin.post("/api/admin/import/commit", json=args).status_code == 200
    teacher = TestClient(app)
    teacher.post("/api/auth/dev-login", json={"staff_no": T})
    classes = {c["class_no"]: c["id"] for c in admin.get(f"/api/semesters/{sid}/classes?scope=all").json()["classes"]}
    return {"admin": admin, "teacher": teacher, "sid": sid, "classes": classes}


def test_classes_sorted_by_name(env):
    want = ["S003", "S004", "S002", "S001"]
    mine = env["teacher"].get(f"/api/semesters/{env['sid']}/classes").json()["classes"]
    assert [c["class_no"] for c in mine] == want
    alls = env["admin"].get(f"/api/semesters/{env['sid']}/classes?scope=all").json()["classes"]
    assert [c["class_no"] for c in alls] == want


def test_base_ignores_class_major_grade(env):
    admin = env["admin"]
    info, pre, _ = run_import(admin, "base", xlsx(BASE), env["sid"])
    codes = {f["code"] for f in info["fields"]}
    assert codes == {"student_no", "name", "gender", "college"}
    assert set(info["sheets"][0]["mapping"]) == {"student_no", "name", "gender", "college"}
    with SessionLocal() as db:
        st = db.get(Student, "2032300001")
        assert st.college == "法学院" and st.class_name == "" and st.major == "" and st.grade == ""
    d = admin.get(f"/api/admin/semesters/{env['sid']}/student/2032300001").json()
    assert set(d["student"]) == {"student_no", "name", "gender", "college", "manual"}
    # 选课表上课班级为空时不再拿基础数据补
    row = next(r for r in admin.get(f"/api/classes/{env['classes']['S001']}").json()["rows"]
               if r["student_no"] == "2032300001")
    assert row["class_label"] == "" and row["major"] == ""


def test_overwrite_enroll_with_scores_needs_confirm(env):
    admin, teacher, sid, cls = env["admin"], env["teacher"], env["sid"], env["classes"]
    rows = {r["student_no"]: r for r in teacher.get(f"/api/classes/{cls['S001']}").json()["rows"]}
    r = teacher.post(f"/api/classes/{cls['S001']}/cells", json={"changes": [
        {"enrollment_id": rows["2032300001"]["enrollment_id"], "field": "skill", "value": "50"}]})
    assert r.status_code == 200

    # 没有改动名单：不提示
    _, pre, args = run_import(admin, "enroll", xlsx(ENROLL), sid)
    assert "scored_impact" not in pre["plan"]
    assert admin.post("/api/admin/import/commit", json=args).status_code == 200

    # 新名单去掉 S001 的甲（有成绩）、乙（无成绩），S002 去掉丙（S002 没有成绩，不算）
    new = [EHEAD, enr("2032300003", "丙", "S001"), enr("2032300002", "乙", "S002")]
    _, pre, args = run_import(admin, "enroll", xlsx(new), sid)
    imp = pre["plan"]["scored_impact"]
    assert imp["classes"] == 1 and imp["deactivate"] == 1 and imp["delete"] == 1 and imp["class_nos"] == ["S001"]
    assert "停用 1 名有成绩的学生" in imp["message"] and "删除 1 名无成绩的学生" in imp["message"]
    assert any(i.get("key") == "scored_impact" for i in pre["items"]) and not pre["blocking"]

    # 追加不动已有名单：不提示
    _, pre_a, _ = run_import(admin, "enroll", xlsx(new), sid, mode="append")
    assert "scored_impact" not in pre_a["plan"]

    r = admin.post("/api/admin/import/commit", json=args)
    assert r.status_code == 400 and "已有成绩" in r.json()["detail"]
    assert len(admin.get(f"/api/classes/{cls['S001']}").json()["rows"]) == 3  # 没导入
    r = admin.post("/api/admin/import/commit", json={**args, "confirm_scored": True})
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["deactivated_with_scores"] == 1 and res["removed"] == 2
    assert [x["student_no"] for x in admin.get(f"/api/classes/{cls['S001']}").json()["rows"]] == ["2032300003"]


def test_overwrite_schedule_with_scores_needs_confirm(env):
    admin, sid = env["admin"], env["sid"]
    _, pre, _ = run_import(admin, "schedule", xlsx(SCHED), sid)
    assert "scored_impact" not in pre["plan"]  # 内容没变
    changed = [HEAD, sched_row("S001", "网球C班", teacher="新老师", staff="T9999"), *SCHED[2:]]
    _, pre, args = run_import(admin, "schedule", xlsx(changed), sid)
    imp = pre["plan"]["scored_impact"]
    assert imp["classes"] == 1 and imp["teacher_changed"] == 1 and imp["class_nos"] == ["S001"]
    assert admin.post("/api/admin/import/commit", json=args).status_code == 400
    assert admin.post("/api/admin/import/commit", json={**args, "confirm_scored": True}).status_code == 200
    # 只改了没有成绩的班：不提示
    changed2 = [HEAD, sched_row("S001", "网球C班", teacher="新老师", staff="T9999"),
                sched_row("S002", "网球B班", weekday="星期四"), *SCHED[3:]]
    _, pre, _ = run_import(admin, "schedule", xlsx(changed2), sid)
    assert "scored_impact" not in pre["plan"]
