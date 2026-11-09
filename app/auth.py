"""登录：数字人大统一身份认证（CAS 协议）+ 开发测试登录 + 应急本地管理员口令。"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import xml.etree.ElementTree as ET
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


def password_ok(pw: str) -> str | None:
    """长度不少于8位，且包含数字、大写、小写、特殊字符中的至少三类。"""
    if len(pw) < 8:
        return "口令长度不少于8位"
    kinds = sum(bool(re.search(p, pw)) for p in (r"\d", r"[A-Z]", r"[a-z]", r"[^\dA-Za-z]"))
    if kinds < 3:
        return "口令须包含数字、大写字母、小写字母、特殊字符中的至少三类"
    return None


# ---------------------------------------------------------------- CAS

def cas_login_url() -> str:
    return f"{config.CAS_SERVER_URL.rstrip('/')}/login?" + urlencode({"service": config.CAS_SERVICE_URL})


def cas_validate(ticket: str) -> tuple[str, str] | None:
    """CAS 2.0/3.0 serviceValidate，返回 (工号, 姓名)。"""
    url = f"{config.CAS_SERVER_URL.rstrip('/')}/serviceValidate"
    r = httpx.get(url, params={"service": config.CAS_SERVICE_URL, "ticket": ticket}, timeout=10)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    ns = {"cas": "http://www.yale.edu/tp/cas"}
    ok = root.find("cas:authenticationSuccess", ns)
    if ok is None:
        return None
    user = (ok.findtext("cas:user", default="", namespaces=ns) or "").strip()
    name = ""
    attrs = ok.find("cas:attributes", ns)
    if attrs is not None:
        for key in ("name", "cn", "displayName", "xm", "userName"):
            v = attrs.findtext(f"cas:{key}", default="", namespaces=ns)
            if v:
                name = v.strip()
                break
    return (user, name) if user else None


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
