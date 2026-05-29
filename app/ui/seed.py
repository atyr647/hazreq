"""Sample catalog data for the prototype.

The repo ships no catalog, so the builder would have nothing to search.
`seed_if_empty` populates a small, believable hazmat catalog the first
time the prototype runs against an empty DB. It is a no-op once any MIP
exists, so it never touches a real catalog.

Multiple items share each SPMIG so the swap-alternate flow has something
to offer; MRCs link several items so bulk-load is exercised.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db import SessionLocal
from app.models import MIP, MRC, SPMIG, HazmatItem, MRCItem

# SPMIG code -> (description, [(nomenclature, niin, unit_of_issue), ...])
_SPMIGS = {
    "M0001": (
        "Cleaning solvents & degreasers",
        [
            ("CLEANING COMPOUND, SOLVENT (PD-680 TYPE II)", "001234567", "GL"),
            ("DEGREASER, AQUEOUS, BIODEGRADABLE", "012345678", "GL"),
            ("CLEANER, ELECTRICAL CONTACT", "023456789", "CN"),
        ],
    ),
    "G0102": (
        "Greases & lubricating oils",
        [
            ("GREASE, AIRCRAFT, GENERAL PURPOSE (MIL-PRF-23827)", "034567890", "LB"),
            ("LUBRICATING OIL, GENERAL PURPOSE (MIL-PRF-32033)", "045678901", "QT"),
            ("GREASE, MOLYBDENUM DISULFIDE", "056789012", "LB"),
        ],
    ),
    "P0440": (
        "Paints, primers & coatings",
        [
            ("PRIMER COATING, EPOXY (MIL-PRF-23377)", "067890123", "KT"),
            ("ENAMEL, GLOSS, HAZE GRAY (FED-STD-595)", "078901234", "GL"),
            ("TOPCOAT, POLYURETHANE (MIL-PRF-85285)", "089012345", "KT"),
        ],
    ),
    "A0010": (
        "Adhesives & sealants",
        [
            ("SEALING COMPOUND, POLYSULFIDE (MIL-PRF-81733)", "090123456", "KT"),
            ("ADHESIVE, EPOXY, TWO-PART", "101234567", "KT"),
        ],
    ),
}

# MIP code -> (title, [(mrc_code, periodicity, description, [item nomenclature substrings]), ...])
_MIPS = {
    "MIP 6541/001": (
        "Diesel engine — preventive maintenance",
        [
            (
                "R-1",
                "Monthly",
                "Lubrication service & exterior cleaning",
                ["GREASE, AIRCRAFT", "LUBRICATING OIL", "CLEANING COMPOUND, SOLVENT"],
            ),
            (
                "Q-3",
                "Quarterly",
                "Corrosion treatment",
                ["PRIMER COATING, EPOXY", "ENAMEL, GLOSS", "DEGREASER, AQUEOUS"],
            ),
        ],
    ),
    "MIP 4521/002": (
        "Hull & deck — corrosion control",
        [
            (
                "S-2",
                "Semiannual",
                "Topside preservation",
                [
                    "PRIMER COATING, EPOXY",
                    "TOPCOAT, POLYURETHANE",
                    "SEALING COMPOUND, POLYSULFIDE",
                ],
            ),
        ],
    ),
}


def seed_if_empty() -> bool:
    """Populate the sample catalog if no MIP exists. Returns True if it
    seeded, False if the catalog already had data."""
    s = SessionLocal()
    try:
        if s.scalar(select(MIP).limit(1)) is not None:
            return False

        # SPMIGs + items, indexed by nomenclature for MRC linking.
        items_by_name: dict[str, HazmatItem] = {}
        for code, (desc, items) in _SPMIGS.items():
            sp = SPMIG(code=code, description=desc)
            s.add(sp)
            s.flush()
            for nomen, niin, uoi in items:
                it = HazmatItem(
                    spmig_id=sp.id, nomenclature=nomen, niin=niin, unit_of_issue=uoi
                )
                s.add(it)
                s.flush()
                items_by_name[nomen] = it

        for mip_code, (title, mrcs) in _MIPS.items():
            mip = MIP(code=mip_code, title=title)
            s.add(mip)
            s.flush()
            for mrc_code, periodicity, mrc_desc, item_substrings in mrcs:
                mrc = MRC(
                    mip_id=mip.id,
                    code=mrc_code,
                    periodicity=periodicity,
                    description=mrc_desc,
                )
                s.add(mrc)
                s.flush()
                for order, substr in enumerate(item_substrings):
                    match = next(
                        (it for name, it in items_by_name.items() if name.startswith(substr)),
                        None,
                    )
                    if match:
                        s.add(
                            MRCItem(
                                mrc_id=mrc.id,
                                hazmat_item_id=match.id,
                                sort_order=order * 10,
                            )
                        )
        s.commit()
        return True
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
