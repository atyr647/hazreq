from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_session
from app.models import (
    MIP,
    MRC,
    SPMIG,
    HazmatItem,
    MRCItem,
    Request as ReqModel,
    RequestLine,
)
from pathlib import Path

from app.services import printer as printer_service
from app.services.pdf import PdfRenderError, render_request_pdf
from app.templating import render, render_partial

import logging

router = APIRouter()
log = logging.getLogger(__name__)


def _get_request_or_404(db: Session, request_id: int) -> ReqModel:
    r = db.execute(
        select(ReqModel)
        .options(selectinload(ReqModel.lines))
        .where(ReqModel.id == request_id)
    ).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Request not found")
    return r


def _ensure_editable(r: ReqModel) -> None:
    if r.is_finalized:
        raise HTTPException(409, "Request is finalized and read-only")


# ============================================================
# Listing & history
# ============================================================

@router.get("/", name="list_requests")
def list_requests(
    request: Request,
    db: Session = Depends(get_session),
    q: str = Query(""),
    status_filter: str = Query("", alias="status"),
    date_from: str = Query("", alias="from"),
    date_to: str = Query("", alias="to"),
):
    stmt = select(ReqModel).options(selectinload(ReqModel.lines)).order_by(
        ReqModel.created_at.desc()
    )
    if q.strip():
        like = f"%{q.strip()}%"
        # Match on header fields OR the snapshot fields of any line.
        # (Snapshot fields make the search future-proof — they don't
        #  change when the catalog is edited later.)
        line_match = (
            select(RequestLine.request_id)
            .where(
                (RequestLine.nomenclature.ilike(like))
                | (RequestLine.spmig_code.ilike(like))
                | (RequestLine.niin.ilike(like))
            )
        )
        stmt = stmt.where(
            (ReqModel.requestor_name.ilike(like))
            | (ReqModel.workcenter.ilike(like))
            | (ReqModel.lpo.ilike(like))
            | (ReqModel.hazmat_location.ilike(like))
            | (ReqModel.id.in_(line_match))
        )
    if status_filter == "draft":
        stmt = stmt.where(ReqModel.finalized_at.is_(None))
    elif status_filter == "final":
        stmt = stmt.where(ReqModel.finalized_at.is_not(None))
    if date_from:
        try:
            df = datetime.strptime(date_from, "%Y-%m-%d")
            stmt = stmt.where(ReqModel.created_at >= df)
        except ValueError:
            pass
    if date_to:
        try:
            dt = datetime.strptime(date_to, "%Y-%m-%d")
            stmt = stmt.where(ReqModel.created_at < dt.replace(hour=23, minute=59, second=59))
        except ValueError:
            pass
    rows = db.execute(stmt.limit(200)).scalars().all()
    return render(
        request,
        "requests/list.html",
        {
            "rows": rows,
            "q": q,
            "status_filter": status_filter,
            "date_from": date_from,
            "date_to": date_to,
        },
        nav="history",
    )


# ============================================================
# Create & edit
# ============================================================

