from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.db import get_session
from app.models import MIP, MRC, SPMIG, HazmatItem, MRCItem
from app.templating import render, render_partial

router = APIRouter()


# ============================================================
# MIPs
# ============================================================

@router.get("/mips", name="list_mips")
def list_mips(request: Request, db: Session = Depends(get_session)):
    mips = db.execute(
        select(MIP).options(selectinload(MIP.mrcs)).order_by(MIP.code)
    ).scalars().all()
    return render(request, "catalog/mips.html", {"mips": mips}, nav="mips")


@router.post("/mips", name="create_mip")
def create_mip(
    request: Request,
    code: str = Form(...),
    title: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_session),
):
    code = code.strip()
    if not code:
        raise HTTPException(400, "Code is required")
    mip = MIP(code=code, title=title.strip() or None, notes=notes.strip() or None)
    db.add(mip)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, f"MIP code '{code}' already exists") from None
    return RedirectResponse(
        request.url_for("view_mip", mip_id=mip.id), status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/mips/{mip_id}", name="view_mip")
def view_mip(mip_id: int, request: Request, db: Session = Depends(get_session)):
    mip = db.get(MIP, mip_id)
    if not mip:
        raise HTTPException(404)
    return render(request, "catalog/mip_detail.html", {"mip": mip}, nav="mips")


@router.post("/mips/{mip_id}/edit", name="edit_mip")
def edit_mip(
    mip_id: int,
    request: Request,
    code: str = Form(...),
    title: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_session),
):
    mip = db.get(MIP, mip_id)
    if not mip:
        raise HTTPException(404)
    mip.code = code.strip()
    mip.title = title.strip() or None
    mip.notes = notes.strip() or None
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, f"MIP code '{code}' already exists") from None
    return RedirectResponse(
        request.url_for("view_mip", mip_id=mip.id), status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/mips/{mip_id}/delete", name="delete_mip")
def delete_mip(mip_id: int, request: Request, db: Session = Depends(get_session)):
    mip = db.get(MIP, mip_id)
    if not mip:
        raise HTTPException(404)
    db.delete(mip)
    db.commit()
    return RedirectResponse(request.url_for("list_mips"), status_code=status.HTTP_303_SEE_OTHER)


# ============================================================
# MRCs (always nested under a MIP)
# ============================================================

@router.post("/mips/{mip_id}/mrcs", name="create_mrc")
def create_mrc(
    mip_id: int,
    request: Request,
    code: str = Form(...),
    periodicity: str = Form(""),
    description: str = Form(""),
    db: Session = Depends(get_session),
):
    mip = db.get(MIP, mip_id)
    if not mip:
        raise HTTPException(404)
    code = code.strip()
    if not code:
        raise HTTPException(400, "MRC code is required")
    mrc = MRC(
        mip_id=mip.id,
        code=code,
        periodicity=periodicity.strip() or None,
        description=description.strip() or None,
    )
    db.add(mrc)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, f"MRC '{code}' already exists under {mip.code}") from None
    return RedirectResponse(
        request.url_for("view_mrc", mrc_id=mrc.id), status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/mrcs/{mrc_id}", name="view_mrc")
def view_mrc(mrc_id: int, request: Request, db: Session = Depends(get_session)):
    mrc = db.execute(
        select(MRC)
        .options(
            selectinload(MRC.mip),
            selectinload(MRC.items).selectinload(MRCItem.hazmat_item).selectinload(HazmatItem.spmig),
        )
        .where(MRC.id == mrc_id)
    ).scalar_one_or_none()
    if not mrc:
        raise HTTPException(404)
    spmigs = db.execute(
        select(SPMIG).options(selectinload(SPMIG.items)).order_by(SPMIG.code)
    ).scalars().all()
    return render(request, "catalog/mrc_detail.html", {"mrc": mrc, "spmigs": spmigs}, nav="mips")


@router.post("/mrcs/{mrc_id}/edit", name="edit_mrc")
def edit_mrc(
    mrc_id: int,
    request: Request,
    code: str = Form(...),
    periodicity: str = Form(""),
    description: str = Form(""),
    db: Session = Depends(get_session),
):
    mrc = db.get(MRC, mrc_id)
    if not mrc:
        raise HTTPException(404)
    mrc.code = code.strip()
    mrc.periodicity = periodicity.strip() or None
    mrc.description = description.strip() or None
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "MRC code already exists under this MIP") from None
    return RedirectResponse(
        request.url_for("view_mrc", mrc_id=mrc.id), status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/mrcs/{mrc_id}/delete", name="delete_mrc")
def delete_mrc(mrc_id: int, request: Request, db: Session = Depends(get_session)):
    mrc = db.get(MRC, mrc_id)
    if not mrc:
        raise HTTPException(404)
    mip_id = mrc.mip_id
    db.delete(mrc)
    db.commit()
    return RedirectResponse(request.url_for("view_mip", mip_id=mip_id), status_code=303)


# ============================================================
# MRC items (link table)
# ============================================================

@router.post("/mrcs/{mrc_id}/items", name="add_mrc_item")
def add_mrc_item(
    mrc_id: int,
    request: Request,
    hazmat_item_id: int = Form(...),
    db: Session = Depends(get_session),
):
    mrc = db.get(MRC, mrc_id)
    if not mrc:
        raise HTTPException(404)
    item = db.get(HazmatItem, hazmat_item_id)
    if not item:
        raise HTTPException(404, "Hazmat item not found")
    existing = db.get(MRCItem, {"mrc_id": mrc_id, "hazmat_item_id": hazmat_item_id})
    if existing:
        raise HTTPException(400, "Item already attached to this MRC")
    next_order = (
        db.scalar(
            select(MRCItem.sort_order).where(MRCItem.mrc_id == mrc_id).order_by(MRCItem.sort_order.desc())
        )
        or 0
    )
    link = MRCItem(
        mrc_id=mrc_id,
        hazmat_item_id=hazmat_item_id,
        sort_order=next_order + 10,
    )
    db.add(link)
    db.commit()
    return RedirectResponse(request.url_for("view_mrc", mrc_id=mrc_id), status_code=303)


@router.post("/mrcs/{mrc_id}/items/{hazmat_item_id}/move", name="move_mrc_item")
def move_mrc_item(
    mrc_id: int,
    hazmat_item_id: int,
    direction: str = Form(...),
    db: Session = Depends(get_session),
):
    items = db.execute(
        select(MRCItem).where(MRCItem.mrc_id == mrc_id).order_by(MRCItem.sort_order)
    ).scalars().all()
    idx = next((i for i, it in enumerate(items) if it.hazmat_item_id == hazmat_item_id), None)
    if idx is None:
        raise HTTPException(404)
    if direction == "up" and idx > 0:
        items[idx].sort_order, items[idx - 1].sort_order = (
            items[idx - 1].sort_order,
            items[idx].sort_order,
        )
    elif direction == "down" and idx < len(items) - 1:
        items[idx].sort_order, items[idx + 1].sort_order = (
            items[idx + 1].sort_order,
            items[idx].sort_order,
        )
    db.commit()
    return RedirectResponse(f"/catalog/mrcs/{mrc_id}", status_code=303)


@router.post("/mrcs/{mrc_id}/items/{hazmat_item_id}/delete", name="remove_mrc_item")
def remove_mrc_item(
    mrc_id: int,
    hazmat_item_id: int,
    db: Session = Depends(get_session),
):
    link = db.get(MRCItem, {"mrc_id": mrc_id, "hazmat_item_id": hazmat_item_id})
    if not link:
        raise HTTPException(404)
    db.delete(link)
    db.commit()
    return RedirectResponse(f"/catalog/mrcs/{mrc_id}", status_code=303)


# ============================================================
# SPMIGs
# ============================================================

@router.get("/spmigs", name="list_spmigs")
def list_spmigs(request: Request, db: Session = Depends(get_session)):
    spmigs = db.execute(
        select(SPMIG).options(selectinload(SPMIG.items)).order_by(SPMIG.code)
    ).scalars().all()
    return render(request, "catalog/spmigs.html", {"spmigs": spmigs}, nav="spmigs")


@router.post("/spmigs", name="create_spmig")
def create_spmig(
    request: Request,
    code: str = Form(...),
    description: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_session),
):
    code = code.strip()
    if not code:
        raise HTTPException(400, "Code is required")
    sp = SPMIG(code=code, description=description.strip() or None, notes=notes.strip() or None)
    db.add(sp)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, f"SPMIG '{code}' already exists") from None
    return RedirectResponse(request.url_for("view_spmig", spmig_id=sp.id), status_code=303)


