"""In-process data access for the Tk prototype.

These are plain functions over a SQLAlchemy Session — the same queries the
FastAPI routers run, minus HTTP. The Tk UI calls them directly; there is no
server, no port, no JSON. This is the layer the full native port would keep
and the web routers would become thin wrappers around (or be deleted).

Mirrors the relevant bits of app/routers/requests.py and the search
endpoints in app/routers/catalog.py.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import and_, select
from sqlalchemy.orm import Session, selectinload

from app.db import SessionLocal
from app.models import MIP, MRC, SPMIG, HazmatItem, MRCItem, Request, RequestLine
from app.services import printer as printer_service
from app.services.pdf import render_request_pdf

HEADER_FIELDS = ("requestor_name", "workcenter", "lpo", "hazmat_location")


@contextmanager
def session_scope() -> Iterator[Session]:
    """Short-lived session per UI action — same lifecycle the web dependency
    gives each request. Commit on success, rollback on error, always close."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# ---- request lifecycle -------------------------------------------------

def create_draft(s: Session) -> int:
    r = Request(datetime_of_request=datetime.now().replace(microsecond=0, second=0))
    s.add(r)
    s.flush()
    return r.id


def get_request(s: Session, request_id: int) -> Request:
    r = s.execute(
        select(Request).options(selectinload(Request.lines)).where(Request.id == request_id)
    ).scalar_one_or_none()
    if r is None:
        raise LookupError(f"request {request_id} not found")
    return r


def update_header(s: Session, request_id: int, **fields: str) -> None:
    r = get_request(s, request_id)
    for key, val in fields.items():
        if key in HEADER_FIELDS:
            setattr(r, key, (val or "").strip() or None)
        elif key == "datetime_of_request":
            r.datetime_of_request = _parse_dt(val)


def _parse_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    val = val.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    return None


# ---- search (mirrors catalog search endpoints) -------------------------

def search_mrc(s: Session, q: str, limit: int = 40) -> list[MRC]:
    tokens = [t for t in (q or "").split() if t]
    stmt = select(MRC).options(selectinload(MRC.mip)).join(MIP, MRC.mip_id == MIP.id)
    for tok in tokens:
        like = f"%{tok}%"
        stmt = stmt.where(
            MIP.code.ilike(like)
            | MIP.title.ilike(like)
            | MRC.code.ilike(like)
            | MRC.description.ilike(like)
        )
    stmt = stmt.order_by(MIP.code, MRC.code).limit(limit)
    return list(s.execute(stmt).scalars().all())


def search_items(s: Session, q: str, limit: int = 40) -> list[HazmatItem]:
    tokens = [t for t in (q or "").split() if t]
    stmt = (
        select(HazmatItem)
        .options(selectinload(HazmatItem.spmig))
        .join(SPMIG, HazmatItem.spmig_id == SPMIG.id)
    )
    for tok in tokens:
        like = f"%{tok}%"
        stmt = stmt.where(
            SPMIG.code.ilike(like)
            | HazmatItem.nomenclature.ilike(like)
            | HazmatItem.niin.ilike(like)
        )
    stmt = stmt.order_by(SPMIG.code, HazmatItem.nomenclature).limit(limit)
    return list(s.execute(stmt).scalars().all())


# ---- lines -------------------------------------------------------------

def _next_sort(s: Session, request_id: int) -> int:
    last = s.scalar(
        select(RequestLine.sort_order)
        .where(RequestLine.request_id == request_id)
        .order_by(RequestLine.sort_order.desc())
    )
    return (last or 0) + 10


def load_mrc(s: Session, request_id: int, mrc_id: int) -> tuple[int, list[str]]:
    """Bulk-load every item on an MRC as snapshot lines (qty=1), deduped
    against items already on the request. Returns (added, skipped_names)."""
    r = get_request(s, request_id)
    mrc = s.execute(
        select(MRC)
        .options(
            selectinload(MRC.mip),
            selectinload(MRC.items)
            .selectinload(MRCItem.hazmat_item)
            .selectinload(HazmatItem.spmig),
        )
        .where(MRC.id == mrc_id)
    ).scalar_one_or_none()
    if mrc is None:
        raise LookupError("MRC not found")

    already = {ln.hazmat_item_id for ln in r.lines if ln.hazmat_item_id is not None}
    base = _next_sort(s, request_id)
    added = 0
    skipped: list[str] = []
    for link in mrc.items:
        item = link.hazmat_item
        if not item:
            continue
        if item.id in already:
            skipped.append(item.nomenclature)
            continue
        s.add(
            RequestLine(
                request_id=request_id,
                sort_order=base + added * 10,
                hazmat_item_id=item.id,
                spmig_code=item.spmig.code if item.spmig else None,
                nomenclature=item.nomenclature,
                niin=item.niin,
                qty=1,
            )
        )
        already.add(item.id)
        added += 1
    if r.source_mip_id is None:
        r.source_mip_id = mrc.mip_id
        r.source_mrc_id = mrc.id
    s.flush()
    return added, skipped


