"""Audit log auto-captures catalog mutations."""

from __future__ import annotations


def test_create_update_delete_are_logged(client, session):
    from app.models import MIP, AuditLog

    # CREATE
    m = MIP(code="MIP-AUD", title="t1")
    session.add(m); session.commit()

    # UPDATE
    m.title = "t2"
    session.commit()

    # DELETE
    session.delete(m); session.commit()

    rows = (
        session.query(AuditLog)
        .filter(AuditLog.entity_type == "mip", AuditLog.entity_key == "MIP-AUD")
        .order_by(AuditLog.id)
        .all()
    )
    actions = [r.action for r in rows]
    assert actions == ["create", "update", "delete"]
    assert "title" in rows[1].summary  # update mentions changed field


def test_no_log_for_request_lifecycle(client, session):
    from app.models import AuditLog, MIP, MRC, SPMIG, HazmatItem, MRCItem

    sp = SPMIG(code="SPMIG-Z"); session.add(sp); session.flush()
    item = HazmatItem(spmig_id=sp.id, nomenclature="X"); session.add(item); session.flush()
    mip = MIP(code="MIP-Z"); session.add(mip); session.flush()
    mrc = MRC(mip_id=mip.id, code="M-1"); session.add(mrc); session.flush()
    session.add(MRCItem(mrc_id=mrc.id, hazmat_item_id=item.id, sort_order=10))
    session.commit()

    catalog_count = session.query(AuditLog).count()

    # Now create a request and load the MRC into it — should NOT emit audit rows.
    r = client.get("/requests/new")
    rid = int(r.headers["location"].rstrip("/").rsplit("/", 1)[-1])
    client.post(f"/requests/{rid}/load-mrc", data={"mrc_id": mrc.id})

    # Audit count is unchanged — request activity is operational, not catalog.
    assert session.query(AuditLog).count() == catalog_count


def test_audit_view_endpoint(client, session):
    from app.models import SPMIG

    session.add(SPMIG(code="SPMIG-VIEW"))
    session.commit()
    resp = client.get("/admin/log")
    assert resp.status_code == 200
    assert "SPMIG-VIEW" in resp.text
    # Filter by entity type
    resp = client.get("/admin/log", params={"entity_type": "spmig"})
    assert resp.status_code == 200
    assert "SPMIG-VIEW" in resp.text
    # Filter by action
    resp = client.get("/admin/log", params={"action": "create", "q": "SPMIG-VIEW"})
    assert resp.status_code == 200
    assert "SPMIG-VIEW" in resp.text
