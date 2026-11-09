import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="pe-test-")
os.environ["PE_DATA_DIR"] = _tmp
os.environ["PE_AUTH_MODE"] = "dev"
os.environ["PE_ADMIN_STAFF_NOS"] = "admin01"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