def add_item(s: Session, request_id: int, hazmat_item_id: int) -> bool:
    """Add a single catalog item as a snapshot line. Returns False if the
    item is already on the request (deduped)."""
    r = get_request(s, request_id)
    item = s.execute(
        select(HazmatItem)
        .options(selectinload(HazmatItem.spmig))
        .where(HazmatItem.id == hazmat_item_id)
    ).scalar_one_or_none()
    if item is None:
        raise LookupError("item not found")
    if any(ln.hazmat_item_id == item.id for ln in r.lines):
        return False
    s.add(
        RequestLine(
            request_id=request_id,
            sort_order=_next_sort(s, request_id),
            hazmat_item_id=item.id,
            spmig_code=item.spmig.code if item.spmig else None,
            nomenclature=item.nomenclature,
            niin=item.niin,
            qty=1,
        )
    )
    s.flush()
    return True


def add_manual_line(
    s: Session, request_id: int, spmig: str, nomenclature: str, niin: str, qty: int
) -> None:
    get_request(s, request_id)  # 404 guard
    s.add(
        RequestLine(
            request_id=request_id,
            sort_order=_next_sort(s, request_id),
            spmig_code=(spmig or "").strip() or None,
            nomenclature=(nomenclature or "").strip() or None,
            niin=(niin or "").strip() or None,
            qty=qty,
        )
    )
    s.flush()


def set_qty(s: Session, request_id: int, line_id: int, qty: int | None) -> None:
    line = s.get(RequestLine, line_id)
    if not line or line.request_id != request_id:
        raise LookupError("line not found")
    line.qty = qty
    s.flush()


def delete_line(s: Session, request_id: int, line_id: int) -> None:
    line = s.get(RequestLine, line_id)
    if not line or line.request_id != request_id:
        raise LookupError("line not found")
    s.delete(line)
    s.flush()


def move_line(s: Session, request_id: int, line_id: int, direction: str) -> None:
    lines = list(
        s.execute(
            select(RequestLine)
            .where(RequestLine.request_id == request_id)
            .order_by(RequestLine.sort_order)
        ).scalars().all()
    )
    idx = next((i for i, ln in enumerate(lines) if ln.id == line_id), None)
    if idx is None:
        raise LookupError("line not found")
    if direction == "up" and idx > 0:
        j = idx - 1
    elif direction == "down" and idx < len(lines) - 1:
        j = idx + 1
    else:
        return
    lines[idx].sort_order, lines[j].sort_order = lines[j].sort_order, lines[idx].sort_order
    s.flush()


def alternates(s: Session, line_id: int) -> list[HazmatItem]:
    line = s.get(RequestLine, line_id)
    if not line or not line.hazmat_item_id:
        return []
    item = s.get(HazmatItem, line.hazmat_item_id)
    if not item:
        return []
    return list(
        s.execute(
            select(HazmatItem)
            .where(and_(HazmatItem.spmig_id == item.spmig_id, HazmatItem.id != item.id))
            .order_by(HazmatItem.nomenclature)
        ).scalars().all()
    )


def swap_alternate(s: Session, request_id: int, line_id: int, to_item_id: int) -> None:
    line = s.get(RequestLine, line_id)
    if not line or line.request_id != request_id:
        raise LookupError("line not found")
    new_item = s.get(HazmatItem, to_item_id)
    if not new_item:
        raise LookupError("replacement item not found")
    sp = s.get(SPMIG, new_item.spmig_id)
    line.hazmat_item_id = new_item.id
    line.spmig_code = sp.code if sp else None
    line.nomenclature = new_item.nomenclature
    line.niin = new_item.niin
    s.flush()


# ---- finalize ----------------------------------------------------------

class FinalizeError(RuntimeError):
    pass


def finalize(s: Session, request_id: int) -> Path | None:
    """Validate qty on every line, then render the PDF. Returns the path
    (or None if PDF persistence is disabled). Raises FinalizeError."""
    r = get_request(s, request_id)
    if not r.lines:
        raise FinalizeError("Cannot finalize an empty request.")
    missing = [ln for ln in r.lines if ln.qty is None or ln.qty <= 0]
    if missing:
        raise FinalizeError(f"{len(missing)} line(s) need a quantity > 0 before finalizing.")
    try:
        path = render_request_pdf(r)
    except Exception as e:  # PdfRenderError or anything the backend throws
        raise FinalizeError(f"PDF generation failed: {e}") from e
    if r.finalized_at is None:
        r.finalized_at = datetime.utcnow()
    r.pdf_path = str(path) if path else None
    return path


