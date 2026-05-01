"""Admin endpoints: backup, restore, health, printer detection."""

from __future__ import annotations

import io
import sqlite3
import zipfile


def test_health_reports_backend(client):
    resp = client.get("/admin/health")
    assert resp.status_code == 200
    j = resp.json()
    assert j["ok"] is True
    assert j["pdf_backend"] in ("docx", "fillable_pdf")
    assert isinstance(j["printing"], bool)


def test_backup_zip_contains_db_and_pdfs(client, session, tmp_path):
    from app.models import Request as ReqModel

    # Create a fake PDF on disk so it ends up in the zip.
    from app.config import settings
    settings.pdf_dir.mkdir(parents=True, exist_ok=True)
    (settings.pdf_dir / "request-99.pdf").write_bytes(b"%PDF-1.4 test\n")

    session.add(ReqModel())
    session.commit()

    resp = client.get("/admin/backup")
    assert resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = set(zf.namelist())
    assert "hazreq.db" in names
    assert "pdfs/request-99.pdf" in names


def test_restore_validates_schema(client, tmp_path):
    # Build a junk SQLite file (not our schema).
    bad = tmp_path / "bad.db"
    conn = sqlite3.connect(str(bad))
    conn.execute("CREATE TABLE only_one (x INT)")
    conn.commit()
    conn.close()

    resp = client.post(
        "/admin/restore",
        files={"file": ("bad.db", bad.read_bytes(), "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert "missing tables" in resp.text.lower()


def test_restore_accepts_valid_backup(client, session, tmp_path):
    """Round-trip: backup → restore → state restored."""
    from app.models import SPMIG

    session.add(SPMIG(code="SPMIG-RESTORE"))
    session.commit()

    backup = client.get("/admin/backup")
    with zipfile.ZipFile(io.BytesIO(backup.content)) as zf:
        db_bytes = zf.read("hazreq.db")

    # Wipe the SPMIG to prove restore brings it back.
    session.query(SPMIG).delete()
    session.commit()
    assert session.query(SPMIG).count() == 0
    # Release this session's connection BEFORE the restore swaps the
    # DB file underneath us — SQLite file substitution mid-connection
    # is undefined behavior.
    session.close()

    resp = client.post(
        "/admin/restore",
        files={"file": ("hazreq.db", db_bytes, "application/octet-stream")},
    )
    assert resp.status_code in (303, 200)

    # Re-open a session against the now-replaced DB file.
    from app.db import SessionLocal
    s = SessionLocal()
    try:
        assert s.query(SPMIG).filter_by(code="SPMIG-RESTORE").count() == 1
    finally:
        s.close()
