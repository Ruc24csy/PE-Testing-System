"""接口全流程：建学期 → 导入三张表 → 教师录入 → 提交前校验 → 体育部确认标准档 → 提交 → 撤回 → 导出。"""
import io

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import init_db


def xlsx(rows, title_row=None, footer=None):
    wb = openpyxl.Workbook()
    ws = wb.active
    if title_row:
        ws.append([title_row])
    for r in rows:
        ws.append(r)
    if footer:
        ws.append([None, None, footer])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


BASE = [["学号", "姓名", "性别", "院系所名称", "班级", "年级", "学校", "专业"],
        ["2026200001", "张一", "男", "法学院", None, "2026", "人大", None],
        ["2025200002", "李二", "女", "经济学院", None, "2025", "人大", None],
        ["2023200003", "王三", "男", "信息学院", None, "2023", "人大", None],
        ["2024200004", "赵四", "女", "统计学院", None, "2024", "人大", None],
        [None, "坏行", "1", None, None, None, None, None]]
SCHED = [["学年学期", "教学班号", "课程性质", "课程编号", "课程名称", "开课单位", "教学班名称", "授课对象", "授课教师", "教工号",
          "周次", "星期", "节次", "上课地点", "选课人数"],
         ["2026-2027学年秋季学期", "C001", "公共体育", "21000773", "乒乓球", "体育部", "乒乓球10班", "", "张老师", "T001",
          "1-16周", "星期五", "0910", "世纪馆", "4"],
         ["2026-2027学年秋季学期", "C002", "公共体育", "21000602", "健美", "体育部", "健美3", "", "体育部", "",
          "1-16周", "星期一", "0102", "拳道馆", "0"]]
ENROLL = [["学年学期", "学号", "姓名", "上课班级", "上课专业", "上课年级", "上课院系", "教学班号", "教学班名称", "上课教师",
           "课程名称", "课程类别", "上课校区"],
          ["2026-2027-1", "2026200001", "张一", "2026法学1班", "法学", "2026", "法学院", "C001", "乒乓球10班", "张老师", "乒乓球", "公共体育", "中关村校区"],
          ["2026-2027-1", "2025200002", "李二", "2025经济1班", "经济学", "2025", "经济学院", "C001", "乒乓球10班", "张老师", "乒乓球", "公共体育", "中关村校区"],
          ["2026-2027-1", "2023200003", "王三", "2023信息1班", "计算机", "2023", "信息学院", "C001", "乒乓球10班", "张老师", "乒乓球", "公共体育", "中关村校区"],
          # 2024级随2025级上课（降级）：按上课年级算大二（大一大二标准），不进待确认
          ["2026-2027-1", "2024200004", "赵四", "2025统计1班", "统计学", "2025", "统计学院", "C001", "乒乓球10班", "张老师", "乒乓球", "公共体育", "中关村校区"],
          # 学生基础数据里没有 → 性别未知，待确认
          ["2026-2027-1", "2025200005", "钱五", "2025经济1班", "经济学", "2025", "经济学院", "C001", "乒乓球10班", "张老师", "乒乓球", "公共体育", "中关村校区"],
          # 一人多课：张一同时选了健美，正常
          ["2026-2027-1", "2026200001", "张一", "2026法学1班", "法学", "2026", "法学院", "C002", "健美3", "体育部", "健美", "公共体育", "中关村校区"],
          ["2026-2027-1", "2026200009", "关闭班", "x", "x", "2026", "x", "C999", "足球05班（选课人数不足10人关闭）", "", "足球", "公共体育", "中关村校区"]]


@pytest.fixture(scope="module")
def admin():
    init_db()
    c = TestClient(app)
    assert c.post("/api/auth/dev-login", json={"staff_no": "admin01"}).status_code == 200
    return c


@pytest.fixture(scope="module")
def teacher():
    c = TestClient(app)
    c.post("/api/auth/dev-login", json={"staff_no": "T001"})
    return c


def do_import(client, kind, data, sid, mode="overwrite"):
    r = client.post("/api/admin/import/upload", data={"kind": kind}, files={"file": ("a.xlsx", data)})
    assert r.status_code == 200, r.text
    info = r.json()
    sh = info["sheets"][0]
    args = {"token": info["token"], "kind": kind, "sheet": sh["name"], "header_row": sh["header_row"],
            "mapping": sh["mapping"], "semester_id": sid, "mode": mode}
    pre = client.post("/api/admin/import/precheck", json=args).json()
    res = client.post("/api/admin/import/commit", json=args)
    return info, pre, res


