"""体育部从表格导入体测成绩：按表名认项目、取最优（表内多条、与已有成绩比）、跳过原因、标了特殊状态的不改、锁定后不改、回退。"""
import io

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app import score_import, scoring
from app.main import app
from app.models import init_db
from tests.test_api import do_import, xlsx

SEM = "2036-2037学年秋季学期"
M1, F1, M2, M3, U, X = "2036300001", "2036300002", "2036300003", "2036300004", "2036300005", "2036309999"
BASE = [["学号", "姓名", "性别", "院系所名称", "班级", "年级", "学校", "专业"],
        [M1, "男一", "男", "法学院", None, "2036", "人大", None],
        [F1, "女一", "女", "法学院", None, "2036", "人大", None],
        [M2, "男二", "男", "法学院", None, "2036", "人大", None],
        [M3, "男三", "男", "法学院", None, "2036", "人大", None]]
SHEAD = ["学年学期", "教学班号", "课程性质", "课程编号", "课程名称", "开课单位", "教学班名称", "授课对象", "授课教师", "教工号",
         "周次", "星期", "节次", "上课地点", "选课人数"]
SCHED = [SHEAD] + [[SEM, no, "公共体育", "21000555", "篮球", "体育部", name, "", "孙老师", "T361", "1-16周", "星期三",
                    "0304", "世纪馆", "3"] for no, name in (("K001", "篮球1"), ("K002", "篮球2"), ("K003", "篮球3"))]
EHEAD = ["学年学期", "学号", "姓名", "上课班级", "上课专业", "上课年级", "上课院系", "教学班号", "教学班名称", "上课教师",
         "课程名称", "课程类别", "上课校区"]


def _e(no, name, cls):
    return ["2036-2037-1", no, name, "2036法学1班", "法学", "2036", "法学院", cls, "篮球", "孙老师", "篮球", "公共体育", "中关村校区"]


ENROLL = [EHEAD, _e(M1, "男一", "K001"), _e(F1, "女一", "K001"), _e(U, "无性别", "K001"),
          _e(M2, "男二", "K002"), _e(M3, "男三", "K001"), _e(M3, "男三", "K003")]

# 体育部样表的表头：成绩写在各项目自己的那一列
HEAD = ["学生姓名", "学号", "引体向上-男/仰卧起坐-女（次）", "肺活量（毫升）", "50米（秒）", "800米-女/1000米-男（分.秒）",
        "立定跳远（厘米）", "坐位体前屈（厘米）", "身高（厘米）", "体重（千克）"]
RULE = "导入规则：（项目成绩无需添加单位）1000/800米（例）：3.24"
COL = {"pull_sit": 2, "run50": 4, "run": 5, "jump": 6}


def row(name, no, col, v):
    r = [name, no] + [None] * 8
    r[COL[col]] = v
    return r


def book(sheets):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        ws.append([RULE])
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


SAMPLE = book({
    "立定跳远": [HEAD, row("男一", M1, "jump", 240), row("男一", M1, "jump", 255), row("女一", F1, "jump", 180),
             row("男二", M2, "jump", 230), row("男一", M1, "jump", "abc"), row("查无", X, "jump", 200),
             # 跳远页里别的列有数也不读：只认这一页的项目
             [None, M3, 50, None, 7.5, None, None, None, None, None]],
    "1000米": [HEAD, row("男一", " " + M1 + " ", "run", "3.4"), row("男一", M1, "run", 4),
              row("女一", F1, "run", 3.3), row("男一", M1, "run", "3.75")],
    "800米": [HEAD, row("女一", F1, "run", 3.45)],
    "50米": [HEAD, row("男一", M1, "run50", 7.1)],
    "仰卧起坐（女）": [HEAD, row("男一", M1, "pull_sit", "30")],
    # 表名不是项目：按列名认，男女合并列按性别取
    "汇总": [HEAD, [None, M3, None, None, None, "3.5", 260, None, None, None],
           [None, U, None, None, None, None, 200, None, None, None]],
})


