"""Tk new-request builder (prototype).

A native window that reproduces the web app's search-centric request
builder, calling app.ui.repo directly — no HTTP, no webview. Run it on a
machine with a display (the Pi 400) via scripts/tk_prototype.py.

Layout:

    +--------------------------------------------------------------+
    | Requestor  Workcenter  LPO  Location  Date/Time   (header)   |
    +---------------------------+----------------------------------+
    | [MRC] [Items]   search    |  Request lines (Treeview)        |
    |  results list             |  SPMIG | Nomenclature | NIIN | Qty|
    |  [Load MRC ->]/[Add ->]   |  [Manual] [Swap] [Up][Dn][Remove]|
    +---------------------------+----------------------------------+
    | status                                      [Finalize -> PDF]|
    +--------------------------------------------------------------+
"""

from __future__ import annotations

import contextlib
import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from app.ui import repo

SEARCH_DEBOUNCE_MS = 150


class BuilderApp(ttk.Frame):
    def __init__(self, master, request_id: int, shell=None) -> None:
        super().__init__(master, padding=8)
        self.request_id = request_id
        self.shell = shell

        # id maps for the result lists (index -> db id)
        self._mrc_ids: list[int] = []
        self._item_ids: list[int] = []
        self._line_ids: list[int] = []
        self._mrc_after: str | None = None
        self._item_after: str | None = None

        # Finalized requests open read-only (mirrors the web view/edit split).
        with repo.session_scope() as s:
            self.read_only = repo.is_finalized(s, request_id)

        self._editable_widgets: list[tk.Widget] = []
        self._build_header()
        self._build_body()
        self._build_footer()
        self._load_header()
        self.reload_lines()
        if self.read_only:
            self._apply_read_only()

    # ---- header --------------------------------------------------------

    def _build_header(self) -> None:
        hdr = ttk.LabelFrame(self, text="Request", padding=8)
        hdr.pack(fill="x", pady=(0, 8))

        self.hdr_vars: dict[str, tk.StringVar] = {}
        _BASE_PRESETS = ["Yokose", "LCU Main Base"]
        fields: list[tuple[str, str, str]] = [
            ("requestor_name",      "Requestor", "entry"),
            ("workcenter",          "Workcenter", "entry"),
            ("lpo",                 "LPO",        "entry"),
            ("hazmat_location",     "Location",   "entry"),
            ("base_location",       "Yokose/LCU", "combo"),
            ("datetime_of_request", "Date/Time",  "entry"),
        ]
        for col, (key, label, kind) in enumerate(fields):
            ttk.Label(hdr, text=label).grid(row=0, column=col, sticky="w", padx=4)
            var = tk.StringVar()
            if kind == "combo":
                ent = ttk.Combobox(hdr, textvariable=var, width=14, values=_BASE_PRESETS)
                ent.bind("<<ComboboxSelected>>", lambda _e, k=key: self._save_header_field(k))
            else:
                ent = ttk.Entry(hdr, textvariable=var, width=14)
            ent.grid(row=1, column=col, sticky="ew", padx=4)
            # Autosave per field on focus-out — mirrors the web PATCH-on-blur.
            ent.bind("<FocusOut>", lambda _e, k=key: self._save_header_field(k))
            self.hdr_vars[key] = var
            self._editable_widgets.append(ent)
            hdr.columnconfigure(col, weight=1)
        self.hdr_vars["datetime_of_request"].set(
            datetime.now().strftime("%Y-%m-%d %H:%M")
        )

    def _load_header(self) -> None:
        """Populate the header entries from the persisted request, so an
        existing draft (or a reopened request) shows its saved values."""
        with repo.session_scope() as s:
            r = repo.get_request(s, self.request_id)
            vals = {
                "requestor_name": r.requestor_name or "",
                "workcenter": r.workcenter or "",
                "lpo": r.lpo or "",
                "hazmat_location": r.hazmat_location or "",
                "base_location": r.base_location or "",
                "datetime_of_request": (
                    r.datetime_of_request.strftime("%Y-%m-%d %H:%M")
                    if r.datetime_of_request
                    else datetime.now().strftime("%Y-%m-%d %H:%M")
                ),
            }
        for key, val in vals.items():
            self.hdr_vars[key].set(val)

    def _save_header_field(self, key: str) -> None:
        val = self.hdr_vars[key].get()
        try:
            with repo.session_scope() as s:
                repo.update_header(s, self.request_id, **{key: val})
        except Exception as e:  # noqa: BLE001
            self.set_status(f"Save failed: {e}", error=True)

    # ---- body ----------------------------------------------------------

    def _build_body(self) -> None:
        pane = ttk.PanedWindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True)

        # Left: search notebook
        left = ttk.Frame(pane)
        pane.add(left, weight=1)
        nb = ttk.Notebook(left)
        nb.pack(fill="both", expand=True)

        self.mrc_search = self._make_search_tab(
            nb, "MIP / MRC", "Load MRC →", self._on_mrc_search, self._load_mrc
        )
        self.item_search = self._make_search_tab(
            nb, "Items", "Add item →", self._on_item_search, self._add_item
        )
        # Seed both result lists with the full catalog up-front. Without this
        # the lists stay empty until the first keystroke (they only refill on
        # <KeyRelease>), so they appeared blank until you typed then deleted.
        self._run_mrc_search("")
        self._run_item_search("")

        # Right: lines
        right = ttk.LabelFrame(pane, text="Request lines", padding=6)
        pane.add(right, weight=2)
        cols = ("spmig", "nomenclature", "niin", "qty")
        self.tree = ttk.Treeview(right, columns=cols, show="headings", selectmode="browse")
        for c, txt, w in [
            ("spmig", "SPMIG", 80),
            ("nomenclature", "Nomenclature", 320),
            ("niin", "NIIN", 90),
            ("qty", "Qty", 50),
        ]:
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor="w" if c != "qty" else "center")
        self.tree.pack(fill="both", expand=True)
        # Double-click the Qty cell to edit inline.
        self.tree.bind("<Double-1>", self._begin_qty_edit)

        btns = ttk.Frame(right)
        btns.pack(fill="x", pady=(6, 0))
        for text, cmd in [
            ("Manual line…", self._add_manual),
            ("Swap…", self._swap_selected),
            ("Move ↑", lambda: self._move("up")),
            ("Move ↓", lambda: self._move("down")),
            ("Remove", self._remove_selected),
        ]:
            b = ttk.Button(btns, text=text, command=cmd)
            b.pack(side="left", padx=(0, 4))
            self._editable_widgets.append(b)

    def _make_search_tab(self, nb, tab_title, action_label, on_search, on_action):
        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text=tab_title)
        var = tk.StringVar()
        ent = ttk.Entry(tab, textvariable=var)
        ent.pack(fill="x")
        ent.bind("<KeyRelease>", lambda _e: on_search(var.get()))
        self._editable_widgets.append(ent)
        listbox = tk.Listbox(tab, activestyle="dotbox")
        listbox.pack(fill="both", expand=True, pady=6)
        listbox.bind("<Double-1>", lambda _e: on_action())
        action_btn = ttk.Button(tab, text=action_label, command=on_action)
        action_btn.pack(anchor="e")
        self._editable_widgets.append(action_btn)
        return listbox

    # ---- search handlers (debounced) -----------------------------------

    def _on_mrc_search(self, q: str) -> None:
        if self._mrc_after:
            self.after_cancel(self._mrc_after)
        self._mrc_after = self.after(SEARCH_DEBOUNCE_MS, lambda: self._run_mrc_search(q))

    def _run_mrc_search(self, q: str) -> None:
        try:
            with repo.session_scope() as s:
                rows = repo.search_mrc(s, q)
                data = [(m.id, f"{m.mip.code} / {m.code}  —  {m.description or ''}") for m in rows]
        except Exception as e:  # noqa: BLE001
            self.set_status(f"Search failed: {e}", error=True)
            return
        self._mrc_ids = [d[0] for d in data]
        self.mrc_search.delete(0, "end")
        for _id, label in data:
            self.mrc_search.insert("end", label)

    def _on_item_search(self, q: str) -> None:
        if self._item_after:
            self.after_cancel(self._item_after)
        self._item_after = self.after(SEARCH_DEBOUNCE_MS, lambda: self._run_item_search(q))

    def _run_item_search(self, q: str) -> None:
        try:
            with repo.session_scope() as s:
                rows = repo.search_items(s, q)
                data = [
                    (i.id, f"{i.spmig.code if i.spmig else '?':>6}  ·  {i.nomenclature}  ({i.niin or '-'})")
                    for i in rows
                ]
        except Exception as e:  # noqa: BLE001
            self.set_status(f"Search failed: {e}", error=True)
            return
        self._item_ids = [d[0] for d in data]
        self.item_search.delete(0, "end")
        for _id, label in data:
            self.item_search.insert("end", label)

    # ---- catalog -> lines ----------------------------------------------

    def _load_mrc(self) -> None:
        sel = self.mrc_search.curselection()
        if not sel:
            return
        mrc_id = self._mrc_ids[sel[0]]
        try:
            with repo.session_scope() as s:
                added, skipped = repo.load_mrc(s, self.request_id, mrc_id)
        except Exception as e:  # noqa: BLE001
            self.set_status(f"Load failed: {e}", error=True)
            return
        msg = f"Loaded {added} item(s)."
        if skipped:
            msg += f" Skipped {len(skipped)} already on the request."
        self.set_status(msg)
        self.reload_lines()

    def _add_item(self) -> None:
        sel = self.item_search.curselection()
        if not sel:
            return
        item_id = self._item_ids[sel[0]]
        try:
            with repo.session_scope() as s:
                added = repo.add_item(s, self.request_id, item_id)
        except Exception as e:  # noqa: BLE001
            self.set_status(f"Add failed: {e}", error=True)
            return
        self.set_status("Item added." if added else "Item already on the request.")
        self.reload_lines()

    def _add_manual(self) -> None:
        dlg = _ManualLineDialog(self)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        spmig, nomen, niin, qty = dlg.result
        try:
            with repo.session_scope() as s:
                repo.add_manual_line(s, self.request_id, spmig, nomen, niin, qty)
        except Exception as e:  # noqa: BLE001
            self.set_status(f"Add failed: {e}", error=True)
            return
        self.set_status("Manual line added.")
        self.reload_lines()

    # ---- line operations -----------------------------------------------

    def _selected_line_id(self) -> int | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return int(sel[0])

    def _remove_selected(self) -> None:
        line_id = self._selected_line_id()
        if line_id is None:
            return
        try:
            with repo.session_scope() as s:
                repo.delete_line(s, self.request_id, line_id)
        except Exception as e:  # noqa: BLE001
            self.set_status(f"Remove failed: {e}", error=True)
            return
        self.reload_lines()

    def _move(self, direction: str) -> None:
        line_id = self._selected_line_id()
        if line_id is None:
            return
        try:
            with repo.session_scope() as s:
                repo.move_line(s, self.request_id, line_id, direction)
        except Exception as e:  # noqa: BLE001
            self.set_status(f"Move failed: {e}", error=True)
            return
        self.reload_lines(reselect=line_id)

    def _swap_selected(self) -> None:
        line_id = self._selected_line_id()
        if line_id is None:
            return
        try:
            with repo.session_scope() as s:
                alts = repo.alternates(s, line_id)
                options = [(a.id, a.nomenclature, a.niin) for a in alts]
        except Exception as e:  # noqa: BLE001
            self.set_status(f"Lookup failed: {e}", error=True)
            return
        if not options:
            self.set_status("No alternates in the same SPMIG.")
            return
        dlg = _SwapDialog(self, options)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        try:
            with repo.session_scope() as s:
                repo.swap_alternate(s, self.request_id, line_id, dlg.result)
        except Exception as e:  # noqa: BLE001
            self.set_status(f"Swap failed: {e}", error=True)
            return
        self.set_status("Line swapped.")
        self.reload_lines(reselect=line_id)

    # ---- inline qty editing --------------------------------------------

    def _begin_qty_edit(self, event: tk.Event) -> None:
        if self.read_only:
            return
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#4":  # qty is the 4th column
            return
        row = self.tree.identify_row(event.y)
        if not row:
            return
        x, y, w, h = self.tree.bbox(row, "#4")
        cur = self.tree.set(row, "qty")
        edit = ttk.Entry(self.tree, justify="center")
        edit.insert(0, cur)
        edit.select_range(0, "end")
        edit.place(x=x, y=y, width=w, height=h)
        edit.focus_set()

        def commit(_e=None):
            raw = edit.get().strip()
            edit.destroy()
            try:
                qty = int(raw) if raw else None
            except ValueError:
                self.set_status("Qty must be a whole number.", error=True)
                return
            try:
                with repo.session_scope() as s:
                    repo.set_qty(s, self.request_id, int(row), qty)
            except Exception as e:  # noqa: BLE001
                self.set_status(f"Update failed: {e}", error=True)
                return
            self.tree.set(row, "qty", "" if qty is None else str(qty))

        edit.bind("<Return>", commit)
        edit.bind("<FocusOut>", commit)
        edit.bind("<Escape>", lambda _e: edit.destroy())

    # ---- rendering -----------------------------------------------------

    def reload_lines(self, reselect: int | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        with repo.session_scope() as s:
            r = repo.get_request(s, self.request_id)
            rows = sorted(r.lines, key=lambda ln: ln.sort_order)
            data = [
                (ln.id, ln.spmig_code or "", ln.nomenclature or "", ln.niin or "",
                 "" if ln.qty is None else str(ln.qty))
                for ln in rows
            ]
        for line_id, spmig, nomen, niin, qty in data:
            self.tree.insert("", "end", iid=str(line_id), values=(spmig, nomen, niin, qty))
        if reselect is not None and self.tree.exists(str(reselect)):
            self.tree.selection_set(str(reselect))
        self._line_count = len(data)

    # ---- footer / finalize ---------------------------------------------

    def _build_footer(self) -> None:
        foot = ttk.Frame(self, padding=(0, 8, 0, 0))
        foot.pack(fill="x")
        self.status = ttk.Label(foot, text="Ready.", anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        # Draft controls (right side). Read-only requests get a different
        # set, built in _apply_read_only.
        self.finalize_btn = ttk.Button(foot, text="Finalize → PDF", command=self._finalize)
        self.finalize_btn.pack(side="right")
        self._footer = foot

    def set_status(self, text: str, error: bool = False) -> None:
        self.status.configure(text=text, foreground="#b00020" if error else "")

    # ---- read-only (finalized) mode ------------------------------------

    def _apply_read_only(self) -> None:
        """Disable all editing affordances and swap the footer to view
        actions: reopen, open PDF, print. Mirrors the web view/edit split."""
        for w in self._editable_widgets:
            with contextlib.suppress(tk.TclError):
                w.configure(state="disabled")
        self.finalize_btn.destroy()
        ttk.Button(self._footer, text="Print", command=self._print).pack(side="right")
        ttk.Button(self._footer, text="Open PDF", command=self._open_pdf).pack(
            side="right", padx=4
        )
        ttk.Button(
            self._footer, text="Reopen for editing", command=self._reopen
        ).pack(side="right")
        self.set_status("Finalized — read-only. Reopen to edit.")

    def _reopen(self) -> None:
        try:
            with repo.session_scope() as s:
                repo.reopen_request(s, self.request_id)
        except Exception as e:  # noqa: BLE001
            self.set_status(f"Reopen failed: {e}", error=True)
            return
        # Rebuild the screen in editable mode via the shell, if present.
        if self.shell is not None:
            self.shell.open_request(self.request_id)
        else:
            self.read_only = False
            self.set_status("Reopened for editing — reload the window to edit.")

    def _open_pdf(self) -> None:
        from app.ui.history import _open_path

        try:
            with repo.session_scope() as s:
                path = repo.pdf_path(s, self.request_id)
        except Exception as e:  # noqa: BLE001
            self.set_status(f"Lookup failed: {e}", error=True)
            return
        if not path:
            self.set_status("No PDF on file.", error=True)
            return
        _open_path(path)
        self.set_status(f"Opened {path}")

    def _print(self) -> None:
        try:
            with repo.session_scope() as s:
                job = repo.print_request(s, self.request_id)
        except Exception as e:  # noqa: BLE001
            self.set_status(f"Print failed: {e}", error=True)
            messagebox.showerror("Print failed", str(e), parent=self)
            return
        self.set_status(f"Sent to printer ({job}).")

    def _finalize(self) -> None:
        # PDF render runs off the UI thread so the window stays responsive.
        # CRITICAL: Tkinter is NOT thread-safe — calling ANY Tk method
        # (including .after) from the worker corrupts the X11/XCB connection
        # and aborts the process ("xcb_xlib_unknown_seq_number" assertion).
        # So the worker only ever touches a thread-safe queue; every Tk call
        # stays on the main thread, which polls the queue via .after().
        self.finalize_btn.configure(state="disabled")
        self.set_status("Generating PDF…")

        result_q: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

        def work():
            try:
                with repo.session_scope() as s:
                    path = repo.finalize(s, self.request_id)
                result_q.put(("ok", path))
            except Exception as e:  # noqa: BLE001
                result_q.put(("err", str(e)))

        threading.Thread(target=work, daemon=True).start()
        self._poll_finalize(result_q)

    def _poll_finalize(self, result_q: "queue.Queue[tuple[str, object]]") -> None:
        """Main-thread poll of the finalize worker's result queue."""
        try:
            kind, payload = result_q.get_nowait()
        except queue.Empty:
            self.after(50, lambda: self._poll_finalize(result_q))
            return
        if kind == "ok":
            self._finalize_done(payload)
        else:
            self._finalize_failed(str(payload))

    def _finalize_done(self, path) -> None:
        self.finalize_btn.configure(state="normal")
        self.set_status(f"Finalized. PDF: {path}")
        if self.shell is not None:
            if messagebox.askyesno(
                "Finalized",
                f"PDF written to:\n{path}\n\nOpen it now?",
            ):
                from app.ui.history import _open_path

                if path:
                    _open_path(str(path))
            self.shell.show_history()
        else:
            messagebox.showinfo("Finalized", f"PDF written to:\n{path}")

    def _finalize_failed(self, msg: str) -> None:
        self.finalize_btn.configure(state="normal")
        self.set_status(f"Finalize failed: {msg}", error=True)
        messagebox.showerror("Finalize failed", msg)


class _ManualLineDialog(tk.Toplevel):
    def __init__(self, master) -> None:
        super().__init__(master)
        self.title("Add manual line")
        self.result: tuple[str, str, str, int] | None = None
        self.transient(master)
        self.resizable(False, False)

        self.vars = {k: tk.StringVar() for k in ("spmig", "nomenclature", "niin", "qty")}
        self.vars["qty"].set("1")
        for row, (key, label) in enumerate(
            [("spmig", "SPMIG"), ("nomenclature", "Nomenclature"), ("niin", "NIIN"), ("qty", "Qty")]
        ):
            ttk.Label(self, text=label).grid(row=row, column=0, sticky="e", padx=6, pady=4)
            ttk.Entry(self, textvariable=self.vars[key], width=36).grid(
                row=row, column=1, padx=6, pady=4
            )
        btns = ttk.Frame(self)
        btns.grid(row=4, column=0, columnspan=2, pady=8)
        ttk.Button(btns, text="Add", command=self._ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        self.grab_set()

    def _ok(self) -> None:
        try:
            qty = int(self.vars["qty"].get() or "1")
        except ValueError:
            messagebox.showerror("Invalid qty", "Qty must be a whole number.", parent=self)
            return
        self.result = (
            self.vars["spmig"].get(),
            self.vars["nomenclature"].get(),
            self.vars["niin"].get(),
            qty,
        )
        self.destroy()


class _SwapDialog(tk.Toplevel):
    def __init__(self, master, options: list[tuple[int, str, str | None]]) -> None:
        super().__init__(master)
        self.title("Swap to alternate")
        self.result: int | None = None
        self._ids = [o[0] for o in options]
        self.transient(master)

        ttk.Label(self, text="Alternates in the same SPMIG:").pack(anchor="w", padx=8, pady=(8, 2))
        self.listbox = tk.Listbox(self, width=50, height=min(10, len(options)))
        for _id, nomen, niin in options:
            self.listbox.insert("end", f"{nomen}  ({niin or '-'})")
        self.listbox.pack(fill="both", expand=True, padx=8)
        self.listbox.selection_set(0)
        self.listbox.bind("<Double-1>", lambda _e: self._ok())
        btns = ttk.Frame(self)
        btns.pack(pady=8)
        ttk.Button(btns, text="Swap", command=self._ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        self.grab_set()

    def _ok(self) -> None:
        sel = self.listbox.curselection()
        if sel:
            self.result = self._ids[sel[0]]
        self.destroy()


def run(request_id: int) -> None:
    """Open just the builder on one request (no shell) — handy for testing
    a single screen in isolation."""
    root = tk.Tk()
    root.title("hazreq — request builder")
    root.geometry("1100x720")
    with contextlib.suppress(tk.TclError):
        ttk.Style().theme_use("clam")  # cleaner than the default motif look
    BuilderApp(root, request_id).pack(fill="both", expand=True)
    root.mainloop()
