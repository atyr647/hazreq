"""In-process admin data helpers for the Tk app: health snapshot + audit
log read. Backup/restore and CSV/JSON import-export reuse the existing
services (app.services.backup / csvio / jsonio) directly from the screen.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AuditLog
from app.services import backup as backup_service
from app.services import printer as printer_service


def health_info() -> dict:
    dbp = backup_service.db_path()
    size = dbp.stat().st_size if dbp and dbp.exists() else 0
    pdf_count = sum(1 for _ in settings.pdf_dir.glob("*.pdf")) if settings.pdf_dir.exists() else 0
    return {
        "db_path": str(dbp) if dbp else "(non-sqlite)",
        "db_size_mb": round(size / (1024 * 1024), 2),
        "pdf_backend": settings.pdf_backend,
        "overlay_present": settings.overlay_pdf_path.exists(),
        "pdf_dir": str(settings.pdf_dir),
        "pdf_count": pdf_count,
        "backup_dir": str(settings.backup_dir),
        "printing": printer_service.is_available(),
        "printers": printer_service.list_printers(),
        "default_printer": printer_service.default_printer() or "(CUPS default)",
    }


@dataclass(frozen=True)
class AuditRow:
    ts: str
    action: str
    entity_type: str
    entity_key: str
    summary: str


def list_audit(
    s: Session, entity_type: str = "", action: str = "", q: str = "", limit: int = 200
) -> list[AuditRow]:
    stmt = select(AuditLog).order_by(AuditLog.ts.desc())
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(AuditLog.entity_key.ilike(like) | AuditLog.summary.ilike(like))
    rows = s.execute(stmt.limit(max(1, min(limit, 1000)))).scalars().all()
    return [
        AuditRow(
            ts=r.ts.strftime("%Y-%m-%d %H:%M:%S") if r.ts else "",
            action=r.action,
            entity_type=r.entity_type,
            entity_key=r.entity_key,
            summary=r.summary or "",
        )
        for r in rows
    ]
