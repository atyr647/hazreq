"""CSV export/import for the catalog tables.

The export bundles five CSVs into one .zip. The import accepts either
the full .zip or any subset of the CSVs and applies them in FK order:
spmig → mip → hazmat_item → mrc → mrc_item.

Records are matched on natural keys (spmig.code, mip.code, mrc(mip,code),
hazmat_item(spmig,nomenclature[,niin])) so the same export reapplied
later updates rather than duplicates.

Requests are not exported here — the SQLite .db backup already carries
them and snapshot semantics make CSV round-tripping risky.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from dataclasses import dataclass
from typing import IO

from sqlalchemy.orm import Session

from app.models import MIP, MRC, SPMIG, HazmatItem, MRCItem

log = logging.getLogger(__name__)


CSV_FILES = ("spmigs.csv", "mips.csv", "hazmat_items.csv", "mrcs.csv", "mrc_items.csv")


# ============================================================
# Export
# ============================================================

def export_zip(db: Session) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("spmigs.csv", _export_spmigs(db))
        zf.writestr("mips.csv", _export_mips(db))
        zf.writestr("hazmat_items.csv", _export_items(db))
        zf.writestr("mrcs.csv", _export_mrcs(db))
        zf.writestr("mrc_items.csv", _export_mrc_items(db))
    return buf.getvalue()


def _writer(headers: list[str]) -> tuple[io.StringIO, csv.DictWriter]:
    s = io.StringIO()
    w = csv.DictWriter(s, fieldnames=headers, lineterminator="\n")
    w.writeheader()
    return s, w


def _export_spmigs(db: Session) -> str:
    s, w = _writer(["code", "description", "notes"])
    for sp in db.query(SPMIG).order_by(SPMIG.code).all():
        w.writerow({"code": sp.code, "description": sp.description or "", "notes": sp.notes or ""})
    return s.getvalue()


def _export_mips(db: Session) -> str:
    s, w = _writer(["code", "title", "notes"])
    for m in db.query(MIP).order_by(MIP.code).all():
        w.writerow({"code": m.code, "title": m.title or "", "notes": m.notes or ""})
    return s.getvalue()


def _export_items(db: Session) -> str:
    s, w = _writer(["spmig_code", "nomenclature", "niin", "unit_of_issue", "notes"])
    for it in (
        db.query(HazmatItem)
        .join(SPMIG, HazmatItem.spmig_id == SPMIG.id)
        .order_by(SPMIG.code, HazmatItem.nomenclature)
        .all()
    ):
        w.writerow(
            {
                "spmig_code": it.spmig.code,
                "nomenclature": it.nomenclature,
                "niin": it.niin or "",
                "unit_of_issue": it.unit_of_issue or "",
                "notes": it.notes or "",
            }
        )
    return s.getvalue()


def _export_mrcs(db: Session) -> str:
    s, w = _writer(["mip_code", "code", "periodicity", "description"])
    for c in (
        db.query(MRC)
        .join(MIP, MRC.mip_id == MIP.id)
        .order_by(MIP.code, MRC.code)
        .all()
    ):
        w.writerow(
            {
                "mip_code": c.mip.code,
                "code": c.code,
                "periodicity": c.periodicity or "",
                "description": c.description or "",
            }
        )
    return s.getvalue()


def _export_mrc_items(db: Session) -> str:
    s, w = _writer(["mip_code", "mrc_code", "spmig_code", "nomenclature", "niin", "sort_order"])
    for link in (
        db.query(MRCItem)
        .join(MRC, MRCItem.mrc_id == MRC.id)
        .join(MIP, MRC.mip_id == MIP.id)
        .join(HazmatItem, MRCItem.hazmat_item_id == HazmatItem.id)
        .join(SPMIG, HazmatItem.spmig_id == SPMIG.id)
        .order_by(MIP.code, MRC.code, MRCItem.sort_order)
        .all()
    ):
        w.writerow(
            {
                "mip_code": link.mrc.mip.code,
                "mrc_code": link.mrc.code,
                "spmig_code": link.hazmat_item.spmig.code,
                "nomenclature": link.hazmat_item.nomenclature,
                "niin": link.hazmat_item.niin or "",
                "sort_order": link.sort_order,
            }
        )
    return s.getvalue()


# ============================================================
# Import
# ============================================================

@dataclass
class ImportReport:
    spmigs_created: int = 0
    spmigs_updated: int = 0
    mips_created: int = 0
    mips_updated: int = 0
    items_created: int = 0
    items_updated: int = 0
    mrcs_created: int = 0
    mrcs_updated: int = 0
    links_created: int = 0
    links_updated: int = 0
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

    def summary(self) -> str:
        return (
            f"SPMIGs: {self.spmigs_created} new, {self.spmigs_updated} updated; "
            f"MIPs: {self.mips_created} new, {self.mips_updated} updated; "
            f"Items: {self.items_created} new, {self.items_updated} updated; "
            f"MRCs: {self.mrcs_created} new, {self.mrcs_updated} updated; "
            f"Links: {self.links_created} new, {self.links_updated} updated."
        )


def import_zip(db: Session, payload: bytes) -> ImportReport:
    """Import a .zip containing any subset of the catalog CSVs."""
    report = ImportReport()
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        names = set(zf.namelist())
        # Apply in FK order, regardless of input order.
        if "spmigs.csv" in names:
            _import_spmigs(db, zf.open("spmigs.csv"), report)
        if "mips.csv" in names:
            _import_mips(db, zf.open("mips.csv"), report)
        if "hazmat_items.csv" in names:
            _import_items(db, zf.open("hazmat_items.csv"), report)
        if "mrcs.csv" in names:
            _import_mrcs(db, zf.open("mrcs.csv"), report)
        if "mrc_items.csv" in names:
            _import_mrc_items(db, zf.open("mrc_items.csv"), report)
    db.commit()
    return report


def _read_csv(stream: IO[bytes]) -> list[dict[str, str]]:
    text = io.TextIOWrapper(stream, encoding="utf-8-sig", newline="")
    return list(csv.DictReader(text))


def _import_spmigs(db: Session, stream, report: ImportReport) -> None:
    for row in _read_csv(stream):
        code = (row.get("code") or "").strip()
        if not code:
            continue
        sp = db.query(SPMIG).filter_by(code=code).one_or_none()
        if sp:
            sp.description = (row.get("description") or "").strip() or None
            sp.notes = (row.get("notes") or "").strip() or None
            report.spmigs_updated += 1
        else:
            db.add(
                SPMIG(
                    code=code,
                    description=(row.get("description") or "").strip() or None,
                    notes=(row.get("notes") or "").strip() or None,
                )
            )
            report.spmigs_created += 1
    db.flush()


def _import_mips(db: Session, stream, report: ImportReport) -> None:
    for row in _read_csv(stream):
        code = (row.get("code") or "").strip()
        if not code:
            continue
        m = db.query(MIP).filter_by(code=code).one_or_none()
        if m:
            m.title = (row.get("title") or "").strip() or None
            m.notes = (row.get("notes") or "").strip() or None
            report.mips_updated += 1
        else:
            db.add(
                MIP(
                    code=code,
                    title=(row.get("title") or "").strip() or None,
                    notes=(row.get("notes") or "").strip() or None,
                )
            )
            report.mips_created += 1
    db.flush()


def _import_items(db: Session, stream, report: ImportReport) -> None:
    for row in _read_csv(stream):
        sp_code = (row.get("spmig_code") or "").strip()
        nom = (row.get("nomenclature") or "").strip()
        niin = (row.get("niin") or "").strip() or None
        if not sp_code or not nom:
            report.errors.append(f"hazmat_items: row missing spmig_code/nomenclature: {row}")
            continue
        sp = db.query(SPMIG).filter_by(code=sp_code).one_or_none()
        if not sp:
            report.errors.append(f"hazmat_items: unknown SPMIG '{sp_code}', skipping {nom}")
            continue
        # Match on (spmig, nomenclature, niin) — the closest thing to a
        # natural key for items.
        q = db.query(HazmatItem).filter_by(spmig_id=sp.id, nomenclature=nom)
        if niin:
            q = q.filter_by(niin=niin)
        existing = q.one_or_none()
        if existing:
            existing.niin = niin
            existing.unit_of_issue = (row.get("unit_of_issue") or "").strip() or None
            existing.notes = (row.get("notes") or "").strip() or None
            report.items_updated += 1
        else:
            db.add(
                HazmatItem(
                    spmig_id=sp.id,
                    nomenclature=nom,
                    niin=niin,
                    unit_of_issue=(row.get("unit_of_issue") or "").strip() or None,
                    notes=(row.get("notes") or "").strip() or None,
                )
            )
            report.items_created += 1
    db.flush()


def _import_mrcs(db: Session, stream, report: ImportReport) -> None:
    for row in _read_csv(stream):
        mip_code = (row.get("mip_code") or "").strip()
        code = (row.get("code") or "").strip()
        if not mip_code or not code:
            report.errors.append(f"mrcs: row missing mip_code/code: {row}")
            continue
        mip = db.query(MIP).filter_by(code=mip_code).one_or_none()
        if not mip:
            report.errors.append(f"mrcs: unknown MIP '{mip_code}', skipping {code}")
            continue
        existing = db.query(MRC).filter_by(mip_id=mip.id, code=code).one_or_none()
        if existing:
            existing.periodicity = (row.get("periodicity") or "").strip() or None
            existing.description = (row.get("description") or "").strip() or None
            report.mrcs_updated += 1
        else:
            db.add(
                MRC(
                    mip_id=mip.id,
                    code=code,
                    periodicity=(row.get("periodicity") or "").strip() or None,
                    description=(row.get("description") or "").strip() or None,
                )
            )
            report.mrcs_created += 1
    db.flush()


def _import_mrc_items(db: Session, stream, report: ImportReport) -> None:
    for row in _read_csv(stream):
        mip_code = (row.get("mip_code") or "").strip()
        mrc_code = (row.get("mrc_code") or "").strip()
        sp_code = (row.get("spmig_code") or "").strip()
        nom = (row.get("nomenclature") or "").strip()
        niin = (row.get("niin") or "").strip() or None
        if not (mip_code and mrc_code and sp_code and nom):
            report.errors.append(f"mrc_items: missing required field: {row}")
            continue
        mip = db.query(MIP).filter_by(code=mip_code).one_or_none()
        if not mip:
            report.errors.append(f"mrc_items: unknown MIP '{mip_code}'")
            continue
        mrc = db.query(MRC).filter_by(mip_id=mip.id, code=mrc_code).one_or_none()
        if not mrc:
            report.errors.append(f"mrc_items: unknown MRC '{mip_code}/{mrc_code}'")
            continue
        sp = db.query(SPMIG).filter_by(code=sp_code).one_or_none()
        if not sp:
            report.errors.append(f"mrc_items: unknown SPMIG '{sp_code}'")
            continue
        q = db.query(HazmatItem).filter_by(spmig_id=sp.id, nomenclature=nom)
        if niin:
            q = q.filter_by(niin=niin)
        item = q.one_or_none()
        if not item:
            report.errors.append(
                f"mrc_items: unknown hazmat_item ({sp_code}, {nom}, NIIN={niin})"
            )
            continue
        try:
            sort_order = int(row.get("sort_order") or 0)
        except ValueError:
            sort_order = 0
        existing = db.get(MRCItem, {"mrc_id": mrc.id, "hazmat_item_id": item.id})
        if existing:
            existing.sort_order = sort_order
            report.links_updated += 1
        else:
            db.add(
                MRCItem(
                    mrc_id=mrc.id,
                    hazmat_item_id=item.id,
                    sort_order=sort_order,
                )
            )
            report.links_created += 1
    db.flush()