def test_full_flow(admin, teacher):
    assert teacher.get("/api/admin/users").status_code == 403
    sem = admin.post("/api/admin/semesters", json={"start_year": 2026, "season": "fall"}).json()
    assert sem["is_current"] and sem["config"]["slots"][0]["items"]["M"] == "run1000"
    sid = sem["id"]
    assert teacher.get(f"/api/semesters/{sid}/classes").json()["imported"] is False

    info, pre, res = do_import(admin, "base", xlsx(BASE, title_row="体测学生基础数据"), sid)
    assert info["sheets"][0]["header_row"] == 2  # 首行是大标题
    assert res.status_code == 200 and res.json()["result"]["students"] == 4

    _, pre, res = do_import(admin, "schedule", xlsx(SCHED, footer="制表单位：中国人民大学"), sid)
    assert not pre["blocking"], pre
    assert any("教师待补" in i["message"] for i in pre["items"])
    assert res.json()["result"]["created"] == 2

    _, pre, res = do_import(admin, "enroll", xlsx(ENROLL), sid)
    msgs = " ".join(i["message"] for i in pre["items"])
    assert "C999" in msgs and "已关闭" in msgs and "多门体育课" in msgs
    assert res.json()["result"]["added"] == 6
    assert res.json()["result"]["pending"] == 1

    # 覆盖导入可重复执行，结果一致
    _, _, res2 = do_import(admin, "enroll", xlsx(ENROLL), sid)
    assert res2.json()["result"]["added"] == 0 and res2.json()["result"]["updated"] == 6

    lst = teacher.get(f"/api/semesters/{sid}/classes").json()
    assert [c["class_no"] for c in lst["classes"]] == ["C001"]
    cid = lst["classes"][0]["id"]
    other = admin.get(f"/api/semesters/{sid}/classes?scope=all").json()["classes"]
    assert len(other) == 2
    c2 = next(c for c in other if c["class_no"] == "C002")["id"]
    assert teacher.get(f"/api/classes/{c2}").status_code == 403  # 只能看本人教学班

    d = teacher.get(f"/api/classes/{cid}").json()
    rows = {r["student_no"]: r for r in d["rows"]}
    assert rows["2026200001"]["band"] == "12" and rows["2023200003"]["band"] == "34"
    assert not rows["2024200004"]["pending"] and rows["2024200004"]["band"] == "12"  # 按上课年级
    assert rows["2024200004"]["band_source"] == "上课年级"
    assert not rows["2026200001"]["pending"]  # 一人多课不算异常
    assert rows["2025200005"]["pending"] and rows["2025200005"]["gender_code"] is None
    e1, e2, e3 = rows["2026200001"]["enrollment_id"], rows["2025200002"]["enrollment_id"], rows["2023200003"]["enrollment_id"]

    r = teacher.post(f"/api/classes/{cid}/cells", json={"changes": [
        {"enrollment_id": e1, "field": "a_raw", "value": "2:50"},
        {"enrollment_id": e1, "field": "b_raw", "value": "273cm"},
        {"enrollment_id": e1, "field": "skill", "value": "55.5"},
        {"enrollment_id": e1, "field": "regular", "value": "9"},
        {"enrollment_id": e2, "field": "a_raw", "value": "3分40秒"},
        {"enrollment_id": e2, "field": "skill", "value": "61"},          # 超范围，拦截
        {"enrollment_id": e3, "field": "a_raw", "value": "abc"},         # 无法解析
        {"enrollment_id": e3, "field": "b_status", "value": "免测"},
    ]}).json()
    ok = {(x["enrollment_id"], x["field"]): x["ok"] for x in r["results"]}
    assert ok[(e2, "skill")] is False and ok[(e3, "a_raw")] is False and ok[(e1, "a_raw")] is True
    row1 = next(x for x in r["rows"] if x["enrollment_id"] == e1)
    assert row1["display"]["a"]["score"] == 107 and row1["display"]["a"]["bonus"] == 7
    assert row1["subtotal"] == 31.4 and row1["total"] == 95.9
    assert row1["display"]["a"]["raw_text"] == "2'50\""
    row3 = next(x for x in r["rows"] if x["enrollment_id"] == e3)
    assert row3["display"]["b"]["status_display"] == "免测"
    # 状态与数值互斥
    r = teacher.post(f"/api/classes/{cid}/cells", json={"changes": [{"enrollment_id": e3, "field": "b_raw", "value": "250"}]}).json()
    assert r["results"][0]["ok"] is False

    chk = teacher.get(f"/api/classes/{cid}/check").json()
    assert chk["blocking"] and len(chk["pending"]) == 1 and chk["missing"]
    assert teacher.post(f"/api/classes/{cid}/submit").status_code == 400

    # 体育部改档：赵四实际按 2024 级（大三）算，写入异动记录后按大三大四计算
    pend = admin.get(f"/api/admin/semesters/{sid}/pending").json()
    assert [s["student_no"] for s in pend["students"]] == ["2025200005"]
    r = admin.post(f"/api/admin/semesters/{sid}/students/2024200004/standard",
                   json={"cohort": 2024, "change_type": "体育部确认", "note": "教务处学籍异动名单"})
    assert r.status_code == 200 and r.json()["standard"]["band"] == "34" and r.json()["standard"]["source"] == "异动记录"
    # 补上钱五的学生基础数据后，重新导入即解除待确认
    _, _, res = do_import(admin, "base", xlsx(BASE + [["2025200005", "钱五", "女", "经济学院", None, "2025", "人大", None]]), sid)
    assert admin.get(f"/api/admin/semesters/{sid}/pending").json()["students"] == []

    # 改本班权重：留痕并重算
    cfg = d["config"]
    cfg["slots"][0]["weight"] = 0.25
    cfg["skill_weight"] = 0.55
    assert teacher.put(f"/api/classes/{cid}/config", json={"cfg": cfg}).status_code == 200
    d = teacher.get(f"/api/classes/{cid}").json()
    row1 = next(x for x in d["rows"] if x["enrollment_id"] == e1)
    assert row1["display"]["a"]["folded"] == 26.75
    cfg["slots"][0]["weight"] = 0.2
    cfg["skill_weight"] = 0.6
    teacher.put(f"/api/classes/{cid}/config", json={"cfg": cfg})

    # 只交体测：体测还有没录完的（没填也没标特殊情况）不能提交；运动技能、平时成绩不查
    r = teacher.post(f"/api/classes/{cid}/submit")
    assert r.status_code == 400 and "体测" in r.json()["detail"]
    d = teacher.get(f"/api/classes/{cid}").json()
    fill = []
    for x in d["rows"]:
        for k in ("a", "b"):
            if x["values"][f"{k}_status"] == "正常" and x["values"][f"{k}_raw"] is None:
                fill.append({"enrollment_id": x["enrollment_id"], "field": f"{k}_status", "value": "缓测"})
    teacher.post(f"/api/classes/{cid}/cells", json={"changes": fill})
    assert teacher.post(f"/api/classes/{cid}/submit").status_code == 200
    # 提交后只锁体测：运动技能能改，体测成绩和状态不能改
    r = teacher.post(f"/api/classes/{cid}/cells", json={"changes": [
        {"enrollment_id": e2, "field": "skill", "value": "50"},
        {"enrollment_id": e2, "field": "a_raw", "value": "3:30"},
        {"enrollment_id": e3, "field": "b_status", "value": "正常"}]})
    assert r.status_code == 200
    ok = {x["field"]: x["ok"] for x in r.json()["results"]}
    assert ok == {"skill": True, "a_raw": False, "b_status": False}
    assert teacher.put(f"/api/classes/{cid}/config", json={"cfg": cfg}).status_code == 403
    teacher.post(f"/api/classes/{cid}/cells", json={"changes": [{"enrollment_id": e2, "field": "skill", "value": ""}]})
    assert teacher.post(f"/api/classes/{cid}/unlock-request", json={"reason": "录错一人"}).status_code == 200
    ul = admin.get("/api/admin/unlock-requests").json()[0]
    # 驳回必须写原因：空的、只有空格的都拒绝，申请仍是待审核，班级仍锁着
    for blank in ("", "   "):
        r = admin.post(f"/api/admin/unlock-requests/{ul['id']}", json={"approve": False, "note": blank})
        assert r.status_code == 400 and "驳回原因" in r.json()["detail"]
    assert admin.get("/api/admin/unlock-requests").json()[0]["status"] == "待审核"
    assert teacher.get(f"/api/classes/{cid}").json()["class"]["status"] != "录入中"
    assert admin.post(f"/api/admin/unlock-requests/{ul['id']}", json={"approve": True, "note": ""}).status_code == 200
    assert teacher.get(f"/api/classes/{cid}").json()["class"]["status"] == "录入中"

    # 留痕
    h = teacher.get(f"/api/classes/{cid}/history/{e1}").json()
    assert any(x["field"] == "a_raw" and x["new"] == "170.0" for x in h["history"])

    # 导出
    r = teacher.get(f"/api/classes/{cid}/export")
    assert r.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    ws = wb["成绩"]
    assert [c.value for c in ws[1]][:6] == ["序号", "学号", "性别", "姓名", "总成绩", "1000米跑/800米跑"]
    assert [c.value for c in ws[2]][5:9] == ["成绩", "得分", "成绩", "得分"]
    assert [c.value for c in ws[3]][1] == "2023200003"  # 默认照网页按学号排序
    first = next([c.value for c in row] for row in ws.iter_rows(min_row=3) if row[1].value == "2026200001")
    assert first[1:6] == ["2026200001", "男", "张一", 95.9, "2'50\""] and first[6] == 107
    assert first[0] in (1, 2, 3, 4, 5)
    assert "评分表版本" in [c.value for c in wb["说明"]["A"]]

    # 整班上传（备用路径）与回退
    tpl = teacher.get(f"/api/classes/{cid}/template")
    wb = openpyxl.load_workbook(io.BytesIO(tpl.content))
    ws = wb.active
    for row in ws.iter_rows(min_row=2):
        if row[0].value == "2025200002":
            row[7].value = 48
            row[8].value = "8.5"
    buf = io.BytesIO()
    wb.save(buf)
    up = teacher.post(f"/api/classes/{cid}/upload", files={"file": ("t.xlsx", buf.getvalue())}).json()
    assert up["students"] >= 1 and not up["errors"]
    cm = teacher.post(f"/api/classes/{cid}/upload/commit", json={"token": up["token"]}).json()
    d = teacher.get(f"/api/classes/{cid}").json()
    assert next(x for x in d["rows"] if x["enrollment_id"] == e2)["values"]["skill"] == 48
    assert teacher.post(f"/api/classes/{cid}/upload/{cm['batch']}/revert").status_code == 200
    d = teacher.get(f"/api/classes/{cid}").json()
    assert next(x for x in d["rows"] if x["enrollment_id"] == e2)["values"]["skill"] is None

    logs = admin.get(f"/api/admin/logs?semester_id={sid}").json()
    actions = {l["action"] for l in logs["logs"]}
    assert {"导入", "保存", "提交", "撤回申请", "撤回审核", "改档", "导出", "配置"} <= actions

    # 以往学期只读
    sem2 = admin.post("/api/admin/semesters", json={"start_year": 2026, "season": "spring"}).json()
    assert sem2["config"]["slots"][0]["items"]["M"] == "run50"
    admin.patch(f"/api/admin/semesters/{sem2['id']}", json={"is_current": True})
    r = teacher.post(f"/api/classes/{cid}/cells", json={"changes": [{"enrollment_id": e2, "field": "skill", "value": "50"}]})
    assert r.status_code == 403
    admin.patch(f"/api/admin/semesters/{sid}", json={"entry_open": True})
    r = teacher.post(f"/api/classes/{cid}/cells", json={"changes": [{"enrollment_id": e2, "field": "skill", "value": "50"}]})
    assert r.status_code == 200


