from __future__ import annotations

import io
import logging
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import engine, get_session
from app.services.pdf import _unoserver_alive
from app.templating import render

router = APIRouter()
log = logging.getLogger(__name__)


def _db_path() -> Path | None:
    if settings.db_url.startswith("sqlite:///"):
        return Path(settings.db_url.replace("sqlite:///", "", 1)).resolve()
    return None


@router.get("/", name="admin_home")
def admin_home(request: Request, db: Session = Depends(get_session)):
    db_path = _db_path()
    db_size = db_path.stat().st_size if db_path and db_path.exists() else 0
    pdf_count = sum(1 for _ in settings.pdf_dir.glob("*.pdf")) if settings.pdf_dir.exists() else 0
    backups = (
        sorted(settings.backup_dir.glob("*.db"), reverse=True)[:5]
        if settings.backup_dir.exists()
        else []
    )
    health = {
        "db_writable": db_path is not None and (not db_path.exists() or db_path.parent.exists()),
        "unoserver_alive": _unoserver_alive(),
        "template_present": settings.template_path.exists(),
        "pdf_dir": str(settings.pdf_dir),
        "backup_dir": str(settings.backup_dir),
        "db_path": str(db_path) if db_path else "(non-sqlite)",
        "db_size_mb": round(db_size / (1024 * 1024), 2),
        "pdf_count": pdf_count,
    }
    return render(
        request, "admin.html", {"health": health, "backups": backups}, nav="admin"
    )


@router.get("/backup", name="admin_backup")
def admin_backup():
    """Stream a .zip containing the SQLite DB (via .backup) plus all PDFs."""
    db_path = _db_path()
    if not db_path or not db_path.exists():
        raise HTTPException(400, "No SQLite DB to back up")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_db = Path(tmp) / "hazreq.db"
            src = sqlite3.connect(str(db_path))
            dst = sqlite3.connect(str(tmp_db))
            src.backup(dst)
            dst.close()
            src.close()
            zf.write(tmp_db, "hazreq.db")
        if settings.pdf_dir.exists():
            for pdf in sorted(settings.pdf_dir.glob("*.pdf")):
                zf.write(pdf, f"pdfs/{pdf.name}")

    buf.seek(0)
    name = f"hazreq-backup-{datetime.now().strftime('%Y%m%d-%H%M')}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/restore", name="admin_restore")
async def admin_restore(file: UploadFile = File(...)):
    db_path = _db_path()
    if not db_path:
        raise HTTPException(400, "Restore only supported for SQLite deployments")

    payload = await file.read()
    if not payload:
        raise HTTPException(400, "Empty upload")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    try:
        # Validate it's a SQLite file with the expected core tables.
        conn = sqlite3.connect(str(tmp_path))
        try:
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        finally:
            conn.close()
        required = {"mip", "mrc", "spmig", "hazmat_item", "mrc_item", "request", "request_line"}
        missing = required - tables
        if missing:
            raise HTTPException(400, f"Backup missing tables: {sorted(missing)}")

        # Swap atomically; current connections will pick up the new DB
        # on next checkout (engine.dispose drops all pooled connections).
        engine.dispose()
        shutil.copyfile(tmp_path, db_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    return RedirectResponse("/admin/?restored=1", status_code=303)


@router.get("/health", name="admin_health")
def admin_health():
    payload = {
        "ok": True,
        "unoserver": _unoserver_alive(),
        "template": settings.template_path.exists(),
        "db": _db_path().exists() if _db_path() else False,
    }
    return payload
