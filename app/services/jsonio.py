"""Whole-DB JSON dump / load.

Used by /admin/export.json and /admin/import.json in browser-storage
mode (and available in any mode — the endpoints aren't gated). The
envelope carries a schema discriminator so future backups stay
recognisable and old ones can be rejected explicitly rather than
silently coerced.

Round-trip targets EVERY table including audit_log: nothing is left on
the server in browser-storage mode, so the JSON has to carry the full
state.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    MIP,
    MRC,
    SPMIG,
    AuditLog,
    HazmatItem,
    MRCItem,
    Request,
    RequestLine,
)

SCHEMA_VERSION = 1
ENVELOPE_TYPE = "hazreq-backup"

# Tables in dependency order. Insert order on import = this list;
# delete order = reversed. Snapshot fields on RequestLine + AuditLog
# don't FK-cascade so they're safe to write any time.
_TABLES: list[tuple[str, type, tuple[str, ...]]] = [
    ("mips", MIP, ("id", "code", "title", "notes", "created_at", "updated_at")),
    ("spmigs", SPMIG, ("id", "code", "description", "notes", "created_at", "updated_at")),
    (
        "hazmat_items", HazmatItem,
        ("id", "spmig_id", "nomenclature", "niin", "unit_of_issue", "notes",
         "created_at", "updated_at"),
    ),
    (
        "mrcs", MRC,
        ("id", "mip_id", "code", "periodicity", "description",
         "created_at", "updated_at"),
    ),
    ("mrc_items", MRCItem, ("mrc_id", "hazmat_item_id", "sort_order")),
    (
        "requests", Request,
        ("id", "finalized_at", "datetime_of_request", "lpo", "workcenter",
         "requestor_name", "hazmat_location", "source_mip_id", "source_mrc_id",
         "pdf_path", "created_at", "updated_at"),
    ),
    (
        "request_lines", RequestLine,
        ("id", "request_id", "sort_order", "hazmat_item_id", "spmig_code",
         "nomenclature", "niin", "qty"),
    ),
    (
        "audit_log", AuditLog,
        ("id", "ts", "action", "entity_type", "entity_id", "entity_key",
         "summary", "details_json"),
    ),
]


def _to_jsonable(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def _from_jsonable(model: type, field: str, v: Any) -> Any:
    if v is None:
        return None
    col = model.__table__.columns.get(field)
    if col is None:
        return v
    coltype = type(col.type).__name__
    if coltype == "DateTime" and isinstance(v, str):
        return datetime.fromisoformat(v)
    return v


def export_to_dict(db: Session) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "type": ENVELOPE_TYPE,
        "exportedAt": datetime.utcnow().isoformat(),
    }
    for key, model, fields in _TABLES:
        rows = db.query(model).all()
        payload[key] = [
            {f: _to_jsonable(getattr(r, f)) for f in fields} for r in rows
        ]
    return payload


class InvalidBackup(ValueError):
    pass


def _validate_envelope(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise InvalidBackup("Backup is not a JSON object")
    if payload.get("type") != ENVELOPE_TYPE:
        raise InvalidBackup(f"Wrong envelope type: {payload.get('type')!r}")
    sv = payload.get("schemaVersion")
    if sv != SCHEMA_VERSION:
        raise InvalidBackup(
            f"Unsupported schemaVersion {sv!r} (expected {SCHEMA_VERSION})"
        )


def import_from_dict(db: Session, payload: dict[str, Any]) -> dict[str, int]:
    """Replace the entire DB content with payload. Returns row counts.

    Wipes every table in reverse-dependency order, then inserts in
    dependency order. Keeps the original primary keys and timestamps so
    audit_log entity_id references remain meaningful.
    """
    _validate_envelope(payload)

    # Suppress the audit listener for this transaction — otherwise the
    # bulk INSERTs would emit a fresh wave of "create" rows that
    # duplicate (and trail) the audit_log we're restoring.
    db.info["skip_audit"] = True
    try:
        # Wipe in reverse dependency order so child rows go before parents.
        for key, model, _fields in reversed(_TABLES):
            db.query(model).delete()
        db.flush()

        counts: dict[str, int] = {}
        for key, model, fields in _TABLES:
            rows = payload.get(key, []) or []
            if not isinstance(rows, list):
                raise InvalidBackup(f"{key!r} is not a list")
            for raw in rows:
                if not isinstance(raw, dict):
                    raise InvalidBackup(f"{key!r} contains a non-object row")
                cleaned = {
                    f: _from_jsonable(model, f, raw.get(f)) for f in fields if f in raw
                }
                db.add(model(**cleaned))
            # Flush per-table so FKs resolve in dependency order regardless
            # of how SQLAlchemy orders objects inside a single flush.
            db.flush()
            counts[key] = len(rows)
        db.commit()
        return counts
    finally:
        db.info.pop("skip_audit", None)
