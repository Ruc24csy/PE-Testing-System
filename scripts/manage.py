"""运维命令。

  python scripts/manage.py init                         建表
  python scripts/manage.py create-admin <工号> <姓名>     创建应急管理员（随机初始口令，首次登录须修改）
  python scripts/manage.py set-admin <工号>              把某个统一认证账号设为体育部管理员
  python scripts/manage.py import <学期> <课程安排.xlsx> <学生选课.xlsx> <学生基础数据.xlsx>
                                                        命令行一次导入三张表（自动建学期、按覆盖方式）
  python scripts/manage.py backup                       备份数据库到 data/backups/
"""
import secrets
import shutil
import string
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import auth, config, importer, scoring  # noqa: E402
from app.models import SessionLocal, Semester, User, init_db  # noqa: E402


def cmd_init():
    init_db()
    print("数据库已就绪：", config.DATABASE_URL)


def cmd_create_admin(staff_no, name):
    init_db()
    alphabet = string.ascii_letters + string.digits + "#@%+"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(12))
        if not auth.password_ok(pw):
            break
    db = SessionLocal()
    u = db.get(User, staff_no) or User(staff_no=staff_no)
    u.name, u.is_admin = name, True
    u.password_hash, u.must_change_password = auth.hash_password(pw), True
    db.merge(u)
    db.commit()
    print(f"应急管理员 {staff_no} 已创建，初始口令：{pw}（首次登录须修改）")


def cmd_set_admin(staff_no):
    init_db()
    db = SessionLocal()
    u = db.get(User, staff_no) or User(staff_no=staff_no)
    u.is_admin = True
    db.merge(u)
    db.commit()
    print(f"{staff_no} 已设为体育部管理员")


def cmd_import(label, schedule, enroll, base):
    init_db()
    db = SessionLocal()
    code, name, y, season = importer.parse_semester(label)
    sem = db.scalar(select(Semester).where(Semester.code == code))
    if sem is None:
        first = db.scalar(select(Semester)) is None
        sem = Semester(code=code, name=name, start_year=y, season=season, is_current=first,
                       config=scoring.default_config(season))
        db.add(sem)
        db.commit()
    for kind, path in (("base", base), ("schedule", schedule), ("enroll", enroll)):
        token = importer.save_upload(Path(path).read_bytes(), path)
        info = importer.inspect_file(db, token, kind)
        sh = info["sheets"][0]
        rows, footers = importer.read_rows(token, sh["name"], sh["header_row"], sh["mapping"], kind)
        rep, plan = importer.precheck(db, kind, rows, sh["mapping"], sem, "overwrite")
        print(f"== {importer.KIND_NAMES[kind]}：{len(rows)} 行，跳过表尾 {footers} 行，{plan}")
        for it in rep.items:
            print(f"   [{it['level']}] {it['message'][:160]}")
        if rep.blocking:
            print("   有阻断性错误，停止导入")
            return
        print("   ", importer.commit(db, kind, rows, sem, "overwrite", "cli"))
        db.commit()


def cmd_backup():
    if not config.DATABASE_URL.startswith("sqlite:///"):
        print("非 SQLite 数据库请用数据库自带的备份工具（如 pg_dump）")
        return
    src = Path(config.DATABASE_URL.removeprefix("sqlite:///"))
    dst_dir = config.DATA_DIR / "backups"
    dst_dir.mkdir(exist_ok=True)
    dst = dst_dir / f"pe_scores-{datetime.now():%Y%m%d-%H%M%S}.db"
    import sqlite3
    with sqlite3.connect(src) as a, sqlite3.connect(dst) as b:
        a.backup(b)
    print("已备份到", dst)


if __name__ == "__main__":
    cmds = {"init": cmd_init, "create-admin": cmd_create_admin, "set-admin": cmd_set_admin,
            "import": cmd_import, "backup": cmd_backup}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__)
        sys.exit(1)
    cmds[sys.argv[1]](*sys.argv[2:])
