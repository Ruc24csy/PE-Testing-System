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
    if not f.exists():
        f.write_text(secrets.token_urlsafe(48))
        f.chmod(0o600)
    return f.read_text().strip()


SECRET_KEY = _secret()

# 登录方式：cas = 数字人大统一身份认证；local = 工号＋平台口令（统一认证接入前临时使用）；
# dev = 开发测试登录（输入工号即可，禁止用于生产）
AUTH_MODE = os.getenv("PE_AUTH_MODE", "dev")
CAS_SERVER_URL = os.getenv("PE_CAS_SERVER_URL", "")  # 例如 https://cas.example.edu.cn/cas
CAS_SERVICE_URL = os.getenv("PE_CAS_SERVICE_URL", "")  # 本平台回调地址，例如 https://pe.example.edu.cn/auth/cas/callback
# 体育部管理员工号，逗号分隔；也可在管理端“账号”里维护
ADMIN_STAFF_NOS = {s.strip() for s in os.getenv("PE_ADMIN_STAFF_NOS", "").split(",") if s.strip()}

HTTPS_ONLY = os.getenv("PE_HTTPS_ONLY", "0") == "1"
LOGIN_MAX_FAILS = 5
LOGIN_LOCK_MINUTES = 15
