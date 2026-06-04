"""Manual GUI smoke driver for the native Tk app.

Drives the real widgets and handlers of every screen under a display and
asserts the database effects — catches wiring bugs that the data-layer
tests (tests/test_native_ui.py, which run under plain pytest) can't.

Needs tkinter + a display; use Xvfb in headless CI:

    xvfb-run -a python scripts/ui_smoke.py

Exits non-zero if any check fails.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

_DATA = tempfile.mkdtemp(prefix="hazreq-uismoke-")
os.environ.update(
    HAZREQ_DATA_DIR=_DATA,
    HAZREQ_DB_URL=f"sqlite:///{_DATA}/db.sqlite",
    HAZREQ_PDF_BACKEND="overlay",
    HAZREQ_OVERLAY_PDF_PATH=os.path.join(REPO_ROOT, "Hazmat Request Blank.pdf"),
)

import tkinter as tk  # noqa: E402
from tkinter import messagebox  # noqa: E402

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from app.ui import builder as buildermod  # noqa: E402
from app.ui import catalog as catmod  # noqa: E402
from app.ui import catalog_repo as cr  # noqa: E402
from app.ui import repo, seed  # noqa: E402
from app.ui import shell as shellmod  # noqa: E402

CHECKS: list[str] = []
FAILURES: list[str] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append(name)
    print("PASS" if cond else "FAIL", name)
    if not cond:
        FAILURES.append(name)


def fake_dialog(result):
    """A real auto-closing Toplevel whose .result is preset — stands in for
    a builder modal that the handler drives via wait_window()."""

    class _Fake(tk.Toplevel):
        def __init__(self, master, *args):
            super().__init__(master)
            self.result = result
            self.after(0, self.destroy)

    return _Fake


def show_dialog(result):
    """Stand-in for a catalog modal that the handler drives via .show()."""

    class _Fake:
        def __init__(self, *a, **k):
            pass

        def show(self):
            return result

    return _Fake


def main() -> int:
    cfg = Config(os.path.join(REPO_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", "app/migrations")
    cfg.set_main_option("sqlalchemy.url", os.environ["HAZREQ_DB_URL"])
    command.upgrade(cfg, "head")
    seed.seed_if_empty()

    # Neutralize popups so handlers don't block on user input.
    messagebox.showinfo = lambda *a, **k: None
    messagebox.showerror = lambda *a, **k: None
    messagebox.showwarning = lambda *a, **k: None
    messagebox.askyesno = lambda *a, **k: True

    app = shellmod.AppShell()

    def pump(n: int = 5) -> None:
        for _ in range(n):
            app.update_idletasks()
            app.update()

    pump()
    _drive_builder(app, pump)
    _drive_history(app, pump)
    _drive_catalog(app, pump)
    _drive_admin(app, pump)
    app.destroy()

    print(f"\n==== {len(CHECKS)} checks, {len(FAILURES)} failures ====")
    print("FAILURES:", FAILURES if FAILURES else "none")
    return 1 if FAILURES else 0


def _drive_builder(app, pump) -> None:
    app.new_request()
    pump()
    b = app._current
    rid = b.request_id

    b.hdr_vars["requestor_name"].set("DRIVER MCTEST")
    b._save_header_field("requestor_name")
    pump()
    with repo.session_scope() as s:
        check("builder.header_autosave", repo.get_request(s, rid).requestor_name == "DRIVER MCTEST")

    b._run_mrc_search("R-1")
    pump()
    check("builder.mrc_search_results", b.mrc_search.size() > 0)
    b.mrc_search.selection_set(0)
    b._load_mrc()
    pump()
    with repo.session_scope() as s:
        n = len(repo.get_request(s, rid).lines)
    check("builder.load_mrc_added_lines", n > 0)

    b._run_item_search("epoxy")
    pump()
    if b.item_search.size() > 0:
        b.item_search.selection_set(0)
        b._add_item()
        pump()
    with repo.session_scope() as s:
        check("builder.add_item", len(repo.get_request(s, rid).lines) == n + 1)

    with repo.session_scope() as s:
        first = sorted(repo.get_request(s, rid).lines, key=lambda ln: ln.sort_order)[0].id
        repo.set_qty(s, rid, first, 9)
    b.reload_lines()
    pump()
    check("builder.tree_shows_qty", b.tree.set(str(first), "qty") == "9")

    buildermod._ManualLineDialog = fake_dialog(("MAN", "MANUAL ROW", "555", 4))
    b._add_manual()
    pump()
    with repo.session_scope() as s:
        names = [ln.nomenclature for ln in repo.get_request(s, rid).lines]
    check("builder.add_manual", "MANUAL ROW" in names)

    with repo.session_scope() as s:
        lines = sorted(repo.get_request(s, rid).lines, key=lambda ln: ln.sort_order)
        target_id = next(ln.id for ln in lines if ln.hazmat_item_id)
        alts = [(a.id, a.nomenclature) for a in repo.alternates(s, target_id)]
    if alts:
        b.tree.selection_set(str(target_id))
        buildermod._SwapDialog = fake_dialog(alts[0][0])
        b._swap_selected()
        pump()
        with repo.session_scope() as s:
            swapped_name = next(
                ln.nomenclature for ln in repo.get_request(s, rid).lines if ln.id == target_id
            )
        check("builder.swap_alternate", swapped_name == alts[0][1])

    with repo.session_scope() as s:
        ids = [ln.id for ln in sorted(repo.get_request(s, rid).lines, key=lambda ln: ln.sort_order)]
    b.tree.selection_set(str(ids[0]))
    b._move("down")
    pump()
    with repo.session_scope() as s:
        order = [ln.id for ln in sorted(repo.get_request(s, rid).lines, key=lambda ln: ln.sort_order)]
    check("builder.move_line", order[0] == ids[1])

    b.tree.selection_set(str(ids[-1]))
    b._remove_selected()
    pump()
    with repo.session_scope() as s:
        remaining = [ln.id for ln in repo.get_request(s, rid).lines]
    check("builder.remove_line", ids[-1] not in remaining)

    app.show_history = lambda: None  # don't navigate away mid-test
    b.shell = app
    b._finalize()  # runs in a worker thread + after()
    for _ in range(40):
        pump(2)
        time.sleep(0.05)
    with repo.session_scope() as s:
        check("builder.finalize", repo.is_finalized(s, rid))
    app._finalized_rid = rid


def _drive_history(app, pump) -> None:
    app.show_history = shellmod.AppShell.show_history.__get__(app)
    app.show_history()
    pump()
    h = app._current
    rid = app._finalized_rid

    h.q_var.set("DRIVER")
    h.refresh()
    pump()
    check("history.search_filter", any("DRIVER" in (r.requestor or "") for r in h._rows))

    if h.tree.exists(str(rid)):
        h.tree.selection_set(str(rid))
        h._sync_action_state()
        pump()
        check("history.pdf_btn_enabled_for_finalized", str(h.btn_pdf["state"]) == "normal")

    with repo.session_scope() as s:
        before = len(repo.list_requests(s))
    h.tree.selection_set(str(rid))
    h._duplicate_selected()
    pump()
    with repo.session_scope() as s:
        check("history.duplicate", len(repo.list_requests(s)) == before + 1)

    dup_rid = app._current.request_id  # duplicate opened in the builder
    app.show_history()
    pump()
    h = app._current
    h.tree.selection_set(str(dup_rid))
    h._delete_selected()
    pump()
    with repo.session_scope() as s:
        check("history.delete", dup_rid not in [r.id for r in repo.list_requests(s)])


def _drive_catalog(app, pump) -> None:
    app.show_catalog()
    pump()
    c = app._current

    catmod._FormDialog = show_dialog({"code": "DRV-MIP", "title": "t", "notes": ""})
    c._new_mip()
    pump()
    with repo.session_scope() as s:
        check("catalog.create_mip", "DRV-MIP" in [m.code for m in cr.mip_tree(s)])
        mid = next(m.id for m in cr.mip_tree(s) if m.code == "DRV-MIP")

    c.refresh()
    pump()
    c.mip_tree.selection_set(f"mip:{mid}")
    # Drive the real integrated MRC editor: set fields, link an item, save.
    with repo.session_scope() as s:
        grease = repo.search_items(s, "grease")[0]
        pick_item, grease_sp = grease.id, grease.spmig_id
    ed = catmod._MrcEditor(c, mid)
    ed._vars["code"].set("DRV-MRC")
    ed._vars["periodicity"].set("M")
    ed._vars["description"].set("d")
    with repo.session_scope() as s:
        ed._put(cr.item_brief(s, pick_item))
    ed._save()
    pump()
    with repo.session_scope() as s:
        m = next(m for m in cr.mip_tree(s) if m.code == "DRV-MIP")
        check("catalog.create_mrc", "DRV-MRC" in [mr.code for mr in m.mrcs])
        mrc_id = next(mr.id for mr in m.mrcs if mr.code == "DRV-MRC")
        check("catalog.mrc_editor_links_item",
              pick_item in [li.id for li in cr.mrc_items(s, mrc_id)])

    # Side-panel add of an item from a *different* SPMIG → 2nd default kept.
    c.refresh()
    pump()
    c.mip_tree.selection_set(f"mrc:{mrc_id}")
    c._on_mip_select()
    pump()
    with repo.session_scope() as s:
        other_id = next(i.id for i in repo.search_items(s, "") if i.spmig_id != grease_sp)
    catmod._ItemPicker = show_dialog(other_id)
    c._add_link()
    pump()
    with repo.session_scope() as s:
        ids_now = [li.id for li in cr.mrc_items(s, mrc_id)]
        check("catalog.add_mrc_link", other_id in ids_now and pick_item in ids_now)

    # Adding another item from the SAME SPMIG as grease replaces it (one
    # default per SPMIG) rather than piling up a second line.
    with repo.session_scope() as s:
        grease_alt = next(i.id for i in repo.search_items(s, "")
                          if i.spmig_id == grease_sp and i.id != pick_item)
    catmod._ItemPicker = show_dialog(grease_alt)
    c._add_link()
    pump()
    with repo.session_scope() as s:
        ids_now = [li.id for li in cr.mrc_items(s, mrc_id)]
        check("catalog.default_per_spmig",
              grease_alt in ids_now and pick_item not in ids_now and other_id in ids_now)

    with repo.session_scope() as s:
        sp_with_items = next(sp.id for sp in cr.spmig_tree(s) if sp.items)
    nb = c.winfo_children()[0]
    nb.select(1)
    pump()
    c.sp_tree.selection_set(f"sp:{sp_with_items}")
    c._delete_spmig_node()
    pump()
    with repo.session_scope() as s:
        check("catalog.delete_guard_blocks", sp_with_items in [sp.id for sp in cr.spmig_tree(s)])
        cr.delete_mip(s, mid)  # cleanup (cascades MRC + link)


def _drive_admin(app, pump) -> None:
    from tkinter import filedialog

    from app.ui import catalog_repo as cr

    # ensure there's at least one audit row to show
    with repo.session_scope() as s:
        mid = cr.create_mip(s, "ADMIN-DRV-MIP", "x")

    app.show_admin()
    pump()
    a = app._current
    check("admin.health_populated", a._health_labels["backend"].cget("text") == "overlay")

    a.audit_q.set("ADMIN-DRV-MIP")
    a.refresh_audit()
    pump()
    check("admin.audit_shows_mutation", len(a.audit.get_children()) >= 1)

    out = os.path.join(_DATA, "drv-backup.zip")
    filedialog.asksaveasfilename = lambda *args, **kw: out
    a._backup()
    pump()
    check("admin.backup_writes_file", os.path.exists(out) and os.path.getsize(out) > 0)

    with repo.session_scope() as s:
        cr.delete_mip(s, mid)


if __name__ == "__main__":
    sys.exit(main())