def test_local_login_lockout(admin):
    from app import auth
    from app.models import SessionLocal, User
    db = SessionLocal()
    db.add(User(staff_no="root", name="应急", is_admin=True, password_hash=auth.hash_password("Init#2026"),
                must_change_password=True))
    db.commit()
    c = TestClient(app)
    r = c.post("/api/auth/local-login", json={"staff_no": "root", "password": "Init#2026"})
    assert r.json()["must_change_password"]
    assert c.get("/api/semesters").status_code == 403
    assert c.post("/api/me/password", json={"old": "Init#2026", "new": "short"}).status_code == 400
    assert c.post("/api/me/password", json={"old": "Init#2026", "new": "NewPass#2026"}).status_code == 200
    assert c.get("/api/semesters").status_code == 200
    bad = TestClient(app)
    for _ in range(5):
        assert bad.post("/api/auth/local-login", json={"staff_no": "root", "password": "x"}).status_code == 401
    assert bad.post("/api/auth/local-login", json={"staff_no": "root", "password": "NewPass#2026"}).status_code == 429


def test_teacher_cannot_use_local_password(admin):
    from app import auth
    from app.models import SessionLocal, User
    db = SessionLocal()
    u = db.get(User, "T001")
    u.password_hash, u.must_change_password = auth.hash_password("Teach#2026x"), True
    db.commit()
    assert TestClient(app).post("/api/auth/local-login", json={"staff_no": "T001", "password": "Teach#2026x"}).status_code == 401
    # 旧数据里留着「未改口令」标记的教师，用其他方式登录不被拦去改口令页
    t = TestClient(app)
    t.post("/api/auth/dev-login", json={"staff_no": "T001"})
    assert t.get("/api/me").json()["must_change_password"] is False
    assert t.get("/api/semesters").status_code == 200


