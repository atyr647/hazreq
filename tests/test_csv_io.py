"""CSV catalog round-trip."""

from __future__ import annotations

import io
import zipfile


def test_export_then_import_round_trip(client, session):
    from app.models import MIP, MRC, SPMIG, HazmatItem, MRCItem
    from app.services import csvio

    sp = SPMIG(code="SPMIG-1", description="Lub")
    session.add(sp); session.flush()
    item = HazmatItem(spmig_id=sp.id, nomenclature="Oil", niin="111")
    session.add(item); session.flush()
    mip = MIP(code="MIP-1", title="System")
    session.add(mip); session.flush()
    mrc = MRC(mip_id=mip.id, code="M-1", periodicity="Monthly")
    session.add(mrc); session.flush()
    session.add(MRCItem(mrc_id=mrc.id, hazmat_item_id=item.id, sort_order=10))
    session.commit()

    payload = csvio.export_zip(session)
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        names = set(zf.namelist())
    assert names == {"spmigs.csv", "mips.csv", "hazmat_items.csv", "mrcs.csv", "mrc_items.csv"}

    # Wipe everything.
    session.query(MRCItem).delete()
    session.query(HazmatItem).delete()
    session.query(MRC).delete()
    session.query(MIP).delete()
    session.query(SPMIG).delete()
    session.commit()
    assert session.query(SPMIG).count() == 0

    # Re-import.
    report = csvio.import_zip(session, payload)
    assert report.spmigs_created == 1
    assert report.mips_created == 1
    assert report.items_created == 1
    assert report.mrcs_created == 1
    assert report.links_created == 1
    assert not report.errors

    # State is restored.
    assert session.query(SPMIG).filter_by(code="SPMIG-1").one().description == "Lub"
    assert session.query(MRCItem).count() == 1


def test_reimport_updates_rather_than_duplicates(client, session):
    from app.models import MIP, SPMIG
    from app.services import csvio

    session.add(SPMIG(code="SPMIG-1", description="v1"))
    session.add(MIP(code="MIP-1", title="v1"))
    session.commit()

    payload = csvio.export_zip(session)

    # Mutate.
    sp = session.query(SPMIG).filter_by(code="SPMIG-1").one()
    sp.description = "MUTATED"
    session.commit()

    # Re-import the original — should restore description, not duplicate.
    report = csvio.import_zip(session, payload)
    assert report.spmigs_created == 0
    assert report.spmigs_updated == 1
    assert session.query(SPMIG).count() == 1
    assert session.query(SPMIG).one().description == "v1"


def test_admin_export_endpoint_streams_zip(client, session):
    from app.models import SPMIG
    session.add(SPMIG(code="SPMIG-9"))
    session.commit()

    resp = client.get("/admin/catalog/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert "spmigs.csv" in zf.namelist()
        assert b"SPMIG-9" in zf.read("spmigs.csv")
