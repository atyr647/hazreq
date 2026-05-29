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
from app.models import AuditLog
from app.services import csvio
from app.services import jsonio
from app.services.pdf import _unoserver_alive
from app.services.printer import default_printer, is_available as printing_available, list_printers
from app.templating import render

from sqlalchemy import select

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
        "pdf_backend": settings.pdf_backend,
        "overlay_present": settings.overlay_pdf_path.exists(),
        "fillable_pdf_present": (
            settings.fillable_pdf_path is not None and settings.fillable_pdf_path.exists()
        ),
        "pdf_dir": str(settings.pdf_dir),
        "backup_dir": str(settings.backup_dir),
        "db_path": str(db_path) if db_path else "(non-sqlite)",
        "db_size_mb": round(db_size / (1024 * 1024), 2),
        "pdf_count": pdf_count,
        "printing_available": printing_available(),
        "printers": list_printers(),
        "default_printer": default_printer(),
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
        # Also remove the WAL/SHM sidecars so SQLite doesn't replay stale
        # transactions against the new main file.
        engine.dispose()
        shutil.copyfile(tmp_path, db_path)
        for sidecar in (db_path.with_suffix(db_path.suffix + "-wal"),
                        db_path.with_suffix(db_path.suffix + "-shm")):
            if sidecar.exists():
                sidecar.unlink()
    finally:
        tmp_path.unlink(missing_ok=True)

    return RedirectResponse("/admin/?restored=1", status_code=303)


# ============================================================
# JSON whole-DB export / import (browser-storage friendly)
# ============================================================

@router.get("/export.json", name="admin_export_json")
def admin_export_json(db: Session = Depends(get_session)):
    payload = jsonio.export_to_dict(db)
    name = f"hazreq-backup-{datetime.now().strftime('%Y%m%d-%H%M')}.json"
    import json as _json
    body = _json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/import.json", name="admin_import_json")
async def admin_import_json(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty upload")
    import json as _json
    try:
        payload = _json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, _json.JSONDecodeError) as e:
        raise HTTPException(400, f"Not valid JSON: {e}") from None
    try:
        counts = jsonio.import_from_dict(db, payload)
    except jsonio.InvalidBackup as e:
        raise HTTPException(400, str(e)) from None
    log.info("JSON restore: %s", counts)
    return RedirectResponse("/admin/?restored=1", status_code=303)


@router.get("/health", name="admin_health")
def admin_health():
    payload = {
        "ok": True,
        "unoserver": _unoserver_alive(),
        "template": settings.template_path.exists(),
        "db": _db_path().exists() if _db_path() else False,
        "pdf_backend": settings.pdf_backend,
        "printing": printing_available(),
    }
    return payload


# ============================================================
# CSV catalog export / import
# ============================================================

@router.get("/catalog/export", name="admin_catalog_export")
def admin_catalog_export(db: Session = Depends(get_session)):
    payload = csvio.export_zip(db)
    name = f"hazreq-catalog-{datetime.now().strftime('%Y%m%d-%H%M')}.zip"
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/catalog/import", name="admin_catalog_import")
async def admin_catalog_import(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
):
    payload = await file.read()
    if not payload:
        raise HTTPException(400, "Empty upload")
    try:
        report = csvio.import_zip(db, payload)
    except zipfile.BadZipFile:
        raise HTTPException(400, "Upload is not a valid .zip file") from None
    log.info("CSV import: %s; errors=%d", report.summary(), len(report.errors))
    return render(
        request,
        "admin_import_result.html",
        {"report": report},
        nav="admin",
    )


# ============================================================
# Audit log viewer
# ============================================================

@router.get("/log", name="admin_log")
def admin_log(
    request: Request,
    db: Session = Depends(get_session),
    entity_type: str = "",
    action: str = "",
    q: str = "",
    limit: int = 200,
):
    stmt = select(AuditLog).order_by(AuditLog.ts.desc())
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            (AuditLog.entity_key.ilike(like)) | (AuditLog.summary.ilike(like))
        )
    rows = db.execute(stmt.limit(max(1, min(limit, 1000)))).scalars().all()
    return render(
        request,
        "admin_log.html",
        {
            "rows": rows,
            "entity_type": entity_type,
            "action": action,
            "q": q,
        },
        nav="admin",
    )
