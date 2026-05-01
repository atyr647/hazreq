"""Catalog invariants: per-MIP MRC uniqueness, SPMIG-required items, etc."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError


def test_mrc_unique_per_mip(client, session):
    from app.models import MIP, MRC

    a = MIP(code="MIP-A")
    b = MIP(code="MIP-B")
    session.add_all([a, b]); session.commit()

    # Same MRC code under TWO different MIPs is fine.
    session.add(MRC(mip_id=a.id, code="M-1"))
    session.add(MRC(mip_id=b.id, code="M-1"))
    session.commit()

    # Same MRC code under the SAME MIP must fail.
    session.add(MRC(mip_id=a.id, code="M-1"))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
    else:
        raise AssertionError("Duplicate MRC under same MIP should fail")


def test_hazmat_item_requires_spmig(session):
    from app.models import HazmatItem

    # spmig_id is NOT NULL — should fail at flush.
    session.add(HazmatItem(spmig_id=None, nomenclature="Orphan"))
    try:
        session.flush()
    except (IntegrityError, Exception):
        session.rollback()
    else:
        raise AssertionError("Hazmat item without SPMIG should fail")


def test_mrc_create_requires_existing_mip(client):
    # Trying to create an MRC under a nonexistent MIP returns 404.
    r = client.post("/catalog/mips/9999/mrcs", data={"code": "M-1"})
    assert r.status_code == 404


def test_compound_search_returns_both_matches_for_shared_mrc_code(client, session):
    from app.models import MIP, MRC

    session.add_all([
        MIP(code="MIP-X"),
        MIP(code="MIP-Y"),
    ])
    session.commit()
    x = session.query(MIP).filter_by(code="MIP-X").one()
    y = session.query(MIP).filter_by(code="MIP-Y").one()
    session.add(MRC(mip_id=x.id, code="M-1"))
    session.add(MRC(mip_id=y.id, code="M-1"))
    session.commit()

    r = client.get("/catalog/search/mrc", params={"q": "M-1"})
    assert r.status_code == 200
    body = r.text
    assert "MIP-X / M-1" in body
    assert "MIP-Y / M-1" in body
