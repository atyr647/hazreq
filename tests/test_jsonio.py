"""Round-trip + envelope-validation tests for app.services.jsonio."""

from __future__ import annotations

from datetime import datetime

import pytest

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
from app.services import jsonio


def _seed(session) -> dict:
    mip = MIP(code="MIP-1", title="Test plant")
    session.add(mip)
    session.flush()
    mrc = MRC(mip_id=mip.id, code="Q-1", periodicity="Quarterly")
    session.add(mrc)
    sp = SPMIG(code="SPMIG-1234", description="Test SPMIG")
    session.add(sp)
    session.flush()
    item = HazmatItem(spmig_id=sp.id, nomenclature="Threadlocker", niin="00-111-2222")
    session.add(item)
    session.flush()
    session.add(MRCItem(mrc_id=mrc.id, hazmat_item_id=item.id, sort_order=10))
    req = Request(
        datetime_of_request=datetime(2026, 1, 1, 12, 0),
        requestor_name="ET2 Tester",
        workcenter="EE01",
    )
    session.add(req)
    session.flush()
    session.add(
        RequestLine(
            request_id=req.id,
            sort_order=10,
            hazmat_item_id=item.id,
            spmig_code=sp.code,
            nomenclature=item.nomenclature,
            niin=item.niin,
            qty=2,
        )
    )
    session.commit()
    return {"mip": mip.id, "mrc": mrc.id, "spmig": sp.id, "item": item.id, "req": req.id}


def test_export_envelope(session):
    _seed(session)
    payload = jsonio.export_to_dict(session)
    assert payload["type"] == "hazreq-backup"
    assert payload["schemaVersion"] == 1
    assert "exportedAt" in payload
    for table in ("mips", "mrcs", "spmigs", "hazmat_items", "mrc_items",
                  "requests", "request_lines", "audit_log"):
        assert table in payload, f"missing {table}"


def test_round_trip(session):
    ids = _seed(session)
    audit_count_before = session.query(AuditLog).count()
    payload = jsonio.export_to_dict(session)
    counts = jsonio.import_from_dict(session, payload)

    # Every list round-trips its row count.
    assert counts["mips"] == 1
    assert counts["mrcs"] == 1
    assert counts["request_lines"] == 1
    assert counts["audit_log"] == audit_count_before

    # Re-imported rows keep their ids and relationships.
    assert session.query(MIP).filter_by(id=ids["mip"]).one().code == "MIP-1"
    assert session.query(RequestLine).filter_by(request_id=ids["req"]).one().qty == 2

    # Audit log size didn't double after import (proves skip_audit works).
    assert session.query(AuditLog).count() == audit_count_before


def test_rejects_wrong_envelope(session):
    with pytest.raises(jsonio.InvalidBackup):
        jsonio.import_from_dict(session, {"type": "shorecalc-backup", "schemaVersion": 1})


def test_rejects_wrong_schema_version(session):
    with pytest.raises(jsonio.InvalidBackup):
        jsonio.import_from_dict(session, {"type": "hazreq-backup", "schemaVersion": 99})


def test_rejects_non_object(session):
    with pytest.raises(jsonio.InvalidBackup):
        jsonio.import_from_dict(session, [1, 2, 3])  # type: ignore[arg-type]


def test_import_replaces_existing(session):
    """An import wipes existing rows (replace, not merge)."""
    _seed(session)
    empty_payload = {
        "schemaVersion": 1,
        "type": "hazreq-backup",
        "exportedAt": datetime.utcnow().isoformat(),
    }
    counts = jsonio.import_from_dict(session, empty_payload)
    assert all(v == 0 for v in counts.values())
    assert session.query(MIP).count() == 0
    assert session.query(Request).count() == 0
    assert session.query(AuditLog).count() == 0
