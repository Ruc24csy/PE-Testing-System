"""运行配置，全部从环境变量读取，便于部署到通州云平台服务器。"""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("PE_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("PE_DATABASE_URL", f"sqlite:///{DATA_DIR / 'pe_scores.db'}")


def _secret() -> str:
    v = os.getenv("PE_SECRET_KEY")
    if v:
        return v
    f = DATA_DIR / ".secret_key"
    # 多个服务进程同时启动时只允许一个生成密钥，其余读同一份，否则各进程的登录状态互不认
    try:
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
    for _ in range(50):
        key = f.read_text().strip()
        if key:
            return key
        import time
        time.sleep(0.05)
    raise RuntimeError("会话密钥文件为空")


SECRET_KEY = _secret()

# 登录方式：sso = 数字人大统一身份认证（学校 CAS 服务器上的 OAuth2.0 接口）；
# local = 工号＋平台口令（统一认证接入前临时使用）；dev = 输入工号即可登录（仅限开发，禁止用于生产）
AUTH_MODE = os.getenv("PE_AUTH_MODE", "dev")
if AUTH_MODE == "cas":  # 旧写法
    AUTH_MODE = "sso"
SSO_BASE_URL = os.getenv("PE_SSO_BASE_URL", "https://cas.ruc.edu.cn/cas")
SSO_AUTHORIZE_URL = os.getenv("PE_SSO_AUTHORIZE_URL") or f"{SSO_BASE_URL.rstrip('/')}/oauth2.0/authorize"
SSO_TOKEN_URL = os.getenv("PE_SSO_TOKEN_URL") or f"{SSO_BASE_URL.rstrip('/')}/oauth2.0/accessToken"
SSO_USER_INFO_URL = os.getenv("PE_SSO_USER_INFO_URL") or f"{SSO_BASE_URL.rstrip('/')}/oauth2.0/user/profiles"
SSO_LOGOUT_URL = os.getenv("PE_SSO_LOGOUT_URL") or f"{SSO_BASE_URL.rstrip('/')}/logout"
SSO_CLIENT_ID = os.getenv("PE_SSO_CLIENT_ID", "")  # 学校应用中心注册后获得的 appid
SSO_CLIENT_SECRET = os.getenv("PE_SSO_CLIENT_SECRET", "")
# 回调地址，必须与应用中心登记的一字不差（明文，不要预先 URL 编码），例如 https://域名/auth/sso/callback
SSO_REDIRECT_URI = os.getenv("PE_SSO_REDIRECT_URI", "")
# 允许登录的人员类别（学校返回的 categoryName），逗号分隔；管理员不受限
SSO_ALLOWED_CATEGORIES = {s.strip() for s in os.getenv("PE_SSO_ALLOWED_CATEGORIES", "教职工").split(",") if s.strip()}

if AUTH_MODE == "sso":
    _missing = [k for k, v in (("PE_SSO_CLIENT_ID", SSO_CLIENT_ID), ("PE_SSO_CLIENT_SECRET", SSO_CLIENT_SECRET),
                               ("PE_SSO_REDIRECT_URI", SSO_REDIRECT_URI)) if not v]
    if _missing:
        raise RuntimeError(f"PE_AUTH_MODE=sso 但缺少配置：{'、'.join(_missing)}")
elif AUTH_MODE not in ("local", "dev"):
    raise RuntimeError(f"未知的 PE_AUTH_MODE：{AUTH_MODE}")

# 体育部管理员工号，逗号分隔；也可在管理端“账号”里维护
ADMIN_STAFF_NOS = {s.strip() for s in os.getenv("PE_ADMIN_STAFF_NOS", "").split(",") if s.strip()}

HTTPS_ONLY = os.getenv("PE_HTTPS_ONLY", "0") == "1"
LOGIN_MAX_FAILS = 5
LOGIN_LOCK_MINUTES = 15