def test_admin_maintains_students_and_enrollments(admin):
    sid = next(s["id"] for s in admin.get("/api/semesters").json() if s["code"] == "2026-2027-1")
    classes = {c["class_no"]: c["id"] for c in admin.get(f"/api/semesters/{sid}/classes?scope=all").json()["classes"]}
    # 补录：一个学生基础数据里没有的学生
    r = admin.post(f"/api/admin/semesters/{sid}/enrollments", json={"student_no": "2025200099", "class_id": classes["C001"], "name": "孙九"})
    assert r.status_code == 200
    eid = r.json()["enrollment_id"]
    d = admin.get(f"/api/admin/semesters/{sid}/student/2025200099").json()
    assert d["student"] is None and d["standard"]["pending"]
    assert admin.put(f"/api/admin/semesters/{sid}/student/2025200099", json={"name": "孙九"}).status_code == 400  # 必须有性别
    d = admin.put(f"/api/admin/semesters/{sid}/student/2025200099", json={"gender": "女", "name": "孙九", "college": "法学院"}).json()
    assert d["student"]["gender"] == "F" and not d["standard"]["pending"] and d["standard"]["band"] == "12"
    # 录一个成绩，再调班：成绩跟着走
    assert admin.post(f"/api/classes/{classes['C001']}/cells", json={"changes": [{"enrollment_id": eid, "field": "skill", "value": "50"}]}).json()["results"][0]["ok"]
    r = admin.patch(f"/api/admin/enrollments/{eid}", json={"class_id": classes["C002"], "class_label": "2025法学1班", "grade_field": "2025"})
    assert r.status_code == 200
    rows = admin.get(f"/api/classes/{classes['C002']}").json()["rows"]
    moved = next(x for x in rows if x["student_no"] == "2025200099")
    assert moved["values"]["skill"] == 50 and moved["class_label"] == "2025法学1班"
    assert all(x["student_no"] != "2025200099" for x in admin.get(f"/api/classes/{classes['C001']}").json()["rows"])
    # 覆盖导入选课表：手动改动不被覆盖，调走的人不会被加回原班
    from tests.test_api import ENROLL, do_import, xlsx
    do_import(admin, "base", xlsx(BASE + [["2025200005", "钱五", "女", "经济学院", None, "2025", "人大", None],
                                          ["2025200099", "别名", "男", "x", None, "2025", "人大", None]]), sid)
    assert admin.get(f"/api/admin/semesters/{sid}/student/2025200099").json()["student"]["gender"] == "F"
    do_import(admin, "enroll", xlsx(ENROLL + [["2026-2027-1", "2025200099", "孙九", "", "", "", "", "C001", "乒乓球10班", "", "乒乓球", "公共体育", "中关村校区"]]), sid)
    assert all(x["student_no"] != "2025200099" for x in admin.get(f"/api/classes/{classes['C001']}").json()["rows"])
    assert any(x["student_no"] == "2025200099" for x in admin.get(f"/api/classes/{classes['C002']}").json()["rows"])
    # 移出与恢复
    assert admin.delete(f"/api/admin/enrollments/{eid}").status_code == 200
    assert all(x["student_no"] != "2025200099" for x in admin.get(f"/api/classes/{classes['C002']}").json()["rows"])
    assert admin.post(f"/api/admin/enrollments/{eid}/restore").status_code == 200
    assert admin.get(f"/api/admin/semesters/{sid}/students?q=孙九").json()["students"][0]["student_no"] == "2025200099"
    acts = {l["action"] for l in admin.get(f"/api/admin/logs?semester_id={sid}").json()["logs"]}
    assert {"加入教学班", "修改学生信息", "修改选课", "移出教学班", "恢复选课"} <= acts
    t = TestClient(app)
    t.post("/api/auth/dev-login", json={"staff_no": "T777"})
    assert t.patch(f"/api/admin/enrollments/{eid}", json={}).status_code == 403


