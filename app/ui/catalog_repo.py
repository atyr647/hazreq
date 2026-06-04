"""In-process catalog CRUD for the Tk app.

Mirrors app/routers/catalog.py (minus HTTP): MIP/MRC and SPMIG/HazmatItem
create-edit-delete, plus the MRC↔item link table the request builder's
bulk-load depends on. Deletion guards match the web app exactly.

Read helpers return plain dataclasses so the UI can use them after the
session closes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import MIP, MRC, SPMIG, HazmatItem, MRCItem


class CatalogError(RuntimeError):
    """Validation / integrity failure, with a user-facing message."""


# ---- display rows ------------------------------------------------------

@dataclass(frozen=True)
class MrcNode:
    id: int
    code: str
    periodicity: str
    description: str
    item_count: int


@dataclass(frozen=True)
class MipNode:
    id: int
    code: str
    title: str
    mrcs: list[MrcNode] = field(default_factory=list)


@dataclass(frozen=True)
class ItemNode:
    id: int
    nomenclature: str
    niin: str
    unit_of_issue: str


@dataclass(frozen=True)
class SpmigNode:
    id: int
    code: str
    description: str
    items: list[ItemNode] = field(default_factory=list)


@dataclass(frozen=True)
class LinkedItem:
    id: int
    spmig_id: int
    spmig_code: str
    nomenclature: str
    niin: str


# ---- MIP / MRC tree ----------------------------------------------------

def mip_tree(s: Session) -> list[MipNode]:
    mips = s.execute(
        select(MIP)
        .options(selectinload(MIP.mrcs).selectinload(MRC.items))
        .order_by(MIP.code)
    ).scalars().all()
    out: list[MipNode] = []
    for m in mips:
        mrcs = [
            MrcNode(
                id=mr.id,
                code=mr.code,
                periodicity=mr.periodicity or "",
                description=mr.description or "",
                item_count=len(mr.items),
            )
            for mr in m.mrcs
        ]
        out.append(MipNode(id=m.id, code=m.code, title=m.title or "", mrcs=mrcs))
    return out


def get_mip(s: Session, mip_id: int) -> dict:
    m = s.get(MIP, mip_id)
    if not m:
        raise CatalogError("MIP not found")
    return {"code": m.code, "title": m.title or "", "notes": m.notes or ""}


def create_mip(s: Session, code: str, title: str = "", notes: str = "") -> int:
    code = (code or "").strip()
    if not code:
        raise CatalogError("Code is required")
    m = MIP(code=code, title=title.strip() or None, notes=notes.strip() or None)
    s.add(m)
    try:
        s.flush()
    except IntegrityError:
        raise CatalogError(f"MIP code '{code}' already exists") from None
    return m.id


def update_mip(s: Session, mip_id: int, code: str, title: str = "", notes: str = "") -> None:
    m = s.get(MIP, mip_id)
    if not m:
        raise CatalogError("MIP not found")
    if not (code or "").strip():
        raise CatalogError("Code is required")
    m.code, m.title, m.notes = code.strip(), title.strip() or None, notes.strip() or None
    try:
        s.flush()
    except IntegrityError:
        raise CatalogError(f"MIP code '{code}' already exists") from None


def delete_mip(s: Session, mip_id: int) -> None:
    m = s.get(MIP, mip_id)
    if m:
        s.delete(m)  # cascades to MRCs and their item links
        s.flush()


def get_mrc(s: Session, mrc_id: int) -> dict:
    mr = s.get(MRC, mrc_id)
    if not mr:
        raise CatalogError("MRC not found")
    return {
        "code": mr.code,
        "periodicity": mr.periodicity or "",
        "description": mr.description or "",
    }


def create_mrc(
    s: Session, mip_id: int, code: str, periodicity: str = "", description: str = ""
) -> int:
    if not s.get(MIP, mip_id):
        raise CatalogError("Parent MIP not found")
    code = (code or "").strip()
    if not code:
        raise CatalogError("MRC code is required")
    mr = MRC(
        mip_id=mip_id,
        code=code,
        periodicity=periodicity.strip() or None,
        description=description.strip() or None,
    )
    s.add(mr)
    try:
        s.flush()
    except IntegrityError:
        raise CatalogError(f"MRC '{code}' already exists under this MIP") from None
    return mr.id


def update_mrc(
    s: Session, mrc_id: int, code: str, periodicity: str = "", description: str = ""
) -> None:
    mr = s.get(MRC, mrc_id)
    if not mr:
        raise CatalogError("MRC not found")
    if not (code or "").strip():
        raise CatalogError("MRC code is required")
    mr.code = code.strip()
    mr.periodicity = periodicity.strip() or None
    mr.description = description.strip() or None
    try:
        s.flush()
    except IntegrityError:
        raise CatalogError("MRC code already exists under this MIP") from None


def delete_mrc(s: Session, mrc_id: int) -> None:
    mr = s.get(MRC, mrc_id)
    if mr:
        s.delete(mr)  # cascades to its item links
        s.flush()


# ---- MRC ↔ item links --------------------------------------------------

def mrc_items(s: Session, mrc_id: int) -> list[LinkedItem]:
    links = s.execute(
        select(MRCItem)
        .options(selectinload(MRCItem.hazmat_item).selectinload(HazmatItem.spmig))
        .where(MRCItem.mrc_id == mrc_id)
        .order_by(MRCItem.sort_order)
    ).scalars().all()
    out: list[LinkedItem] = []
    for link in links:
        it = link.hazmat_item
        if not it:
            continue
        out.append(
            LinkedItem(
                id=it.id,
                spmig_id=it.spmig_id,
                spmig_code=it.spmig.code if it.spmig else "",
                nomenclature=it.nomenclature,
                niin=it.niin or "",
            )
        )
    return out


def item_brief(s: Session, item_id: int) -> LinkedItem:
    """One hazmat item as a LinkedItem (with its SPMIG), for the MRC editor's
    in-memory buffer."""
    it = s.execute(
        select(HazmatItem)
        .options(selectinload(HazmatItem.spmig))
        .where(HazmatItem.id == item_id)
    ).scalar_one_or_none()
    if it is None:
        raise CatalogError("Item not found")
    return LinkedItem(
        id=it.id,
        spmig_id=it.spmig_id,
        spmig_code=it.spmig.code if it.spmig else "",
        nomenclature=it.nomenclature,
        niin=it.niin or "",
    )


def set_mrc_default(s: Session, mrc_id: int, item_id: int) -> list[str]:
    """Make `item_id` this MRC's default for its SPMIG. An MRC carries at
    most one item per SPMIG (loading the MRC then adds one line per SPMIG;
    operators reach the SPMIG's other items via Swap). If a different item
    from the same SPMIG is already linked, it is replaced in place and its
    nomenclature(s) returned so the caller can report the change.
    """
    if not s.get(MRC, mrc_id):
        raise CatalogError("MRC not found")
    it = s.get(HazmatItem, item_id)
    if not it:
        raise CatalogError("Hazmat item not found")
    if s.get(MRCItem, {"mrc_id": mrc_id, "hazmat_item_id": item_id}):
        raise CatalogError("Item already attached to this MRC")
    same_spmig = list(
        s.execute(
            select(MRCItem)
            .join(HazmatItem, MRCItem.hazmat_item_id == HazmatItem.id)
            .options(selectinload(MRCItem.hazmat_item))
            .where(MRCItem.mrc_id == mrc_id, HazmatItem.spmig_id == it.spmig_id)
        ).scalars().all()
    )
    if same_spmig:
        slot = min(lk.sort_order for lk in same_spmig)
        replaced = [lk.hazmat_item.nomenclature for lk in same_spmig if lk.hazmat_item]
        for lk in same_spmig:
            s.delete(lk)
        s.flush()
        s.add(MRCItem(mrc_id=mrc_id, hazmat_item_id=item_id, sort_order=slot))
        s.flush()
        return replaced
    last = s.scalar(
        select(MRCItem.sort_order)
        .where(MRCItem.mrc_id == mrc_id)
        .order_by(MRCItem.sort_order.desc())
    )
    s.add(MRCItem(mrc_id=mrc_id, hazmat_item_id=item_id, sort_order=(last or 0) + 10))
    s.flush()
    return []


def sync_mrc_items(s: Session, mrc_id: int, ordered_item_ids: list[int]) -> None:
    """Make the MRC's links exactly `ordered_item_ids`, in that order. Adds
    missing links, drops removed ones, and renumbers sort_order — a diff (not
    a delete-all-readd) so the audit log only records real changes. Callers
    are responsible for the one-item-per-SPMIG invariant (the editor enforces
    it as items are chosen)."""
    if not s.get(MRC, mrc_id):
        raise CatalogError("MRC not found")
    existing = {
        lk.hazmat_item_id: lk
        for lk in s.execute(
            select(MRCItem).where(MRCItem.mrc_id == mrc_id)
        ).scalars().all()
    }
    desired = list(dict.fromkeys(ordered_item_ids))  # de-dup, keep order
    for item_id, lk in list(existing.items()):
        if item_id not in desired:
            s.delete(lk)
    s.flush()
    for idx, item_id in enumerate(desired):
        order = (idx + 1) * 10
        lk = existing.get(item_id)
        if lk is not None:
            lk.sort_order = order
        else:
            s.add(MRCItem(mrc_id=mrc_id, hazmat_item_id=item_id, sort_order=order))
    s.flush()


def add_mrc_item(s: Session, mrc_id: int, item_id: int) -> None:
    if not s.get(MRC, mrc_id):
        raise CatalogError("MRC not found")
    if not s.get(HazmatItem, item_id):
        raise CatalogError("Hazmat item not found")
    if s.get(MRCItem, {"mrc_id": mrc_id, "hazmat_item_id": item_id}):
        raise CatalogError("Item already attached to this MRC")
    last = s.scalar(
        select(MRCItem.sort_order)
        .where(MRCItem.mrc_id == mrc_id)
        .order_by(MRCItem.sort_order.desc())
    )
    s.add(MRCItem(mrc_id=mrc_id, hazmat_item_id=item_id, sort_order=(last or 0) + 10))
    s.flush()  # so back-to-back adds in one session get distinct sort orders


def remove_mrc_item(s: Session, mrc_id: int, item_id: int) -> None:
    link = s.get(MRCItem, {"mrc_id": mrc_id, "hazmat_item_id": item_id})
    if link:
        s.delete(link)
        s.flush()  # so a same-session delete_item guard sees the link is gone


def move_mrc_item(s: Session, mrc_id: int, item_id: int, direction: str) -> None:
    links = list(
        s.execute(
            select(MRCItem).where(MRCItem.mrc_id == mrc_id).order_by(MRCItem.sort_order)
        ).scalars().all()
    )
    idx = next((i for i, lk in enumerate(links) if lk.hazmat_item_id == item_id), None)
    if idx is None:
        raise CatalogError("Item not on this MRC")
    if direction == "up" and idx > 0:
        j = idx - 1
    elif direction == "down" and idx < len(links) - 1:
        j = idx + 1
    else:
        return
    links[idx].sort_order, links[j].sort_order = links[j].sort_order, links[idx].sort_order
    s.flush()


# ---- SPMIG / item tree -------------------------------------------------

def spmig_tree(s: Session) -> list[SpmigNode]:
    spmigs = s.execute(
        select(SPMIG).options(selectinload(SPMIG.items)).order_by(SPMIG.code)
    ).scalars().all()
    out: list[SpmigNode] = []
    for sp in spmigs:
        items = [
            ItemNode(
                id=it.id,
                nomenclature=it.nomenclature,
                niin=it.niin or "",
                unit_of_issue=it.unit_of_issue or "",
            )
            for it in sp.items
        ]
        out.append(SpmigNode(id=sp.id, code=sp.code, description=sp.description or "", items=items))
    return out


def get_spmig(s: Session, spmig_id: int) -> dict:
    sp = s.get(SPMIG, spmig_id)
    if not sp:
        raise CatalogError("SPMIG not found")
    return {"code": sp.code, "description": sp.description or "", "notes": sp.notes or ""}


def create_spmig(s: Session, code: str, description: str = "", notes: str = "") -> int:
    code = (code or "").strip()
    if not code:
        raise CatalogError("Code is required")
    sp = SPMIG(code=code, description=description.strip() or None, notes=notes.strip() or None)
    s.add(sp)
    try:
        s.flush()
    except IntegrityError:
        raise CatalogError(f"SPMIG '{code}' already exists") from None
    return sp.id


def update_spmig(s: Session, spmig_id: int, code: str, description: str = "", notes: str = "") -> None:
    sp = s.get(SPMIG, spmig_id)
    if not sp:
        raise CatalogError("SPMIG not found")
    if not (code or "").strip():
        raise CatalogError("Code is required")
    sp.code, sp.description, sp.notes = code.strip(), description.strip() or None, notes.strip() or None
    try:
        s.flush()
    except IntegrityError:
        raise CatalogError(f"SPMIG '{code}' already exists") from None


def delete_spmig(s: Session, spmig_id: int) -> None:
    sp = s.get(SPMIG, spmig_id)
    if not sp:
        return
    if sp.items:
        raise CatalogError(
            "Cannot delete a SPMIG that still has hazmat items. Delete them first."
        )
    s.delete(sp)
    s.flush()


def get_item(s: Session, item_id: int) -> dict:
    it = s.get(HazmatItem, item_id)
    if not it:
        raise CatalogError("Item not found")
    return {
        "nomenclature": it.nomenclature,
        "niin": it.niin or "",
        "unit_of_issue": it.unit_of_issue or "",
        "notes": it.notes or "",
    }


def create_item(
    s: Session, spmig_id: int, nomenclature: str, niin: str = "",
    unit_of_issue: str = "", notes: str = "",
) -> int:
    if not s.get(SPMIG, spmig_id):
        raise CatalogError("Parent SPMIG not found")
    nomenclature = (nomenclature or "").strip()
    if not nomenclature:
        raise CatalogError("Nomenclature is required")
    it = HazmatItem(
        spmig_id=spmig_id,
        nomenclature=nomenclature,
        niin=niin.strip() or None,
        unit_of_issue=unit_of_issue.strip() or None,
        notes=notes.strip() or None,
    )
    s.add(it)
    s.flush()
    return it.id


def update_item(
    s: Session, item_id: int, nomenclature: str, niin: str = "",
    unit_of_issue: str = "", notes: str = "",
) -> None:
    it = s.get(HazmatItem, item_id)
    if not it:
        raise CatalogError("Item not found")
    if not (nomenclature or "").strip():
        raise CatalogError("Nomenclature is required")
    it.nomenclature = nomenclature.strip()
    it.niin = niin.strip() or None
    it.unit_of_issue = unit_of_issue.strip() or None
    it.notes = notes.strip() or None


def delete_item(s: Session, item_id: int) -> None:
    it = s.get(HazmatItem, item_id)
    if not it:
        return
    if s.scalar(select(MRCItem).where(MRCItem.hazmat_item_id == item_id)):
        raise CatalogError(
            "Item is attached to one or more MRCs. Remove it from those MRCs first."
        )
    s.delete(it)
    s.flush()
