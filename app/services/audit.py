"""Automatic audit logging for catalog mutations.

Hooked via SQLAlchemy `before_flush`. Walks the session's pending
inserts / updates / deletes for catalog entities and emits AuditLog
rows in the same flush. Operational tables (Request, RequestLine,
AuditLog itself) are intentionally excluded.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


# Model-keyed tables get populated on first flush via _ensure_tables().
# Importing app.models at module load triggers a circular import any
# time db.py is the first leg of the import chain (db imports this
# module to wire the listener, this module would then re-enter models
# which is mid-import).
_ENTITIES: dict = {}
_FIELDS: dict = {}


def _ensure_tables() -> None:
    if _ENTITIES:
        return
    from app.models import MIP, MRC, SPMIG, HazmatItem, MRCItem  # noqa: PLC0415

    _ENTITIES.update({
        MIP: ("mip", lambda o: o.code),
        MRC: ("mrc", lambda o: f"{o.mip.code if o.mip else o.mip_id}/{o.code}"),
        SPMIG: ("spmig", lambda o: o.code),
        HazmatItem: ("hazmat_item", lambda o: f"{o.spmig.code if o.spmig else o.spmig_id}: {o.nomenclature}"),
        MRCItem: ("mrc_item", lambda o: f"{o.mrc_id}↔{o.hazmat_item_id}"),
    })
    _FIELDS.update({
        MIP: ("code", "title", "notes"),
        MRC: ("mip_id", "code", "periodicity", "description"),
        SPMIG: ("code", "description", "notes"),
        HazmatItem: ("spmig_id", "nomenclature", "niin", "unit_of_issue", "notes"),
        MRCItem: ("mrc_id", "hazmat_item_id", "sort_order"),
    })


def _snapshot(obj: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {f: getattr(obj, f, None) for f in fields}


def _changed_fields(obj: Any, fields: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """Return {field: {"from": old, "to": new}} for actually changed fields."""
    state = inspect(obj)
    changed: dict[str, dict[str, Any]] = {}
    for f in fields:
        attr = state.attrs.get(f)
        if attr is None:
            continue
        history = attr.history
        if history.has_changes():
            old = history.deleted[0] if history.deleted else None
            new = history.added[0] if history.added else getattr(obj, f)
            changed[f] = {"from": old, "to": new}
    return changed


def _safe_key(extractor, obj) -> str:
    try:
        return str(extractor(obj))
    except Exception:  # noqa: BLE001
        return f"<{type(obj).__name__}#{getattr(obj, 'id', '?')}>"


def _summary(action: str, entity_type: str, key: str, changed: dict | None = None) -> str:
    if action == "update" and changed:
        return f"Updated {entity_type} {key}: " + ", ".join(sorted(changed.keys()))
    return f"{action.capitalize()} {entity_type} {key}"


def _log_row(action: str, model_cls, obj, *, changed: dict | None = None):
    from app.models import AuditLog  # noqa: PLC0415

    entity_type, key_fn = _ENTITIES[model_cls]
    fields = _FIELDS[model_cls]
    if action == "update":
        details = changed or {}
    else:
        details = _snapshot(obj, fields)
    key = _safe_key(key_fn, obj)
    return AuditLog(
        ts=datetime.utcnow(),
        action=action,
        entity_type=entity_type,
        entity_id=getattr(obj, "id", None),
        entity_key=key,
        summary=_summary(action, entity_type, key, changed),
        details_json=json.dumps(details, default=str),
    )


def _on_before_flush(session: Session, flush_context, instances) -> None:  # noqa: ARG001
    if session.info.get("skip_audit"):
        return
    _ensure_tables()
    from app.models import AuditLog  # noqa: PLC0415

    new_rows: list = []
    for obj in list(session.new):
        cls = type(obj)
        if cls is AuditLog or cls not in _ENTITIES:
            continue
        new_rows.append(_log_row("create", cls, obj))
    for obj in list(session.dirty):
        cls = type(obj)
        if cls is AuditLog or cls not in _ENTITIES:
            continue
        if not session.is_modified(obj, include_collections=False):
            continue
        changed = _changed_fields(obj, _FIELDS[cls])
        if not changed:
            continue
        new_rows.append(_log_row("update", cls, obj, changed=changed))
    for obj in list(session.deleted):
        cls = type(obj)
        if cls is AuditLog or cls not in _ENTITIES:
            continue
        new_rows.append(_log_row("delete", cls, obj))
    for row in new_rows:
        session.add(row)


def install(session_factory) -> None:
    """Wire the before_flush listener onto a sessionmaker."""
    if getattr(install, "_installed", False):
        return
    event.listen(session_factory, "before_flush", _on_before_flush)
    install._installed = True