def test_excellent_rate(admin):
    from app.services import excellence, is_excellent
    assert is_excellent(89.5) and not is_excellent(89.4) and not is_excellent(None)
    e = excellence([{"total": 95.9}, {"total": 89.5}, {"total": 70}, {"total": None}])
    assert e["excellent"] == 2 and e["roster"] == 4 and e["rate"] == 0.5 and e["over"]
    sid = next(s["id"] for s in admin.get("/api/semesters").json() if s["code"] == "2026-2027-1")
    c1 = next(c for c in admin.get(f"/api/semesters/{sid}/classes?scope=all").json()["classes"] if c["class_no"] == "C001")
    assert c1["excellent"] >= 1  # 张一总评 95.9
    x = admin.get(f"/api/classes/{c1['id']}/export")
    info = openpyxl.load_workbook(io.BytesIO(x.content))["说明"]
    assert any(str(r[0].value).startswith("优秀率 C001") for r in info.iter_rows())


def test_preview_does_not_save(admin):
    sid = next(s["id"] for s in admin.get("/api/semesters").json() if s["code"] == "2026-2027-1")
    cid = next(c["id"] for c in admin.get(f"/api/semesters/{sid}/classes?scope=all").json()["classes"] if c["class_no"] == "C001")
    rows = admin.get(f"/api/classes/{cid}").json()["rows"]
    r = next(x for x in rows if x["student_no"] == "2025200002")
    before = r["values"]["regular"]
    res = admin.post(f"/api/classes/{cid}/preview", json={"changes": [
        {"enrollment_id": r["enrollment_id"], "field": "regular", "value": "7.5"},
        {"enrollment_id": r["enrollment_id"], "field": "skill", "value": "99"}]}).json()
    pr = res["rows"][0]
    assert pr["values"]["regular"] == 7.5
    # 体测两项还有一格空着，按新口径小计与总成绩都不出数
    assert pr["subtotal"] is None and pr["total"] is None
    assert res["errors"][0]["field"] == "skill"
    # 四项都齐了才出小计与总成绩
    res = admin.post(f"/api/classes/{cid}/preview", json={"changes": [
        {"enrollment_id": r["enrollment_id"], "field": "a_status", "value": "正常"},
        {"enrollment_id": r["enrollment_id"], "field": "b_status", "value": "正常"},
        {"enrollment_id": r["enrollment_id"], "field": "a_raw", "value": "3分40秒"},
        {"enrollment_id": r["enrollment_id"], "field": "b_raw", "value": "180"},
        {"enrollment_id": r["enrollment_id"], "field": "skill", "value": "55"},
        {"enrollment_id": r["enrollment_id"], "field": "regular", "value": "7.5"}]}).json()
    pr = res["rows"][0]
    assert pr["subtotal"] is not None and pr["total"] is not None
    again = next(x for x in admin.get(f"/api/classes/{cid}").json()["rows"] if x["student_no"] == "2025200002")
    assert again["values"]["regular"] == before  # 没有写库


