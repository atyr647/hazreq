"""FastAPI request dependencies.

`get_session` lives here (not in app.db) so that the data layer — app.db
and everything that imports it, including the native Tk app — has no
dependency on FastAPI/Starlette. Only the web routers import this module.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from app.db import SessionLocal, sessionmaker_for_session


def get_session(request: Request) -> Iterator[Session]:
    """Yield a Session.

    In browser-storage mode the middleware sets request.state.session_id;
    we look up the per-cookie sessionmaker. Otherwise fall back to the
    module-level SessionLocal bound to settings.db_url.
    """
    sid = getattr(request.state, "session_id", None)
    maker = sessionmaker_for_session(sid) if sid else SessionLocal
    s = maker()
    try:
        yield s
    finally:
        s.close()
