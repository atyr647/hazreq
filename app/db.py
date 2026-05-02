"""SQLAlchemy engine + session. SQLite is opened in WAL mode with
foreign-key enforcement on every connection.

In default mode there is one process-wide engine bound to settings.db_url.
In browser-storage mode (HAZREQ_BROWSER_STORAGE=1) the middleware tags
each request with its own per-cookie engine; get_session() then routes
the dependency to that engine instead of the default. The default engine
still exists in browser-storage mode — it just isn't used by request
handlers — so module-level `engine` / `SessionLocal` stays valid for
tests and ad-hoc tooling.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from fastapi import Request
from sqlalchemy import DateTime, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


def _attach_sqlite_pragmas(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def _build_engine(url: str) -> Engine:
    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_engine(url, future=True, connect_args=connect_args)
    if url.startswith("sqlite"):
        _attach_sqlite_pragmas(engine)
    return engine


def _make_engine() -> Engine:
    return _build_engine(settings.db_url)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

# Audit listener is wired on the Session class once. Any sessionmaker
# (default or per-cookie) inherits it without re-registration.
from app.services import audit as _audit  # noqa: E402

_audit.install(Session)


# ----- Per-cookie session DBs (browser-storage mode only) ------------

_session_lock = threading.Lock()
_session_engines: dict[str, Engine] = {}
_session_makers: dict[str, sessionmaker] = {}


def _migrate(url: str) -> None:
    """Apply alembic migrations to the given DB URL.

    Imported lazily so the alembic dep doesn't load for every import of
    db.py — only when a fresh per-cookie DB is being initialised.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.set_main_option("script_location", "app/migrations")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


def _session_db_url(session_id: str) -> str:
    settings.sessions_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{settings.sessions_dir / f'{session_id}.db'}"


def engine_for_session(session_id: str) -> Engine:
    """Return (and lazily create + migrate) the per-cookie engine."""
    with _session_lock:
        eng = _session_engines.get(session_id)
        if eng is not None:
            return eng
        url = _session_db_url(session_id)
        _migrate(url)
        eng = _build_engine(url)
        _session_engines[session_id] = eng
        _session_makers[session_id] = sessionmaker(
            bind=eng, autoflush=False, autocommit=False, future=True
        )
        return eng


def sessionmaker_for_session(session_id: str) -> sessionmaker:
    if session_id not in _session_makers:
        engine_for_session(session_id)
    return _session_makers[session_id]


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency yielding a Session.

    In browser-storage mode the middleware sets request.state.session_id;
    we look up the per-cookie sessionmaker. Otherwise fall back to the
    module-level SessionLocal that's bound to settings.db_url.
    """
    sid = getattr(request.state, "session_id", None)
    maker = sessionmaker_for_session(sid) if sid else SessionLocal
    s = maker()
    try:
        yield s
    finally:
        s.close()