def test_logout_clears_cache_only():
    init_db()
    c = TestClient(app)
    assert c.post("/api/auth/dev-login", json={"staff_no": "T001"}).status_code == 200
    r = c.post("/auth/logout")
    assert r.status_code == 200
    # 只清缓存：本地存储里有未保存的成绩草稿，cookies 会波及同域其他系统
    assert r.headers["Clear-Site-Data"] == '"cache"'
    assert c.get("/api/me").status_code == 401


def test_logout_stays_on_platform_in_sso_mode(monkeypatch):
    # 统一认证模式下也只退出本平台，不跳数智人大的登出页
    from app import config
    monkeypatch.setattr(config, "AUTH_MODE", "sso")
    r = TestClient(app).post("/auth/logout")
    assert r.status_code == 200
    assert r.json()["redirect"] == "/"


def test_create_semester_validation(admin, teacher):
    assert teacher.post("/api/admin/semesters", json={"start_year": 2027, "season": "fall"}).status_code == 403
    r = admin.post("/api/admin/semesters", json={"start_year": 2027, "season": "spring"})
    assert r.status_code == 200
    sem = r.json()
    assert sem["code"] == "2027-2028-2" and sem["name"] == "2027-2028学年春季学期" and sem["season"] == "spring"
    dup = admin.post("/api/admin/semesters", json={"start_year": 2027, "season": "spring"})
    assert dup.status_code == 409 and "已存在" in dup.json()["detail"]
    for bad in ({"start_year": 2019, "season": "fall"}, {"start_year": 2101, "season": "fall"},
                {"start_year": "abc", "season": "fall"}, {"start_year": True, "season": "fall"},
                {"start_year": 2027, "season": "summer"}, {"season": "fall"}, {"label": "2027-2028学年秋季学期"}):
        r = admin.post("/api/admin/semesters", json=bad)
        assert r.status_code == 400, bad
    assert admin.post("/api/admin/semesters", json={"start_year": "2027", "season": "fall"}).json()["code"] == "2027-2028-1"


