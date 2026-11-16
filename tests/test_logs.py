"""操作日志页：没学期时能看日志、选学期时登录记录仍可见、后端参数报错在前端显示成可读的话。"""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import init_db


@pytest.fixture(scope="module")
def admin():
    init_db()
    c = TestClient(app)
    assert c.post("/api/auth/dev-login", json={"staff_no": "admin01"}).status_code == 200
    return c


def test_logs_without_semester(admin):
    # 还没建学期时前端曾经传 semester_id=（空串），后端返回 422
    for qs in ("", "?semester_id=", "?semester_id=&action=&page=1", "?semester_id=%20"):
        r = admin.get(f"/api/admin/logs{qs}")
        assert r.status_code == 200, (qs, r.text)
        assert any(l["action"] == "登录" for l in r.json()["logs"])
    r = admin.get("/api/admin/logs?semester_id=abc")
    assert r.status_code == 400 and isinstance(r.json()["detail"], str)


def test_login_logs_visible_in_semester(admin):
    sid = admin.post("/api/admin/semesters", json={"start_year": 2031, "season": "fall"}).json()["id"]
    other = admin.post("/api/admin/semesters", json={"start_year": 2031, "season": "spring"}).json()["id"]
    logs = admin.get(f"/api/admin/logs?semester_id={sid}").json()["logs"]
    actions = {l["action"] for l in logs}
    assert "登录" in actions                      # 不属于任何学期的记录
    # 别的学期的记录不混进来：新建 other 学期这条日志若带了学期号，就只出现在 other 下
    only_login = admin.get(f"/api/admin/logs?semester_id={sid}&action=登录").json()
    assert only_login["total"] >= 1 and all(l["action"] == "登录" for l in only_login["logs"])
    assert admin.get(f"/api/admin/logs?semester_id={other}&action=登录").json()["total"] == only_login["total"]


def _err_text():
    mr = pytest.importorskip("py_mini_racer")
    js = Path(__file__).resolve().parent.parent.joinpath("app/static/app.js").read_text(encoding="utf-8")
    m = re.search(r"^function errText\(.*?^}\n", js, re.S | re.M)
    assert m, "app.js 里找不到 errText"
    ctx = mr.MiniRacer()
    ctx.eval(m.group(0))
    return lambda detail, status: ctx.call("errText", detail, status)


def test_frontend_422_message_readable(admin):
    err_text = _err_text()
    # 拿后端真实的 422 返回（page 不是数字）喂给前端的转换函数
    r = admin.get("/api/admin/logs?page=abc")
    assert r.status_code == 422 and isinstance(r.json()["detail"], list)
    msg = err_text(r.json()["detail"], 422)
    assert "object" not in msg.lower() and "请求参数有误（422）" in msg and "page" in msg
    assert err_text("本班成绩已提交", 400) == "本班成绩已提交"
    assert err_text(None, 500) == "请求失败（500）"
    assert err_text({"msg": "出错了"}, 400) == "出错了"
    assert "object" not in err_text({"foo": 1}, 400).lower()