@router.get("/new", name="new_request")
def new_request(request: Request, db: Session = Depends(get_session)):
    """Creates a new draft request and redirects to its editor."""
    r = ReqModel(datetime_of_request=datetime.now().replace(microsecond=0, second=0))
    db.add(r)
    db.commit()
    return RedirectResponse(
        request.url_for("view_request", request_id=r.id), status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/{request_id}", name="view_request")
def view_request(request_id: int, request: Request, db: Session = Depends(get_session)):
    r = _get_request_or_404(db, request_id)
    return render(
        request,
        "requests/edit.html" if not r.is_finalized else "requests/view.html",
        {"r": r},
        nav="new" if not r.is_finalized else "history",
    )


@router.patch("/{request_id}", name="patch_request")
async def patch_request(request_id: int, request: Request, db: Session = Depends(get_session)):
    """Auto-save endpoint for header fields. Accepts a single field=value."""
    r = _get_request_or_404(db, request_id)
    _ensure_editable(r)
    form = await request.form()
    allowed = {
        "lpo",
        "workcenter",
        "requestor_name",
        "hazmat_location",
        "datetime_of_request",
    }
    changed = False
    for key, val in form.items():
        if key not in allowed:
            continue
        if key == "datetime_of_request":
            try:
                setattr(r, key, datetime.fromisoformat(val) if val else None)
            except ValueError:
                raise HTTPException(400, "Invalid datetime") from None
        else:
            setattr(r, key, (val or "").strip() or None)
        changed = True
    if changed:
        db.commit()
    return Response(status_code=204)


@router.post("/{request_id}/duplicate", name="duplicate_request")
def duplicate_request(request_id: int, request: Request, db: Session = Depends(get_session)):
    src = _get_request_or_404(db, request_id)
    dup = ReqModel(
        datetime_of_request=datetime.now().replace(microsecond=0, second=0),
        lpo=src.lpo,
        workcenter=src.workcenter,
        requestor_name=src.requestor_name,
        hazmat_location=src.hazmat_location,
        source_mip_id=src.source_mip_id,
        source_mrc_id=src.source_mrc_id,
    )
    db.add(dup)
    db.flush()
    for line in src.lines:
        db.add(
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
    db.commit()
    return RedirectResponse(
        request.url_for("view_request", request_id=dup.id), status_code=303
    )


@router.post("/{request_id}/delete", name="delete_request")
def delete_request(request_id: int, request: Request, db: Session = Depends(get_session)):
    r = _get_request_or_404(db, request_id)
    db.delete(r)
    db.commit()
    return RedirectResponse(request.url_for("list_requests"), status_code=303)


# ============================================================
# Lines
# ============================================================

def _next_sort(db: Session, request_id: int) -> int:
    last = db.scalar(
        select(RequestLine.sort_order)
        .where(RequestLine.request_id == request_id)
        .order_by(RequestLine.sort_order.desc())
    )
    return (last or 0) + 10


@router.post("/{request_id}/lines", name="add_line")
def add_line(
    request_id: int,
    request: Request,
    spmig_code: str = Form(""),
    nomenclature: str = Form(""),
    niin: str = Form(""),
    qty: int = Form(1),
    db: Session = Depends(get_session),
):
    r = _get_request_or_404(db, request_id)
    _ensure_editable(r)
    line = RequestLine(
        request_id=r.id,
        sort_order=_next_sort(db, r.id),
        spmig_code=spmig_code.strip() or None,
        nomenclature=nomenclature.strip() or None,
        niin=niin.strip() or None,
        qty=qty,
    )
    db.add(line)
    db.commit()
    db.refresh(line)
    return render_partial("requests/_line.html", {"line": line, "r": r})


@router.post("/{request_id}/load-mrc", name="load_mrc")
def load_mrc(
    request_id: int,
    request: Request,
    mrc_id: int = Form(...),
    db: Session = Depends(get_session),
):
    r = _get_request_or_404(db, request_id)
    _ensure_editable(r)
    mrc = db.execute(
        select(MRC)
        .options(
            selectinload(MRC.mip),
            selectinload(MRC.items).selectinload(MRCItem.hazmat_item).selectinload(HazmatItem.spmig),
        )
        .where(MRC.id == mrc_id)
    ).scalar_one_or_none()
    if not mrc:
        raise HTTPException(404, "MRC not found")

    # Dedup: never add a hazmat_item that's already on this request, even
    # if it came from a different MRC. Manual lines (no hazmat_item_id)
    # are out of scope for dedup since they're free-text.
    already = {ln.hazmat_item_id for ln in r.lines if ln.hazmat_item_id is not None}

    base_sort = _next_sort(db, r.id)
    new_lines: list[RequestLine] = []
    skipped: list[str] = []
    offset = 0
    for link in mrc.items:
        item = link.hazmat_item
        if not item:
            continue
        if item.id in already:
            skipped.append(item.nomenclature)
            continue
        line = RequestLine(
            request_id=r.id,
            sort_order=base_sort + offset * 10,
            hazmat_item_id=item.id,
            spmig_code=item.spmig.code if item.spmig else None,
            nomenclature=item.nomenclature,
            niin=item.niin,
            qty=1,  # universal default; operator overrides per request
        )
        db.add(line)
        new_lines.append(line)
        already.add(item.id)
        offset += 1
    if r.source_mip_id is None:
        r.source_mip_id = mrc.mip_id
        r.source_mrc_id = mrc.id
    db.commit()
    for line in new_lines:
        db.refresh(line)
    return render_partial(
        "requests/_lines_block.html",
        {"lines": new_lines, "r": r, "skipped": skipped},
    )


@router.patch("/{request_id}/lines/{line_id}", name="patch_line")
async def patch_line(
    request_id: int,
    line_id: int,
    request: Request,
    db: Session = Depends(get_session),
):
    r = _get_request_or_404(db, request_id)
    _ensure_editable(r)
    line = db.get(RequestLine, line_id)
    if not line or line.request_id != request_id:
        raise HTTPException(404)
    form = await request.form()
    allowed = {"spmig_code", "nomenclature", "niin", "qty"}
    for key, val in form.items():
        if key not in allowed:
            continue
        if key == "qty":
            line.qty = int(val) if val else None
        else:
            setattr(line, key, (val or "").strip() or None)
    db.commit()
    return Response(status_code=204)


@router.post("/{request_id}/lines/{line_id}/delete", name="delete_line")
def delete_line(request_id: int, line_id: int, db: Session = Depends(get_session)):
    r = _get_request_or_404(db, request_id)
    _ensure_editable(r)
    line = db.get(RequestLine, line_id)
    if not line or line.request_id != request_id:
        raise HTTPException(404)
    db.delete(line)
    db.commit()
    return Response(status_code=204)


@router.post("/{request_id}/lines/{line_id}/move", name="move_line")
def move_line(
    request_id: int,
    line_id: int,
    direction: str = Form(...),
    db: Session = Depends(get_session),
):
    r = _get_request_or_404(db, request_id)
    _ensure_editable(r)
    lines = db.execute(
        select(RequestLine).where(RequestLine.request_id == request_id).order_by(RequestLine.sort_order)
    ).scalars().all()
    idx = next((i for i, ln in enumerate(lines) if ln.id == line_id), None)
    if idx is None:
        raise HTTPException(404)
    if direction == "up" and idx > 0:
        lines[idx].sort_order, lines[idx - 1].sort_order = (
            lines[idx - 1].sort_order,
            lines[idx].sort_order,
        )
    elif direction == "down" and idx < len(lines) - 1:
        lines[idx].sort_order, lines[idx + 1].sort_order = (
            lines[idx + 1].sort_order,
            lines[idx].sort_order,
        )
    db.commit()
    return RedirectResponse(f"/requests/{request_id}", status_code=303)


# ============================================================
# Swap-alternate
# ============================================================

@router.get("/{request_id}/lines/{line_id}/alternates", name="line_alternates")
def line_alternates(
    request_id: int, line_id: int, db: Session = Depends(get_session)
):
    line = db.get(RequestLine, line_id)
    if not line or line.request_id != request_id:
        raise HTTPException(404)
    alternates: list[HazmatItem] = []
    if line.hazmat_item_id:
        item = db.get(HazmatItem, line.hazmat_item_id)
        if item:
            alternates = db.execute(
                select(HazmatItem)
                .where(and_(HazmatItem.spmig_id == item.spmig_id, HazmatItem.id != item.id))
                .order_by(HazmatItem.nomenclature)
            ).scalars().all()
    return render_partial(
        "requests/_alternates.html", {"line": line, "alternates": alternates}
    )


@router.post("/{request_id}/lines/{line_id}/swap", name="swap_alternate")
def swap_alternate(
    request_id: int,
    line_id: int,
    to_item_id: int = Form(...),
    db: Session = Depends(get_session),
):
    r = _get_request_or_404(db, request_id)
    _ensure_editable(r)
    line = db.get(RequestLine, line_id)
    if not line or line.request_id != request_id:
        raise HTTPException(404)
    new_item = db.get(HazmatItem, to_item_id)
    if not new_item:
        raise HTTPException(404, "Replacement item not found")
    sp = db.get(SPMIG, new_item.spmig_id)
    line.hazmat_item_id = new_item.id
    line.spmig_code = sp.code if sp else None
    line.nomenclature = new_item.nomenclature
    line.niin = new_item.niin
    db.commit()
    db.refresh(line)
    return render_partial("requests/_line.html", {"line": line, "r": r})


# ============================================================
# Finalize / PDF
# ============================================================

@router.post("/{request_id}/finalize", name="finalize_request")
def finalize_request(request_id: int, request: Request, db: Session = Depends(get_session)):
    r = _get_request_or_404(db, request_id)
    if not r.lines:
        raise HTTPException(400, "Cannot finalize an empty request")
    # Every line must have a manually selected qty > 0. Defaults from the
    # catalog get the user started, but qty is intentionally a per-request
    # decision and finalize is the gate that enforces it.
    missing = [ln for ln in r.lines if ln.qty is None or ln.qty <= 0]
    if missing:
        raise HTTPException(
            400,
            f"{len(missing)} line(s) need a quantity > 0 before finalizing.",
        )
    try:
        path = render_request_pdf(r)
    except PdfRenderError as e:
        raise HTTPException(500, f"PDF generation failed: {e}") from None
    if r.finalized_at is None:
        r.finalized_at = datetime.utcnow()
    r.pdf_path = str(path) if path else None
    db.commit()
    return RedirectResponse(
        request.url_for("view_request", request_id=r.id), status_code=303
    )


@router.post("/{request_id}/reopen", name="reopen_request")
def reopen_request(request_id: int, request: Request, db: Session = Depends(get_session)):
    """Re-open a finalized request for further editing.

    Clears finalized_at; pdf_path is left in place so the previously
    generated PDF stays accessible until the next finalize overwrites it.
    Intentionally available to anyone on the LAN — mirrors the rest of
    the trust model. Confirmed via UI prompt before posting.
    """
    r = _get_request_or_404(db, request_id)
    if not r.is_finalized:
        # idempotent — re-opening an in-progress request is a no-op
        return RedirectResponse(
            request.url_for("view_request", request_id=r.id), status_code=303
        )
    r.finalized_at = None
    db.commit()
    log.info("Reopened request %s for editing", r.id)
    return RedirectResponse(
        request.url_for("view_request", request_id=r.id), status_code=303
    )


@router.get("/{request_id}/pdf", name="request_pdf")
def request_pdf(request_id: int, db: Session = Depends(get_session)):
    r = _get_request_or_404(db, request_id)
    if not r.pdf_path:
        raise HTTPException(404, "No PDF generated yet — finalize the request first")
    return FileResponse(r.pdf_path, media_type="application/pdf", filename=f"hazreq-{r.id}.pdf")


@router.post("/{request_id}/print", name="request_print")
def request_print(
    request_id: int,
    request: Request,
    printer_name: str = Form(""),
    copies: int = Form(1),
    db: Session = Depends(get_session),
):
    """Send the generated PDF to CUPS via `lp`. Auto-finalizes if needed."""
    r = _get_request_or_404(db, request_id)
    if not r.pdf_path:
        # auto-finalize so a one-click "Print" is enough
        if not r.lines:
            raise HTTPException(400, "Cannot print an empty request")
        missing = [ln for ln in r.lines if ln.qty is None or ln.qty <= 0]
        if missing:
            raise HTTPException(
                400, f"{len(missing)} line(s) need a quantity > 0 before printing."
            )
        try:
            path = render_request_pdf(r)
        except PdfRenderError as e:
            raise HTTPException(500, f"PDF generation failed: {e}") from None
        if path is None:
            raise HTTPException(500, "PDF persistence is disabled — cannot print")
        if r.finalized_at is None:
            r.finalized_at = datetime.utcnow()
        r.pdf_path = str(path)
        db.commit()
    try:
        job = printer_service.print_pdf(
            Path(r.pdf_path), printer=(printer_name or None), copies=max(1, int(copies))
        )
    except printer_service.PrintError as e:
        raise HTTPException(500, f"Print failed: {e}") from None
    log.info("Submitted print job for request %s: %s", r.id, job)
    return RedirectResponse(
        request.url_for("view_request", request_id=r.id) + "?printed=1", status_code=303
    )


@router.get("/{request_id}/print-preview", name="print_preview")
def print_preview(request_id: int, request: Request, db: Session = Depends(get_session)):
    r = _get_request_or_404(db, request_id)
    return render(request, "requests/print_preview.html", {"r": r}, nav="history")