# ---- history (mirrors requests.list_requests + the request actions) ----

@dataclass(frozen=True)
class RequestRow:
    """Flat display row for the history list — safe to use after the
    session closes (no lazy ORM attributes)."""

    id: int
    when: str
    requestor: str
    workcenter: str
    lpo: str
    location: str
    finalized: bool
    line_count: int
    has_pdf: bool

    @property
    def status(self) -> str:
        return "Final" if self.finalized else "Draft"


def list_requests(
    s: Session,
    q: str = "",
    status_filter: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 200,
) -> list[RequestRow]:
    stmt = (
        select(Request)
        .options(selectinload(Request.lines))
        .order_by(Request.created_at.desc())
    )
    if q and q.strip():
        like = f"%{q.strip()}%"
        line_match = select(RequestLine.request_id).where(
            RequestLine.nomenclature.ilike(like)
            | RequestLine.spmig_code.ilike(like)
            | RequestLine.niin.ilike(like)
        )
        stmt = stmt.where(
            Request.requestor_name.ilike(like)
            | Request.workcenter.ilike(like)
            | Request.lpo.ilike(like)
            | Request.hazmat_location.ilike(like)
            | Request.id.in_(line_match)
        )
    if status_filter == "draft":
        stmt = stmt.where(Request.finalized_at.is_(None))
    elif status_filter == "final":
        stmt = stmt.where(Request.finalized_at.is_not(None))
    if date_from:
        with contextlib.suppress(ValueError):
            stmt = stmt.where(Request.created_at >= datetime.strptime(date_from, "%Y-%m-%d"))
    if date_to:
        with contextlib.suppress(ValueError):
            dt = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            stmt = stmt.where(Request.created_at < dt)

    rows = s.execute(stmt.limit(limit)).scalars().all()
    out: list[RequestRow] = []
    for r in rows:
        when = r.datetime_of_request or r.created_at
        out.append(
            RequestRow(
                id=r.id,
                when=when.strftime("%Y-%m-%d %H:%M") if when else "",
                requestor=(r.requestor_name or "").strip(),
                workcenter=(r.workcenter or "").strip(),
                lpo=(r.lpo or "").strip(),
                location=(r.hazmat_location or "").strip(),
                finalized=r.finalized_at is not None,
                line_count=len(r.lines),
                has_pdf=bool(r.pdf_path) and Path(r.pdf_path).exists(),
            )
        )
    return out


def is_finalized(s: Session, request_id: int) -> bool:
    return get_request(s, request_id).finalized_at is not None


def duplicate_request(s: Session, request_id: int) -> int:
    src = get_request(s, request_id)
    dup = Request(
        datetime_of_request=datetime.now().replace(microsecond=0, second=0),
        lpo=src.lpo,
        workcenter=src.workcenter,
        requestor_name=src.requestor_name,
        hazmat_location=src.hazmat_location,
        source_mip_id=src.source_mip_id,
        source_mrc_id=src.source_mrc_id,
    )
    s.add(dup)
    s.flush()
    for line in src.lines:
        s.add(
            RequestLine(
                request_id=dup.id,
                sort_order=line.sort_order,
                hazmat_item_id=line.hazmat_item_id,
                spmig_code=line.spmig_code,
                nomenclature=line.nomenclature,
                niin=line.niin,
                qty=line.qty,
            )
        )
    s.flush()
    return dup.id


def delete_request(s: Session, request_id: int) -> None:
    r = s.get(Request, request_id)
    if r:
        s.delete(r)
        s.flush()


def reopen_request(s: Session, request_id: int) -> None:
    """Clear finalized_at so the request is editable again. The prior PDF
    path is left in place until the next finalize overwrites it."""
    r = get_request(s, request_id)
    r.finalized_at = None
    s.flush()


def pdf_path(s: Session, request_id: int) -> str | None:
    r = get_request(s, request_id)
    return r.pdf_path if r.pdf_path and Path(r.pdf_path).exists() else None


def print_request(s: Session, request_id: int, copies: int = 1) -> str:
    """Send the request's PDF to CUPS, finalizing first if needed.
    Returns the lp job id. Raises FinalizeError / printer_service.PrintError."""
    r = get_request(s, request_id)
    if not (r.pdf_path and Path(r.pdf_path).exists()):
        finalize(s, request_id)  # auto-finalize so one click is enough
        # Persist the finalize+PDF *before* attempting to print, so a print
        # failure (e.g. no CUPS) doesn't roll back a perfectly good finalize.
        s.commit()
        r = get_request(s, request_id)
    if not (r.pdf_path and Path(r.pdf_path).exists()):
        raise FinalizeError("No PDF available to print.")
    return printer_service.print_pdf(Path(r.pdf_path), copies=max(1, copies))
