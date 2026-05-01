"""Test fixtures.

One shared SQLite file for the whole test run; tables are truncated
between tests so each test starts clean. The PDF converter is stubbed
so tests don't need LibreOffice.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Configure env BEFORE any app module is imported.
_TMP = Path(tempfile.mkdtemp(prefix="hazreq-test-"))
(_TMP / "pdfs").mkdir(exist_ok=True)
(_TMP / "templates").mkdir(exist_ok=True)
os.environ["HAZREQ_DATA_DIR"] = str(_TMP)
os.environ["HAZREQ_DB_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["HAZREQ_PDF_DIR"] = str(_TMP / "pdfs")
os.environ["HAZREQ_BACKUP_DIR"] = str(_TMP / "backups")
os.environ["HAZREQ_TEMPLATE_PATH"] = str(_TMP / "templates" / "hazmat_chit.docx")

import pytest
from alembic import command
from alembic.config import Config


def _migrate() -> None:
    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.set_main_option("script_location", "app/migrations")
    cfg.set_main_option("sqlalchemy.url", os.environ["HAZREQ_DB_URL"])
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
def _setup():
    """Run migrations once for the whole session and prep the docx template."""
    _migrate()
    from app.config import settings

    if not settings.template_path.exists():
        from scripts.prepare_template import main as prep
        prep()
    yield


@pytest.fixture(autouse=True)
def _clean_db():
    """Wipe data tables between tests (preserve schema + alembic_version)."""
    from sqlalchemy import text

    from app.db import engine

    tables = [
        "request_line",
        "request",
        "mrc_item",
        "hazmat_item",
        "mrc",
        "spmig",
        "mip",
    ]
    with engine.begin() as conn:
        for t in tables:
            conn.execute(text(f"DELETE FROM {t}"))
    yield


@pytest.fixture(autouse=True)
def _stub_converter(monkeypatch):
    """Make finalize work without LibreOffice."""
    from app.services import pdf as pdf_service
    monkeypatch.setattr(
        pdf_service, "_convert_docx_to_pdf",
        lambda b: b"%PDF-1.4\n% test\n%%EOF\n",
    )


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def session():
    from app.db import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
