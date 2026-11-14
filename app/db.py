"""Database engine/session management."""
from datetime import datetime, timezone
from typing import Iterator, Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)
_engine: Optional[Engine] = None


def utcnow() -> datetime:
    """Naive UTC timestamp (stored as UTC everywhere)."""
    now = datetime.utcnow()
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def to_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None or dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def init_engine(url: str) -> Engine:
    global _engine
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url or url == "sqlite://":
            kwargs["poolclass"] = StaticPool
    _engine = create_engine(url, future=True, **kwargs)

    if url.startswith("sqlite"):
        @event.listens_for(_engine, "connect")
        def _fk_on(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    SessionLocal.configure(bind=_engine)
    return _engine


def get_engine() -> Engine:
    assert _engine is not None, "init_engine() has not been called"
    return _engine


# Idempotent PostgreSQL upgrades for databases created by earlier versions.
UPGRADES = [
    # v2.1.1: an admin exists before their workspace is created.
    "ALTER TABLE admin_users ALTER COLUMN tenant_id DROP NOT NULL",
    # v2.2: self sign-up, 30-day trial, platform owner, student profiles, monthly fees.
    "ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS is_platform_admin BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'TRIAL'",
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMP",
    "UPDATE tenants SET trial_ends_at = (now() AT TIME ZONE 'utc') + INTERVAL '30 days' WHERE trial_ends_at IS NULL",
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS suspended_reason TEXT",
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS auto_send_links BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE students ADD COLUMN IF NOT EXISTS parent_email VARCHAR(200)",
    "ALTER TABLE students ADD COLUMN IF NOT EXISTS date_of_birth DATE",
    "ALTER TABLE students ADD COLUMN IF NOT EXISTS gender VARCHAR(20)",
    "ALTER TABLE students ADD COLUMN IF NOT EXISTS class_grade VARCHAR(40)",
    "ALTER TABLE students ADD COLUMN IF NOT EXISTS school VARCHAR(200)",
    "ALTER TABLE students ADD COLUMN IF NOT EXISTS address TEXT",
    "ALTER TABLE students ADD COLUMN IF NOT EXISTS joined_on DATE",
    "ALTER TABLE students ADD COLUMN IF NOT EXISTS monthly_fee_paise INTEGER",
    "ALTER TABLE students ADD COLUMN IF NOT EXISTS fee_due_day INTEGER",
    "ALTER TABLE students ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE students ADD COLUMN IF NOT EXISTS created_at TIMESTAMP",
    "ALTER TABLE installments ADD COLUMN IF NOT EXISTS kind VARCHAR(10) NOT NULL DEFAULT 'ONE_OFF'",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_monthly_fee_per_student_month ON installments (student_id, billing_month) "
    "WHERE kind = 'MONTHLY'",
    # v2.3: business (merchant) UPI IDs.
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS upi_is_merchant BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS merchant_code VARCHAR(4)",
]


def create_all() -> None:
    from app import models  # noqa: F401  (register tables)

    engine = get_engine()
    Base.metadata.create_all(engine)
    # Schema upgrades for databases created by earlier versions (create_all never alters tables).
    if engine.dialect.name == "postgresql":
        from sqlalchemy import text

        with engine.begin() as conn:
            for stmt in UPGRADES:
                conn.execute(text(stmt))


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