@router.get("/spmigs/{spmig_id}", name="view_spmig")
def view_spmig(spmig_id: int, request: Request, db: Session = Depends(get_session)):
    sp = db.get(SPMIG, spmig_id)
    if not sp:
        raise HTTPException(404)
    return render(request, "catalog/spmig_detail.html", {"spmig": sp}, nav="spmigs")


@router.post("/spmigs/{spmig_id}/edit", name="edit_spmig")
def edit_spmig(
    spmig_id: int,
    request: Request,
    code: str = Form(...),
    description: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_session),
):
    sp = db.get(SPMIG, spmig_id)
    if not sp:
        raise HTTPException(404)
    sp.code = code.strip()
    sp.description = description.strip() or None
    sp.notes = notes.strip() or None
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, f"SPMIG '{code}' already exists") from None
    return RedirectResponse(request.url_for("view_spmig", spmig_id=sp.id), status_code=303)


@router.post("/spmigs/{spmig_id}/delete", name="delete_spmig")
def delete_spmig(spmig_id: int, request: Request, db: Session = Depends(get_session)):
    sp = db.get(SPMIG, spmig_id)
    if not sp:
        raise HTTPException(404)
    if sp.items:
        raise HTTPException(
            400,
            "Cannot delete a SPMIG that still has hazmat items. Delete or reassign them first.",
        )
    db.delete(sp)
    db.commit()
    return RedirectResponse(request.url_for("list_spmigs"), status_code=303)


