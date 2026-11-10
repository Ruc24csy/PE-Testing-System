"""数据模型。学期是全部数据的顶层维度。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint, create_engine, event)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import DATABASE_URL


def now() -> datetime:
    return datetime.now()


class Base(DeclarativeBase):
    pass


class User(Base):
    """登录过或被导入的教师/管理员。工号即数字人大账号。"""
    __tablename__ = "users"
    staff_no: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # 本地口令仅用于应急管理员账号（统一认证不可用时）；首次登录须修改
    password_hash: Mapped[str | None] = mapped_column(String(256))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account: Mapped[str] = mapped_column(String(64), index=True)
    ip: Mapped[str] = mapped_column(String(64), default="")
    success: Mapped[bool] = mapped_column(Boolean)
    at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class Semester(Base):
    __tablename__ = "semesters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)  # 2026-2027-1
    name: Mapped[str] = mapped_column(String(64))  # 2026-2027学年秋季学期
    start_year: Mapped[int] = mapped_column(Integer)
    season: Mapped[str] = mapped_column(String(8))  # fall / spring
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    entry_open: Mapped[bool] = mapped_column(Boolean, default=False)  # 以往学期是否开放补录
    config: Mapped[dict] = mapped_column(JSON)  # 体测项目配置（默认值）
    standard_version: Mapped[str] = mapped_column(String(16), default="GB2014")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class TeachingClass(Base):
    __tablename__ = "classes"
    __table_args__ = (UniqueConstraint("semester_id", "class_no"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    semester_id: Mapped[int] = mapped_column(ForeignKey("semesters.id"), index=True)
    class_no: Mapped[str] = mapped_column(String(32))  # 教学班号，唯一关联键
    course_code: Mapped[str] = mapped_column(String(32), default="")
    course_name: Mapped[str] = mapped_column(String(64), default="")
    class_name: Mapped[str] = mapped_column(String(128), default="")
    course_nature: Mapped[str] = mapped_column(String(32), default="")
    teacher_name: Mapped[str] = mapped_column(String(64), default="")
    staff_no: Mapped[str] = mapped_column(String(32), default="", index=True)
    teacher_pending: Mapped[bool] = mapped_column(Boolean, default=False)  # 教师待补
    location: Mapped[str] = mapped_column(String(128), default="")
    weekday: Mapped[str] = mapped_column(String(16), default="")
    periods: Mapped[str] = mapped_column(String(32), default="")
    weeks: Mapped[str] = mapped_column(String(64), default="")
    enrolled_count: Mapped[int] = mapped_column(Integer, default=0)
    audience: Mapped[str] = mapped_column(String(256), default="")
    campus: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(16), default="未开始")  # 未开始/录入中/已提交
    config: Mapped[dict | None] = mapped_column(JSON)  # 本班覆盖的项目与权重，空则用学期默认
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    submitted_by: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    semester: Mapped[Semester] = relationship()


class Student(Base):
    """学生基础数据（来自《体测学生基础数据》），跨学期共用。"""
    __tablename__ = "students"
    student_no: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    gender: Mapped[str | None] = mapped_column(String(2))  # M / F / None
    college: Mapped[str] = mapped_column(String(64), default="")
    class_name: Mapped[str] = mapped_column(String(128), default="")
    grade: Mapped[str] = mapped_column(String(16), default="")
    major: Mapped[str] = mapped_column(String(128), default="")
    # 管理员手动补录或修改过：之后导入学生基础数据表时不覆盖这条
    manual: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class Enrollment(Base):
    """某学期某教学班的一名学生（来自学生选课表）。"""
    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("class_id", "student_no"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    semester_id: Mapped[int] = mapped_column(ForeignKey("semesters.id"), index=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"), index=True)
    student_no: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    class_label: Mapped[str] = mapped_column(String(128), default="")  # 上课班级
    major: Mapped[str] = mapped_column(String(128), default="")
    grade_field: Mapped[str] = mapped_column(String(16), default="")  # 上课年级（原值）
    college: Mapped[str] = mapped_column(String(64), default="")
    campus: Mapped[str] = mapped_column(String(32), default="")
    course_category: Mapped[str] = mapped_column(String(32), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)  # 一人多条选课时，体育部可停用多余记录
    seq: Mapped[int] = mapped_column(Integer, default=0)
    info_error: Mapped[str | None] = mapped_column(String(256))  # 教师标记“信息有误”的说明
    # 管理员手动添加、修改、调班或移除过：之后覆盖导入选课表时不改动这条（不改字段、不删除、不恢复）
    manual: Mapped[bool] = mapped_column(Boolean, default=False)

    teaching_class: Mapped[TeachingClass] = relationship()


class GradeCorrection(Base):
    """年级校正名单（学籍异动记录），判定标准档的第一优先依据。"""
    __tablename__ = "grade_corrections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_no: Mapped[str] = mapped_column(String(16), index=True)
    cohort: Mapped[int | None] = mapped_column(Integer)  # 实际跟读的年级（如 2024 表示随2024级）
    band: Mapped[str | None] = mapped_column(String(4))  # 直接指定标准档 12/34（特殊学籍用）
    change_type: Mapped[str] = mapped_column(String(32), default="")  # 休学/降级/跳级/转专业重读/提前毕业/体育部指定…
    effective_semester: Mapped[str] = mapped_column(String(16), default="")  # 生效学年学期代码
    note: Mapped[str] = mapped_column(Text, default="")
    operator: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class StandardSnapshot(Base):
    """每学期“一人一档”标准档快照。"""
    __tablename__ = "standard_snapshots"
    __table_args__ = (UniqueConstraint("semester_id", "student_no"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    semester_id: Mapped[int] = mapped_column(ForeignKey("semesters.id"), index=True)
    student_no: Mapped[str] = mapped_column(String(16), index=True)
    grade_num: Mapped[int | None] = mapped_column(Integer)  # 大几
    band: Mapped[str | None] = mapped_column(String(4))  # 12 / 34
    source: Mapped[str] = mapped_column(String(16), default="")  # 异动记录/上课年级/学号推算
    detail: Mapped[dict] = mapped_column(JSON, default=dict)  # 来源字段取值、命中规则
    pending: Mapped[bool] = mapped_column(Boolean, default=False)
    pending_reasons: Mapped[list] = mapped_column(JSON, default=list)
    changed_notice: Mapped[bool] = mapped_column(Boolean, default=False)  # 学期中标准档被变更
    decided_by: Mapped[str] = mapped_column(String(32), default="system")
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ScoreRecord(Base):
    __tablename__ = "scores"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(ForeignKey("enrollments.id"), unique=True)
    a_raw: Mapped[float | None] = mapped_column(Float)
    a_status: Mapped[str] = mapped_column(String(8), default="正常")
    b_raw: Mapped[float | None] = mapped_column(Float)
    b_status: Mapped[str] = mapped_column(String(8), default="正常")
    skill: Mapped[float | None] = mapped_column(Float)
    regular: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str] = mapped_column(String(256), default="")  # 作弊/违纪原因等
    computed: Mapped[dict] = mapped_column(JSON, default=dict)
    standard_version: Mapped[str] = mapped_column(String(16), default="GB2014")
    updated_by: Mapped[str] = mapped_column(String(32), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ScoreHistory(Base):
    """成绩留痕：每次修改的原值、新值、换算结果、档位、评分表版本与操作人。"""
    __tablename__ = "score_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(Integer, index=True)
    field: Mapped[str] = mapped_column(String(16))
    old: Mapped[str | None] = mapped_column(String(64))
    new: Mapped[str | None] = mapped_column(String(64))
    computed: Mapped[dict] = mapped_column(JSON, default=dict)
    standard_version: Mapped[str] = mapped_column(String(16), default="")
    batch: Mapped[str | None] = mapped_column(String(40), index=True)  # 批量导入批次，用于回退
    operator: Mapped[str] = mapped_column(String(32))
    at: Mapped[datetime] = mapped_column(DateTime, default=now)


class UnlockRequest(Base):
    __tablename__ = "unlock_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(8), default="待审核")  # 待审核/已同意/已驳回
    requester: Mapped[str] = mapped_column(String(32))
    reviewer: Mapped[str | None] = mapped_column(String(32))
    review_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    semester_id: Mapped[int | None] = mapped_column(Integer, index=True)
    actor: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(32), index=True)  # 保存/提交/撤回/改档/导入/导出/配置…
    target: Mapped[str] = mapped_column(String(128), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str] = mapped_column(String(64), default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class Setting(Base):
    """键值设置，例如导入字段映射的记忆。"""
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


engine = create_engine(DATABASE_URL, future=True,
                       connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})

if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(conn, _):
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

SessionLocal = sessionmaker(engine, expire_on_commit=False)


def init_db():
    # 多个服务进程同时启动时串行建表、补列
    import fcntl
    from .config import DATA_DIR
    with open(DATA_DIR / ".init.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        Base.metadata.create_all(engine)
        _add_missing_columns()


def _add_missing_columns():
    """老库升级：给已有的表补上新增的列（只加列，不改不删）。"""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = col.type.compile(engine.dialect)
                default = col.default.arg if col.default is not None and not callable(col.default.arg) else None
                sql = f'ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl}'
                if isinstance(default, bool):
                    sql += f" NOT NULL DEFAULT {'1' if default else '0'}" if engine.dialect.name == "sqlite" \
                        else f" NOT NULL DEFAULT {'true' if default else 'false'}"
                conn.execute(text(sql))