@pytest.fixture(scope="module")
def env():
    init_db()
    admin = TestClient(app)
    assert admin.post("/api/auth/dev-login", json={"staff_no": "admin01"}).status_code == 200
    sems = {s["code"]: s for s in admin.get("/api/semesters").json()}
    sem = sems.get("2036-2037-1") or admin.post("/api/admin/semesters", json={"start_year": 2036, "season": "fall"}).json()
    sid = sem["id"]
    admin.patch(f"/api/admin/semesters/{sid}", json={"entry_open": True})
    do_import(admin, "base", xlsx(BASE), sid)
    do_import(admin, "schedule", xlsx(SCHED), sid)
    do_import(admin, "enroll", xlsx(ENROLL), sid)
    classes = {c["class_no"]: c["id"] for c in admin.get(f"/api/semesters/{sid}/classes?scope=all").json()["classes"]}
    rows = {r["student_no"]: r for r in admin.get(f"/api/classes/{classes['K001']}").json()["rows"]}
    # 已有成绩：男一立定跳远 250，女一 800 米 3'30"
    r = admin.post(f"/api/classes/{classes['K001']}/cells", json={"changes": [
        {"enrollment_id": rows[M1]["enrollment_id"], "field": "b_raw", "value": "250"},
        {"enrollment_id": rows[F1]["enrollment_id"], "field": "a_raw", "value": "3'30\""}]})
    assert all(x["ok"] for x in r.json()["results"]), r.text
    # 男二立定跳远标了免测：导入不改
    m2 = admin.get(f"/api/classes/{classes['K002']}").json()["rows"][0]
    r = admin.post(f"/api/classes/{classes['K002']}/cells", json={"changes": [
        {"enrollment_id": m2["enrollment_id"], "field": "b_status", "value": "免测"}]})
    assert all(x["ok"] for x in r.json()["results"]), r.text
    return {"admin": admin, "sid": sid, "classes": classes}


def values(env, cls):
    rows = env["admin"].get(f"/api/classes/{env['classes'][cls]}").json()["rows"]
    return {r["student_no"]: r["values"] for r in rows}


def upload(env, data=SAMPLE):
    r = env["admin"].post("/api/admin/score-import/upload", data={"semester_id": env["sid"]},
                          files={"file": ("名单.xlsx", data)})
    assert r.status_code == 200, r.text
    return r.json()


def test_parse_and_item_names():
    assert score_import.item_of("仰卧起坐（女）") == "sit_up"
    assert score_import.item_of("引体向上（男）") == "pull_up"
    assert score_import.item_of("800米") == "run800" and score_import.item_of("1000米") == "run1000"
    assert score_import.item_of("50米") == "run50" and score_import.item_of("立定跳远") == "long_jump"
    assert score_import.item_of("800米-女/1000米-男（分.秒）") == {"M": "run1000", "F": "run800"}
    assert score_import.item_of("肺活量（毫升）") is None and score_import.item_of("汇总") is None
    p = score_import.parse_value
    assert p("run1000", "3.24") == 204 and p("run1000", 4.1) == 250 and p("run1000", 3) == 180
    assert p("run800", 2.48) == 168 and p("run800", "3'05\"") == 185
    assert p("run50", "6.61") == 6.6 and p("long_jump", 285) == 285 and p("sit_up", "52") == 52
    with pytest.raises(scoring.ParseError):
        p("run1000", "3.75")