# ============================================================
# Hazmat items (always nested under a SPMIG)
# ============================================================

@router.post("/spmigs/{spmig_id}/items", name="create_hazmat_item")
def create_hazmat_item(
    spmig_id: int,
    request: Request,
    nomenclature: str = Form(...),
    niin: str = Form(""),
    unit_of_issue: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_session),
):
    sp = db.get(SPMIG, spmig_id)
    if not sp:
        raise HTTPException(404)
    nomenclature = nomenclature.strip()
    if not nomenclature:
        raise HTTPException(400, "Nomenclature is required")
    item = HazmatItem(
        spmig_id=spmig_id,
        nomenclature=nomenclature,
        niin=niin.strip() or None,
        unit_of_issue=unit_of_issue.strip() or None,
        notes=notes.strip() or None,
    )
    db.add(item)
    db.commit()
    return RedirectResponse(request.url_for("view_spmig", spmig_id=spmig_id), status_code=303)


@router.get("/items/{item_id}", name="view_item")
def view_item(item_id: int, request: Request, db: Session = Depends(get_session)):
    item = db.get(HazmatItem, item_id)
    if not item:
        raise HTTPException(404)
    used_in = db.execute(
        select(MRC)
        .options(selectinload(MRC.mip))
        .join(MRCItem, MRCItem.mrc_id == MRC.id)
        .where(MRCItem.hazmat_item_id == item_id)
    ).scalars().all()
    return render(request, "catalog/item_detail.html", {"item": item, "used_in": used_in}, nav="spmigs")


@router.post("/items/{item_id}/edit", name="edit_item")
def edit_item(
    item_id: int,
    request: Request,
    nomenclature: str = Form(...),
    niin: str = Form(""),
    unit_of_issue: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_session),
):
    item = db.get(HazmatItem, item_id)
    if not item:
        raise HTTPException(404)
    item.nomenclature = nomenclature.strip()
    item.niin = niin.strip() or None
    item.unit_of_issue = unit_of_issue.strip() or None
    item.notes = notes.strip() or None
    db.commit()
    return RedirectResponse(request.url_for("view_item", item_id=item_id), status_code=303)


@router.post("/items/{item_id}/delete", name="delete_item")
def delete_item(item_id: int, request: Request, db: Session = Depends(get_session)):
    item = db.get(HazmatItem, item_id)
    if not item:
        raise HTTPException(404)
    spmig_id = item.spmig_id
    in_use = db.scalar(select(MRCItem).where(MRCItem.hazmat_item_id == item_id))
    if in_use:
        raise HTTPException(
            400,
            "Item is attached to one or more MRCs. Remove it from those MRCs first.",
        )
    db.delete(item)
    db.commit()
    return RedirectResponse(request.url_for("view_spmig", spmig_id=spmig_id), status_code=303)


# ============================================================
# Search (used by request builder)
# ============================================================

@router.get("/search/mrc", name="search_mrc")
def search_mrc(q: str = "", db: Session = Depends(get_session)):
    """Returns HTML fragment of (MIP, MRC) matches.

    Matches against MIP code/title and MRC code/description. Tokens are
    AND-combined so 'mip-foo m-1' narrows by both.
    """
    tokens = [t.strip() for t in q.split() if t.strip()]
    stmt = select(MRC).options(selectinload(MRC.mip)).join(MIP, MRC.mip_id == MIP.id)
    for tok in tokens:
        like = f"%{tok}%"
        stmt = stmt.where(
            (MIP.code.ilike(like))
            | (MIP.title.ilike(like))
            | (MRC.code.ilike(like))
            | (MRC.description.ilike(like))
        )
    stmt = stmt.order_by(MIP.code, MRC.code).limit(40)
    rows = db.execute(stmt).scalars().all()
    return render_partial("catalog/_mrc_search_results.html", {"rows": rows, "q": q})


@router.get("/search/items", name="search_items")
def search_items(q: str = "", db: Session = Depends(get_session)):
    """Returns HTML fragment of HazmatItem matches.

    Matches across SPMIG code, NIIN, and nomenclature. Tokens are
    AND-combined.
    """
    tokens = [t.strip() for t in q.split() if t.strip()]
    stmt = (
        select(HazmatItem)
        .options(selectinload(HazmatItem.spmig))
        .join(SPMIG, HazmatItem.spmig_id == SPMIG.id)
    )
    for tok in tokens:
        like = f"%{tok}%"
        stmt = stmt.where(
            (SPMIG.code.ilike(like))
            | (HazmatItem.nomenclature.ilike(like))
            | (HazmatItem.niin.ilike(like))
        )
    stmt = stmt.order_by(SPMIG.code, HazmatItem.nomenclature).limit(40)
    rows = db.execute(stmt).scalars().all()
    return render_partial("catalog/_item_search_results.html", {"rows": rows, "q": q})
