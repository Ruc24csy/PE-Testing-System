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
          # 2024级随2025级上课：以学号推算为准（大三，大三大四标准），不进待确认
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
    sem = admin.post("/api/admin/semesters", json={"label": "2026-2027学年秋季学期"}).json()
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
    assert not rows["2024200004"]["pending"] and rows["2024200004"]["band"] == "34"  # 按学号推算
    assert rows["2024200004"]["band_source"] == "学号推算"
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

    # 体育部改档：赵四实际随 2025 级（降级），写入异动记录后按大一大二计算
    pend = admin.get(f"/api/admin/semesters/{sid}/pending").json()
    assert [s["student_no"] for s in pend["students"]] == ["2025200005"]
    r = admin.post(f"/api/admin/semesters/{sid}/students/2024200004/standard",
                   json={"cohort": 2025, "change_type": "降级", "note": "教务处学籍异动名单"})
    assert r.status_code == 200 and r.json()["standard"]["band"] == "12" and r.json()["standard"]["source"] == "异动记录"
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

    assert teacher.post(f"/api/classes/{cid}/submit").status_code == 200
    r = teacher.post(f"/api/classes/{cid}/cells", json={"changes": [{"enrollment_id": e2, "field": "skill", "value": "50"}]})
    assert r.status_code == 403  # 已提交锁定
    assert teacher.post(f"/api/classes/{cid}/unlock-request", json={"reason": "录错一人"}).status_code == 200
    ul = admin.get("/api/admin/unlock-requests").json()[0]
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
    heads = [c.value for c in ws[1]]
    first = {h: c.value for h, c in zip(heads, ws[2])}
    assert first["学号"] == "2026200001" and first["课程总成绩"] == 95.9 and first["耐力跑原始成绩"] == "2'50\""
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
    sem2 = admin.post("/api/admin/semesters", json={"label": "2026-2027-2"}).json()
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


def test_issue_teacher_passwords(admin):
    sid = admin.get("/api/semesters").json()
    sid = next(s["id"] for s in sid if s["code"] == "2026-2027-1")
    r = admin.post("/api/admin/users/passwords", json={"semester_id": sid})
    assert r.status_code == 200
    ws = openpyxl.load_workbook(io.BytesIO(r.content)).active
    rows = {row[0]: row[2] for row in ws.iter_rows(min_row=2, values_only=True)}
    assert "T001" in rows and len(rows["T001"]) == 10
    # 已有口令的不重复生成
    assert admin.post("/api/admin/users/passwords", json={"semester_id": sid}).status_code == 400
    t = TestClient(app)
    assert t.post("/api/auth/local-login", json={"staff_no": "T001", "password": rows["T001"]}).json()["must_change_password"]
    assert t.get(f"/api/semesters/{sid}/classes").status_code == 403
    assert t.post("/api/me/password", json={"old": rows["T001"], "new": "Teacher#2026"}).status_code == 200
    assert len(t.get(f"/api/semesters/{sid}/classes").json()["classes"]) == 1
    # 重置
    r = admin.post("/api/admin/users/passwords", json={"staff_nos": ["T001"]})
    ws = openpyxl.load_workbook(io.BytesIO(r.content)).active
    assert ws.cell(2, 1).value == "T001"
    assert TestClient(app).post("/api/auth/local-login", json={"staff_no": "T001", "password": "Teacher#2026"}).status_code == 401