def test_precheck_reports_updates_and_skip_reasons(env):
    r = upload(env)
    ups = {(u["student_no"], u["class"].split()[0], u["item"]): u for u in r["updates"]}
    assert set(ups) == {(M1, "K001", "long_jump"), (F1, "K001", "long_jump"), (M1, "K001", "run1000"),
                        (M3, "K001", "long_jump"), (M3, "K001", "run1000"), (M3, "K003", "long_jump"),
                        (M3, "K003", "run1000")}
    # 表内两条取好的 255，且好于已有 250
    assert ups[(M1, "K001", "long_jump")]["new"] == "255" and ups[(M1, "K001", "long_jump")]["old"] == "250"
    assert ups[(M1, "K001", "run1000")]["new"] == "3'40\""  # 学号前后空格照样认出
    assert ups[(M3, "K001", "run1000")]["new"] == "3'50\""
    reasons = [(s["student_no"].strip(), s["sheet"], s["reason"]) for s in r["skips"]]
    has = lambda no, sheet, text: any(n == no and sh == sheet and text in rs for n, sh, rs in reasons)  # noqa: E731
    assert has(M1, "立定跳远", "表里有更好的一条")
    assert has(M2, "立定跳远", "已标为“免测”")
    assert has(M1, "立定跳远", "成绩格式不对")
    assert has(X, "立定跳远", "没有这个学号的有效选课记录")
    assert has(M1, "1000米", "表里有更好的一条")  # 4 即 4分00秒，比 3分40秒慢
    assert has(M1, "1000米", "秒数不能大于等于60")
    assert has(F1, "1000米", "女生，不测1000米跑")
    assert has(F1, "800米", "已有更好的成绩")
    assert has(M1, "50米", "不是该生本学期的体测项目")
    assert has(M1, "仰卧起坐（女）", "男生，不测一分钟仰卧起坐")
    assert has(U, "汇总", "性别待确认")
    assert not any(n == M3 and sh == "立定跳远" for n, sh, _ in reasons)  # 跳远页里的 50 米列没读
    assert len(r["updates"]) == 7
    # 预检不写库
    assert values(env, "K001")[M1]["b_raw"] == 250


def test_commit_takes_best_keeps_locked_and_reverts(env):
    admin, sid = env["admin"], env["sid"]
    token = upload(env)["token"]
    res = admin.post("/api/admin/score-import/commit", json={"semester_id": sid, "token": token}).json()
    assert res["saved"] == 7
    k1 = values(env, "K001")
    assert k1[M1]["b_raw"] == 255 and k1[M1]["a_raw"] == 220
    assert k1[F1]["b_raw"] == 180 and k1[F1]["a_raw"] == 210  # 800 米保留原来更好的 3'30"
    assert k1[M3]["a_raw"] == 230 and values(env, "K003")[M3]["b_raw"] == 260
    assert values(env, "K002")[M2]["b_raw"] is None  # 标了免测的不改
    # 有修改记录和操作日志，能看出是导入写的
    rows = {r["student_no"]: r for r in admin.get(f"/api/classes/{env['classes']['K001']}").json()["rows"]}
    hist = admin.get(f"/api/classes/{env['classes']['K001']}/history/{rows[M1]['enrollment_id']}").json()
    assert "导入" in str(hist) or res["batch"] in str(hist)
    logs = admin.get(f"/api/admin/logs?semester_id={sid}&action=导入").json()
    assert any(res["batch"] in str(x) for x in logs["logs"])
    # 再导入同一份：都不如现有成绩，不再更新
    again = upload(env)
    assert again["updates"] == []
    # 回退：改回导入前的值
    rv = admin.post(f"/api/admin/score-import/{res['batch']}/revert", json={"semester_id": sid}).json()
    assert rv["reverted"] == 7
    k1 = values(env, "K001")
    assert k1[M1]["b_raw"] == 250 and k1[M1]["a_raw"] is None and k1[F1]["b_raw"] is None


def test_locked_semester_skips_pe(env):
    """体育部锁定本学期体测后，表格导入一条都不改，跳过原因写明已锁定；解锁后照常。"""
    admin, sid = env["admin"], env["sid"]
    assert admin.post(f"/api/admin/semesters/{sid}/pe-lock", json={"locked": True}).status_code == 200
    try:
        r = upload(env)
        assert r["updates"] == []
        assert any("已锁定" in s["reason"] for s in r["skips"])
    finally:
        assert admin.post(f"/api/admin/semesters/{sid}/pe-lock", json={"locked": False}).status_code == 200
    assert len(upload(env)["updates"]) == 7


def test_readonly_semester_rejected(env):
    admin = env["admin"]
    sems = {s["code"]: s for s in admin.get("/api/semesters").json()}
    sem = sems.get("2036-2037-2") or admin.post("/api/admin/semesters", json={"start_year": 2036, "season": "spring"}).json()
    assert not sem["is_current"] and not sem["entry_open"]
    r = admin.post("/api/admin/score-import/upload", data={"semester_id": sem["id"]}, files={"file": ("a.xlsx", SAMPLE)})
    assert r.status_code == 403
