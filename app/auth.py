"""登录：数字人大统一身份认证（CAS 协议）+ 开发测试登录 + 应急本地管理员口令。"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .models import LoginAttempt, SessionLocal, User, now


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    staff_no = request.session.get("staff_no")
    if not staff_no:
        raise HTTPException(401, "请先登录")
    u = db.get(User, staff_no)
    if u is None:
        raise HTTPException(401, "请先登录")
    if u.must_change_password and request.url.path not in ("/api/me", "/api/me/password", "/auth/logout"):
        raise HTTPException(403, "首次登录请先修改初始口令")
    return u


def admin_user(u: User = Depends(current_user)) -> User:
    if not is_admin(u):
        raise HTTPException(403, "仅体育部管理员可操作")
    return u


def is_admin(u: User) -> bool:
    return u.is_admin or u.staff_no in config.ADMIN_STAFF_NOS


# ---------------------------------------------------------------- 失败锁定

def locked(db: Session, account: str) -> bool:
    since = now() - timedelta(minutes=config.LOGIN_LOCK_MINUTES)
    recent = db.scalars(select(LoginAttempt).where(LoginAttempt.account == account, LoginAttempt.at >= since)
                        .order_by(LoginAttempt.at.desc()).limit(config.LOGIN_MAX_FAILS)).all()
    return len(recent) >= config.LOGIN_MAX_FAILS and all(not a.success for a in recent)


def record_attempt(db: Session, account: str, ip: str, success: bool):
    db.add(LoginAttempt(account=account, ip=ip, success=success))
    db.commit()


# ---------------------------------------------------------------- 口令

def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 240_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("pbkdf2$"):
        return False
    _, salt, dk = stored.split("$")
    calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 240_000)
    return hmac.compare_digest(calc.hex(), dk)


def gen_password(length: int = 10) -> str:
    """随机初始口令：满足复杂度要求，去掉易混淆字符，便于抄写。"""
    import secrets
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length - 1)) + secrets.choice("#@%+")
        if password_ok(pw) is None:
            return pw


def password_ok(pw: str) -> str | None:
    """长度不少于8位，且包含数字、大写、小写、特殊字符中的至少三类。"""
    if len(pw) < 8:
        return "口令长度不少于8位"
    kinds = sum(bool(re.search(p, pw)) for p in (r"\d", r"[A-Z]", r"[a-z]", r"[^\dA-Za-z]"))
    if kinds < 3:
        return "口令须包含数字、大写字母、小写字母、特殊字符中的至少三类"
    return None


# ---------------------------------------------------------------- 统一身份认证（OAuth2.0）
# 学校提供的是 CAS 服务器上的 OAuth2.0 接口：
#   authorize → 用户登录后带 ?code=..&state=.. 回到 redirect_uri
#   accessToken（POST 表单）→ access_token
#   user/profiles?access_token=..（查询参数，不是 Bearer 头）→ 身份信息
# 实测响应：{"loginName": "工号/学号", "name": "...", "categoryName": "教职工/学生", "accounts": [...]}

class SSOError(Exception):
    pass


def sso_login_url(state: str) -> str:
    return f"{config.SSO_AUTHORIZE_URL}?" + urlencode({
        "response_type": "code", "client_id": config.SSO_CLIENT_ID,
        "redirect_uri": config.SSO_REDIRECT_URI, "scope": "all", "state": state})


def _parse_token(raw: str) -> dict:
    import json
    from urllib.parse import parse_qs
    try:
        body = json.loads(raw)
        if isinstance(body, dict):
            return body
    except ValueError:
        pass
    return {k: v[0] for k, v in parse_qs(raw).items()}


def _flatten(info: dict) -> dict:
    for key in ("data", "attributes", "user", "result"):
        inner = info.get(key)
        if isinstance(inner, dict):
            return {**info, **inner}
        if isinstance(inner, list) and inner and isinstance(inner[0], dict):
            return {**info, **inner[0]}
    return info


def _pick(d: dict, *names) -> str:
    for n in names:
        v = d.get(n)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def sso_exchange(code: str) -> dict:
    """用授权码换身份，返回 {"staff_no", "name", "category"}。"""
    r = httpx.post(config.SSO_TOKEN_URL, data={
        "grant_type": "authorization_code", "code": code, "client_id": config.SSO_CLIENT_ID,
        "client_secret": config.SSO_CLIENT_SECRET, "redirect_uri": config.SSO_REDIRECT_URI}, timeout=10)
    token = _pick(_parse_token(r.text), "access_token", "accessToken")
    if not token:
        raise SSOError(f"未换到 access_token：{r.text[:200]}")
    r = httpx.get(config.SSO_USER_INFO_URL, params={"access_token": token}, timeout=10)
    try:
        info = r.json()
    except ValueError:
        raise SSOError(f"统一身份认证返回的不是 JSON：{r.text[:200]}")
    if not isinstance(info, dict):
        raise SSOError("统一身份认证返回结构异常")
    info = _flatten(info)
    staff_no = _pick(info, "loginName")
    if not staff_no:
        raise SSOError("统一身份认证没有返回工号")
    accounts = [a for a in (info.get("accounts") or []) if isinstance(a, dict)]
    acct = next((a for a in accounts if _pick(a, "loginName") == staff_no), None) or \
        next((a for a in accounts if a.get("isMainOrg")), None) or (accounts[0] if accounts else {})
    category = _pick(acct, "categoryName") or _pick(info, "categoryName")
    if not category:  # 学校没给人员类别时按位数推：教职工工号8位，学生学号10位
        category = {8: "教职工", 10: "学生"}.get(len(staff_no), "")
    return {"staff_no": staff_no, "name": _pick(info, "name"), "category": category}


def ensure_user(db: Session, staff_no: str, name: str = "") -> User:
    u = db.get(User, staff_no)
    if u is None:
        u = User(staff_no=staff_no, name=name)
        db.add(u)
    elif name and not u.name:
        u.name = name
    if staff_no in config.ADMIN_STAFF_NOS:
        u.is_admin = True
    db.commit()
    return u
