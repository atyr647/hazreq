"""Request-flow invariants:

- Snapshot strictness: catalog edits don't affect existing request lines
- Bulk-load creates correct snapshots with default qtys
- Dedup: same hazmat item never lands twice from different MRCs
- Swap-alternate updates the line snapshot in place
- Finalize requires qty > 0 on every line
- Finalize is idempotent (re-render doesn't break)
- Re-open clears finalized_at; re-finalize works
"""

from __future__ import annotations


def _seed(session):
    from app.models import MIP, MRC, SPMIG, HazmatItem, MRCItem

    sp = SPMIG(code="SPMIG-A", description="Lubricants")
    session.add(sp); session.flush()
    item1 = HazmatItem(spmig_id=sp.id, nomenclature="Oil 30W", niin="111")
    item2 = HazmatItem(spmig_id=sp.id, nomenclature="Oil 30W (alt)", niin="222")
    session.add_all([item1, item2]); session.flush()

    mip = MIP(code="MIP-1")
    session.add(mip); session.flush()
    mrc_a = MRC(mip_id=mip.id, code="M-1")
    mrc_b = MRC(mip_id=mip.id, code="M-2")
    session.add_all([mrc_a, mrc_b]); session.flush()

    session.add_all([
        MRCItem(mrc_id=mrc_a.id, hazmat_item_id=item1.id, default_qty=2, sort_order=10),
        MRCItem(mrc_id=mrc_b.id, hazmat_item_id=item1.id, default_qty=5, sort_order=10),
    ])
    session.commit()
    return {"sp": sp, "item1": item1, "item2": item2, "mip": mip, "mrc_a": mrc_a, "mrc_b": mrc_b}


def test_bulk_load_snapshots_catalog_state(client, session):
    s = _seed(session)
    r = client.get("/requests/new")
    assert r.status_code == 303
    rid = int(r.headers["location"].rstrip("/").rsplit("/", 1)[-1])

    resp = client.post(f"/requests/{rid}/load-mrc", data={"mrc_id": s["mrc_a"].id})
    assert resp.status_code == 200

    from app.models import RequestLine
    lines = session.query(RequestLine).filter_by(request_id=rid).all()
    assert len(lines) == 1
    assert lines[0].spmig_code == "SPMIG-A"
    assert lines[0].nomenclature == "Oil 30W"
    assert lines[0].niin == "111"
    assert lines[0].qty == 2
    assert lines[0].hazmat_item_id == s["item1"].id


def test_catalog_edits_do_not_mutate_existing_lines(client, session):
    s = _seed(session)
    r = client.get("/requests/new")
    rid = int(r.headers["location"].rstrip("/").rsplit("/", 1)[-1])
    client.post(f"/requests/{rid}/load-mrc", data={"mrc_id": s["mrc_a"].id})

    # Mutate the catalog AFTER the line was snapshotted.
    item = session.get(type(s["item1"]), s["item1"].id)
    item.nomenclature = "RENAMED"
    item.niin = "999"
    session.commit()

    from app.models import RequestLine
    line = session.query(RequestLine).filter_by(request_id=rid).one()
    # Snapshot is immutable.
    assert line.nomenclature == "Oil 30W"
    assert line.niin == "111"


def test_dedup_skips_item_already_on_request(client, session):
    s = _seed(session)
    r = client.get("/requests/new")
    rid = int(r.headers["location"].rstrip("/").rsplit("/", 1)[-1])
    client.post(f"/requests/{rid}/load-mrc", data={"mrc_id": s["mrc_a"].id})
    # Loading a different MRC that includes the same hazmat_item must skip it.
    resp = client.post(f"/requests/{rid}/load-mrc", data={"mrc_id": s["mrc_b"].id})
    assert resp.status_code == 200
    assert "Skipped" in resp.text

    from app.models import RequestLine
    lines = session.query(RequestLine).filter_by(request_id=rid).all()
    assert len(lines) == 1


def test_swap_alternate_updates_snapshot_in_place(client, session):
    s = _seed(session)
    r = client.get("/requests/new")
    rid = int(r.headers["location"].rstrip("/").rsplit("/", 1)[-1])
    client.post(f"/requests/{rid}/load-mrc", data={"mrc_id": s["mrc_a"].id})

    from app.models import RequestLine
    line = session.query(RequestLine).filter_by(request_id=rid).one()
    original_qty = line.qty

    resp = client.post(
        f"/requests/{rid}/lines/{line.id}/swap",
        data={"to_item_id": s["item2"].id},
    )
    assert resp.status_code == 200
    session.expire_all()
    line2 = session.query(RequestLine).filter_by(id=line.id).one()
    assert line2.nomenclature == "Oil 30W (alt)"
    assert line2.niin == "222"
    assert line2.qty == original_qty  # qty preserved
    assert line2.hazmat_item_id == s["item2"].id


def test_finalize_requires_qty_on_every_line(client, session):
    s = _seed(session)
    r = client.get("/requests/new")
    rid = int(r.headers["location"].rstrip("/").rsplit("/", 1)[-1])
    client.post(f"/requests/{rid}/load-mrc", data={"mrc_id": s["mrc_a"].id})
    # Wipe the auto-set qty.
    from app.models import RequestLine
    line = session.query(RequestLine).filter_by(request_id=rid).one()
    line.qty = None
    session.commit()

    resp = client.post(f"/requests/{rid}/finalize")
    assert resp.status_code == 400
    assert "quantity" in resp.text.lower()


def test_finalize_is_idempotent_and_reopen_works(client, session):
    s = _seed(session)
    r = client.get("/requests/new")
    rid = int(r.headers["location"].rstrip("/").rsplit("/", 1)[-1])
    client.post(f"/requests/{rid}/load-mrc", data={"mrc_id": s["mrc_a"].id})

    # First finalize.
    resp = client.post(f"/requests/{rid}/finalize")
    assert resp.status_code == 303
    from app.models import Request as ReqModel
    req = session.get(ReqModel, rid)
    first_finalized = req.finalized_at
    assert first_finalized is not None
    assert req.pdf_path is not None

    # Re-finalize: idempotent — finalized_at stays the same.
    resp = client.post(f"/requests/{rid}/finalize")
    assert resp.status_code == 303
    session.expire_all()
    req = session.get(ReqModel, rid)
    assert req.finalized_at == first_finalized

    # Re-open: clears finalized_at, leaves pdf_path.
    resp = client.post(f"/requests/{rid}/reopen")
    assert resp.status_code == 303
    session.expire_all()
    req = session.get(ReqModel, rid)
    assert req.finalized_at is None
    assert req.pdf_path is not None  # previous PDF still accessible


def test_finalized_request_is_read_only_until_reopened(client, session):
    s = _seed(session)
    r = client.get("/requests/new")
    rid = int(r.headers["location"].rstrip("/").rsplit("/", 1)[-1])
    client.post(f"/requests/{rid}/load-mrc", data={"mrc_id": s["mrc_a"].id})
    client.post(f"/requests/{rid}/finalize")

    # Patching a header field on a finalized request is rejected.
    resp = client.patch(f"/requests/{rid}", data={"workcenter": "X"})
    assert resp.status_code == 409
