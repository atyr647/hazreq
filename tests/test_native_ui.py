"""Functional tests for the native Tk app's data layer.

Exercises every public function in app.ui.repo and app.ui.catalog_repo
end-to-end against the test DB — the same logic the Tk screens call. The
widgets themselves are smoke-driven separately (needs a display); this
covers the behaviour.
"""

from __future__ import annotations

import pytest

from app.ui import catalog_repo as cat
from app.ui import repo

# ---- helpers -----------------------------------------------------------

def _build_catalog(s):
    """A small catalog: one SPMIG with 3 items, one MIP/MRC linking 2."""
    sp = cat.create_spmig(s, "SP1", "solvents")
    i1 = cat.create_item(s, sp, "CLEANER A", "111111111", "GL")
    i2 = cat.create_item(s, sp, "CLEANER B", "222222222", "GL")
    i3 = cat.create_item(s, sp, "CLEANER C", "333333333", "CN")
    mip = cat.create_mip(s, "MIP-1", "engine")
    mrc = cat.create_mrc(s, mip, "R-1", "Monthly", "lube")
    cat.add_mrc_item(s, mrc, i1)
    cat.add_mrc_item(s, mrc, i2)
    return {"sp": sp, "items": [i1, i2, i3], "mip": mip, "mrc": mrc}


# ====================================================================
# catalog_repo
# ====================================================================

def test_catalog_create_and_trees(session):
    with repo.session_scope() as s:
        _build_catalog(s)
    with repo.session_scope() as s:
        mips = cat.mip_tree(s)
        spmigs = cat.spmig_tree(s)
    assert [m.code for m in mips] == ["MIP-1"]
    assert mips[0].mrcs[0].code == "R-1"
    assert mips[0].mrcs[0].item_count == 2
    assert [sp.code for sp in spmigs] == ["SP1"]
    assert len(spmigs[0].items) == 3


def test_catalog_unique_code_guards(session):
    with repo.session_scope() as s:
        cat.create_mip(s, "DUP")
    with pytest.raises(cat.CatalogError, match="already exists"), repo.session_scope() as s:
        cat.create_mip(s, "DUP")
    with repo.session_scope() as s:
        cat.create_spmig(s, "SPDUP")
    with pytest.raises(cat.CatalogError, match="already exists"), repo.session_scope() as s:
        cat.create_spmig(s, "SPDUP")


def test_catalog_required_fields(session):
    with pytest.raises(cat.CatalogError, match="Code is required"), repo.session_scope() as s:
        cat.create_mip(s, "   ")
    with repo.session_scope() as s:
        sp = cat.create_spmig(s, "SPX")
    with pytest.raises(cat.CatalogError, match="Nomenclature is required"), repo.session_scope() as s:
        cat.create_item(s, sp, "  ")


def test_catalog_edit(session):
    with repo.session_scope() as s:
        ids = _build_catalog(s)
    with repo.session_scope() as s:
        cat.update_mip(s, ids["mip"], "MIP-1B", "engine v2", "note")
        cat.update_mrc(s, ids["mrc"], "R-2", "Weekly", "lube v2")
        cat.update_item(s, ids["items"][0], "CLEANER A2", "999", "EA")
    with repo.session_scope() as s:
        assert cat.get_mip(s, ids["mip"])["code"] == "MIP-1B"
        assert cat.get_mrc(s, ids["mrc"])["code"] == "R-2"
        assert cat.get_item(s, ids["items"][0])["nomenclature"] == "CLEANER A2"


def test_mrc_item_link_dedup_reorder_remove(session):
    with repo.session_scope() as s:
        ids = _build_catalog(s)
        mrc, items = ids["mrc"], ids["items"]
    # dedup
    with pytest.raises(cat.CatalogError, match="already attached"), repo.session_scope() as s:
        cat.add_mrc_item(s, mrc, items[0])
    # add a third, then reorder it to the top
    with repo.session_scope() as s:
        cat.add_mrc_item(s, mrc, items[2])
        order = [li.id for li in cat.mrc_items(s, mrc)]
        assert order == [items[0], items[1], items[2]]
        cat.move_mrc_item(s, mrc, items[2], "up")
        assert [li.id for li in cat.mrc_items(s, mrc)] == [items[0], items[2], items[1]]
        cat.move_mrc_item(s, mrc, items[0], "up")  # already top → no-op
        assert [li.id for li in cat.mrc_items(s, mrc)][0] == items[0]
    # remove
    with repo.session_scope() as s:
        cat.remove_mrc_item(s, mrc, items[0])
        assert items[0] not in [li.id for li in cat.mrc_items(s, mrc)]


