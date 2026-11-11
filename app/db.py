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


def create_all() -> None:
    from app import models  # noqa: F401  (register tables)

    Base.metadata.create_all(get_engine())


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