def test_class_template_sorted_by_student_no(admin, teacher):
    """录入模板按学号升序，与选课表导入行序、后补学生无关。"""
    sem = admin.post("/api/admin/semesters", json={"start_year": 2028, "season": "fall"}).json()
    sid_new = sem["id"]
    # 选课表故意不按学号写：2028...9、2028...2、2028...11、2028...100
    nos = ["2028200009", "2028200002", "2028200011", "2028200100"]
    enroll = [ENROLL[0]] + [[row[0].replace("2026-2027-1", sem["code"])] + [no, f"学生{no}"] + row[3:]
                             for no, row in zip(nos, [ENROLL[1], ENROLL[2], ENROLL[3], ENROLL[4]])]
    for i, r in enumerate(enroll[1:], start=1):
        r[7], r[8] = "C901", "排序测试班"
    sch = [SCHED[0], [SCHED[1][0].replace("2026-2027学年秋季学期", sem["name"])] + ["C901"] + SCHED[1][2:8] + ["张老师", "T001", "1-16周", "星期五", "0910", "世纪馆", "4"]]
    do_import(admin, "base", xlsx(BASE + [[no, f"学生{no}", "男", "法学院", None, "2028", "人大", None] for no in nos]), sid_new)
    do_import(admin, "schedule", xlsx(sch), sid_new)
    _, pre, res = do_import(admin, "enroll", xlsx(enroll), sid_new)
    assert not pre["blocking"], pre
    cid = next(c["id"] for c in admin.get(f"/api/semesters/{sid_new}/classes?scope=all").json()["classes"]
               if c["class_no"] == "C901")
    # 后补一个学号最小的学生排在名单末尾
    assert admin.post(f"/api/admin/semesters/{sid_new}/enrollments",
                      json={"student_no": "2028200001", "class_id": cid, "name": "补录学生"}).status_code == 200

    wb = openpyxl.load_workbook(io.BytesIO(teacher.get(f"/api/classes/{cid}/template").content))
    ws = wb.active
    got = [row[0].value for row in ws.iter_rows(min_row=2)]
    assert got == ["2028200001", "2028200002", "2028200009", "2028200011", "2028200100"]
    # 学号列仍是文本，姓名、表头不变
    assert [c.value for c in ws[1]][:3] == ["学号", "姓名", "性别"]
    assert isinstance(ws.cell(row=2, column=1).value, str)
    # 网页录入表和导出仍按同一学号顺序
    web = [r["student_no"] for r in teacher.get(f"/api/classes/{cid}").json()["rows"]]
    assert sorted(web, key=lambda s: int(s)) == got