def test_delete_guards_and_cascade(session):
    with repo.session_scope() as s:
        ids = _build_catalog(s)
    # SPMIG with items blocked
    with pytest.raises(cat.CatalogError, match="still has hazmat items"), repo.session_scope() as s:
        cat.delete_spmig(s, ids["sp"])
    # item linked to an MRC blocked
    with pytest.raises(cat.CatalogError, match="attached to one or more MRCs"), repo.session_scope() as s:
        cat.delete_item(s, ids["items"][0])
    # deleting the MIP cascades its MRC + links; item then deletable
    with repo.session_scope() as s:
        cat.delete_mip(s, ids["mip"])
        assert cat.mip_tree(s) == []
        cat.delete_item(s, ids["items"][0])  # no longer linked
    with repo.session_scope() as s:
        # SP now has 2 items; delete them then the SPMIG
        cat.delete_item(s, ids["items"][1])
        cat.delete_item(s, ids["items"][2])
        cat.delete_spmig(s, ids["sp"])
        assert cat.spmig_tree(s) == []


# ====================================================================
# repo — request flow
# ====================================================================

def test_request_header_and_datetime(session):
    with repo.session_scope() as s:
        rid = repo.create_draft(s)
    with repo.session_scope() as s:
        repo.update_header(s, rid, requestor_name="  Jane  ", workcenter="W",
                           lpo="L", hazmat_location="Bay", datetime_of_request="2026-05-29 09:30")
    with repo.session_scope() as s:
        r = repo.get_request(s, rid)
        assert r.requestor_name == "Jane"  # trimmed
        assert r.datetime_of_request.strftime("%Y-%m-%d %H:%M") == "2026-05-29 09:30"
    # blanking a field stores NULL
    with repo.session_scope() as s:
        repo.update_header(s, rid, requestor_name="")
    with repo.session_scope() as s:
        assert repo.get_request(s, rid).requestor_name is None


def test_search_tokens_and_load_mrc_dedup(session):
    with repo.session_scope() as s:
        ids = _build_catalog(s)
        rid = repo.create_draft(s)
    with repo.session_scope() as s:
        # AND-token search
        assert len(repo.search_mrc(s, "MIP-1 R-1")) == 1
        assert repo.search_items(s, "cleaner 333")[0].niin == "333333333"
    with repo.session_scope() as s:
        added, skipped = repo.load_mrc(s, rid, ids["mrc"])
        assert added == 2 and skipped == []
    with repo.session_scope() as s:
        added, skipped = repo.load_mrc(s, rid, ids["mrc"])  # all dupes now
        assert added == 0 and len(skipped) == 2


def test_add_item_dedup_and_manual_line(session):
    with repo.session_scope() as s:
        ids = _build_catalog(s)
        rid = repo.create_draft(s)
    with repo.session_scope() as s:
        assert repo.add_item(s, rid, ids["items"][0]) is True
    with repo.session_scope() as s:
        assert repo.add_item(s, rid, ids["items"][0]) is False  # dedup
    with repo.session_scope() as s:
        repo.add_manual_line(s, rid, "FREE", "FREE WIDGET", "000", 7)
    with repo.session_scope() as s:
        lines = sorted(repo.get_request(s, rid).lines, key=lambda x: x.sort_order)
        assert [ln.nomenclature for ln in lines] == ["CLEANER A", "FREE WIDGET"]
        assert lines[1].qty == 7


def test_qty_move_swap_delete_line(session):
    with repo.session_scope() as s:
        ids = _build_catalog(s)
        rid = repo.create_draft(s)
        repo.load_mrc(s, rid, ids["mrc"])
    with repo.session_scope() as s:
        lines = sorted(repo.get_request(s, rid).lines, key=lambda x: x.sort_order)
        l0, l1 = lines[0].id, lines[1].id
    with repo.session_scope() as s:
        repo.set_qty(s, rid, l0, 5)
        repo.move_line(s, rid, l0, "down")
    with repo.session_scope() as s:
        order = [ln.id for ln in sorted(repo.get_request(s, rid).lines, key=lambda x: x.sort_order)]
        assert order == [l1, l0]
    # swap l0 (CLEANER A) to an alternate in the same SPMIG. Extract the
    # alternate's fields inside the scope — ORM objects detach once it closes.
    with repo.session_scope() as s:
        alts = [(a.id, a.nomenclature) for a in repo.alternates(s, l0)]
        assert {name for _, name in alts} == {"CLEANER B", "CLEANER C"}
        alt_id, alt_name = alts[0]
        repo.swap_alternate(s, rid, l0, alt_id)
    with repo.session_scope() as s:
        swapped = next(ln for ln in repo.get_request(s, rid).lines if ln.id == l0)
        assert swapped.nomenclature == alt_name
        assert swapped.qty == 5  # qty preserved across swap
    with repo.session_scope() as s:
        repo.delete_line(s, rid, l1)
    with repo.session_scope() as s:
        assert [ln.id for ln in repo.get_request(s, rid).lines] == [l0]


