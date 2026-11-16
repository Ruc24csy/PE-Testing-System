"""统一身份认证（学校 CAS 服务器上的 OAuth2.0）回调流程，用假的学校接口。"""
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app import auth, config
from app.main import app
from app.models import init_db

REDIRECT = "https://tykcjlr.ruc.edu.cn"


class FakeResp:
    def __init__(self, text=None, js=None):
        self.text = text or ""
        self._js = js

    def json(self):
        if self._js is None:
            raise ValueError
        return self._js


@pytest.fixture
def sso(monkeypatch):
    init_db()
    monkeypatch.setattr(config, "AUTH_MODE", "sso")
    monkeypatch.setattr(config, "SSO_CLIENT_ID", "appid")
    monkeypatch.setattr(config, "SSO_CLIENT_SECRET", "secret")
    monkeypatch.setattr(config, "SSO_REDIRECT_URI", REDIRECT)
    calls = {}
    profile = {"loginName": "20200089", "name": "李秀娟", "categoryName": "教职工",
               "accounts": [{"loginName": "20200089", "categoryName": "教职工", "isMainOrg": True}]}

    def post(url, data, timeout):
        calls["token"] = (url, data)
        return FakeResp(text="access_token=TOKEN123&expires_in=7200")

    def get(url, params, timeout):
        calls["profile"] = (url, params)
        return FakeResp(js=calls.get("profile_body", profile))

    monkeypatch.setattr(auth.httpx, "post", post)
    monkeypatch.setattr(auth.httpx, "get", get)
    return calls


def start(c):
    r = c.get("/auth/login", follow_redirects=False)
    assert r.status_code in (302, 307)
    loc = r.headers["location"]
    q = parse_qs(urlparse(loc).query)
    assert loc.startswith("https://cas.ruc.edu.cn/cas/oauth2.0/authorize")
    assert q["client_id"] == ["appid"] and q["redirect_uri"] == [REDIRECT]
    assert q["scope"] == ["all"] and q["response_type"] == ["code"]
    return q["state"][0]


def test_sso_login_teacher_via_root_callback(sso):
    c = TestClient(app)
    state = start(c)
    r = c.get(f"/?code=CODE&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/"
    assert sso["token"][1]["code"] == "CODE" and sso["token"][1]["redirect_uri"] == REDIRECT
    assert sso["profile"][1] == {"access_token": "TOKEN123"}  # 查询参数，不是 Bearer 头
    me = c.get("/api/me").json()
    assert me["staff_no"] == "20200089" and me["name"] == "李秀娟"
    r = c.get(f"/?code=CODE&state={state}", follow_redirects=False)  # state 一次一用
    assert "error=expired" in r.headers["location"]


def test_sso_callback_path_also_works(sso):
    c = TestClient(app)
    state = start(c)
    r = c.get(f"/auth/sso/callback?code=CODE&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/"


def test_sso_rejects_forged_state(sso):
    c = TestClient(app)
    start(c)
    r = c.get("/?code=CODE&state=forged", follow_redirects=False)
    assert "error=expired" in r.headers["location"]
    assert "token" not in sso  # 伪造的回调不拿 code 去换令牌


def test_sso_rejects_student(sso):
    sso["profile_body"] = {"loginName": "2025201717", "name": "学生", "categoryName": "学生"}
    c = TestClient(app)
    state = start(c)
    r = c.get(f"/?code=CODE&state={state}", follow_redirects=False)
    assert "error=category" in r.headers["location"]
    assert c.get("/api/me").status_code == 401


def test_local_password_only_for_admin_in_sso_mode(sso):
    from app.models import SessionLocal, User
    db = SessionLocal()
    u = db.get(User, "T900") or User(staff_no="T900")
    u.password_hash, u.must_change_password, u.is_admin = auth.hash_password("Teach#2026x"), False, False
    db.merge(u)
    db.commit()
    c = TestClient(app)
    assert c.post("/api/auth/local-login", json={"staff_no": "T900", "password": "Teach#2026x"}).status_code == 401


@pytest.mark.parametrize("login,ok", [("20200089", True), ("2025201717", False)])
def test_sso_category_fallback_by_length(sso, login, ok):
    sso["profile_body"] = {"loginName": login, "name": "某人"}
    c = TestClient(app)
    state = start(c)
    r = c.get(f"/?code=CODE&state={state}", follow_redirects=False)
    assert (r.headers["location"] == "/") is ok


def test_sso_login_skips_initial_password_change(sso):
    """应急管理员的账号发过初始口令、还没改：走统一认证登录时不被拦去改口令页。"""
    from app.models import SessionLocal, User
    db = SessionLocal()
    u = db.get(User, "20200089") or User(staff_no="20200089")
    u.password_hash, u.must_change_password, u.is_admin = auth.hash_password("Init#2026x"), True, True
    db.merge(u)
    db.commit()
    c = TestClient(app)
    state = start(c)
    assert c.get(f"/?code=CODE&state={state}", follow_redirects=False).headers["location"] == "/"
    assert c.get("/api/me").json()["must_change_password"] is False
    assert c.get("/api/semesters").status_code == 200
    # 同一账号用口令登录仍须先改初始口令
    loc = TestClient(app)
    assert loc.post("/api/auth/local-login", json={"staff_no": "20200089", "password": "Init#2026x"}).json()["must_change_password"]
    assert loc.get("/api/semesters").status_code == 403