def test_finalize_validation_and_success(session):
    with repo.session_scope() as s:
        ids = _build_catalog(s)
        rid = repo.create_draft(s)
    # empty request can't finalize
    with pytest.raises(repo.FinalizeError, match="empty"), repo.session_scope() as s:
        repo.finalize(s, rid)
    # a line with qty 0 blocks finalize
    with repo.session_scope() as s:
        repo.add_item(s, rid, ids["items"][0])
        line = repo.get_request(s, rid).lines[0]
        repo.set_qty(s, rid, line.id, 0)
    with pytest.raises(repo.FinalizeError, match="quantity"), repo.session_scope() as s:
        repo.finalize(s, rid)
    # fix qty → finalize produces a real overlay PDF
    with repo.session_scope() as s:
        line = repo.get_request(s, rid).lines[0]
        repo.set_qty(s, rid, line.id, 2)
    with repo.session_scope() as s:
        path = repo.finalize(s, rid)
        assert path is not None and path.exists()
        assert path.read_bytes().startswith(b"%PDF")
        assert repo.is_finalized(s, rid)


def test_history_list_filters(session):
    with repo.session_scope() as s:
        ids = _build_catalog(s)
        d = repo.create_draft(s)
        repo.update_header(s, d, requestor_name="DRAFTY", workcenter="WD")
        f = repo.create_draft(s)
        repo.update_header(s, f, requestor_name="FINALY", workcenter="WF")
        repo.add_item(s, f, ids["items"][0])
        repo.set_qty(s, f, repo.get_request(s, f).lines[0].id, 1)
    with repo.session_scope() as s:
        repo.finalize(s, f)
    with repo.session_scope() as s:
        assert len(repo.list_requests(s)) == 2
        assert [r.id for r in repo.list_requests(s, status_filter="draft")] == [d]
        assert [r.id for r in repo.list_requests(s, status_filter="final")] == [f]
        assert [r.requestor for r in repo.list_requests(s, q="FINALY")] == ["FINALY"]
        # search matches a line's snapshot too
        assert f in [r.id for r in repo.list_requests(s, q="CLEANER A")]
        row = next(r for r in repo.list_requests(s) if r.id == f)
        assert row.finalized and row.has_pdf and row.line_count == 1


def test_duplicate_reopen_delete(session):
    with repo.session_scope() as s:
        ids = _build_catalog(s)
        rid = repo.create_draft(s)
        repo.update_header(s, rid, requestor_name="ORIG")
        repo.add_item(s, rid, ids["items"][0])
        repo.set_qty(s, rid, repo.get_request(s, rid).lines[0].id, 3)
    with repo.session_scope() as s:
        repo.finalize(s, rid)
    with repo.session_scope() as s:
        dup = repo.duplicate_request(s, rid)
    with repo.session_scope() as s:
        orig, copy = repo.get_request(s, rid), repo.get_request(s, dup)
        assert copy.requestor_name == "ORIG"
        assert [ln.nomenclature for ln in copy.lines] == [ln.nomenclature for ln in orig.lines]
        assert copy.finalized_at is None  # the copy is a fresh draft
    with repo.session_scope() as s:
        repo.reopen_request(s, rid)
        assert not repo.is_finalized(s, rid)
    with repo.session_scope() as s:
        repo.delete_request(s, dup)
    with repo.session_scope() as s:
        assert dup not in [r.id for r in repo.list_requests(s)]


def test_print_request_without_cups_raises_cleanly(session, monkeypatch):
    from app.services import printer as printer_service
    monkeypatch.setattr(printer_service, "is_available", lambda: False)
    with repo.session_scope() as s:
        ids = _build_catalog(s)
        rid = repo.create_draft(s)
        repo.add_item(s, rid, ids["items"][0])
        repo.set_qty(s, rid, repo.get_request(s, rid).lines[0].id, 1)
    with pytest.raises(printer_service.PrintError), repo.session_scope() as s:
        repo.print_request(s, rid)
    # but it auto-finalized + rendered the PDF before trying to print
    with repo.session_scope() as s:
        assert repo.is_finalized(s, rid)
        assert repo.pdf_path(s, rid) is not None
