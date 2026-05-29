"""History screen for the Tk app.

Lists past requests with live search + status filter, and the same
per-request actions the web history offers: open, duplicate, delete, and
(for finalized requests) open the PDF or re-print it. Calls app.ui.repo
directly — no HTTP.
"""

from __future__ import annotations

import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk

from app.ui import repo

SEARCH_DEBOUNCE_MS = 200


def _open_path(path: str) -> None:
    """Open a file with the OS default handler (xdg-open / open)."""
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class HistoryScreen(ttk.Frame):
    def __init__(self, master, shell) -> None:
        super().__init__(master, padding=8)
        self.shell = shell
        self._rows: list[repo.RequestRow] = []
        self._search_after: str | None = None

        self._build_toolbar()
        self._build_table()
        self._build_actions()
        self.refresh()

    # ---- toolbar -------------------------------------------------------

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Search").pack(side="left")
        self.q_var = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=self.q_var, width=32)
        ent.pack(side="left", padx=(4, 12))
        ent.bind("<KeyRelease>", lambda _e: self._debounced_refresh())

        ttk.Label(bar, text="Status").pack(side="left")
        self.status_var = tk.StringVar(value="All")
        cb = ttk.Combobox(
            bar, textvariable=self.status_var, width=8, state="readonly",
            values=["All", "Draft", "Final"],
        )
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda _e: self.refresh())

        ttk.Button(bar, text="Refresh", command=self.refresh).pack(side="left", padx=4)
        ttk.Button(bar, text="+ New request", command=self.shell.new_request).pack(side="right")

    # ---- table ---------------------------------------------------------

    def _build_table(self) -> None:
        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True)
        cols = ("when", "requestor", "workcenter", "lpo", "status", "lines")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        for c, txt, w, anchor in [
            ("when", "Date / Time", 130, "w"),
            ("requestor", "Requestor", 160, "w"),
            ("workcenter", "Workcenter", 110, "w"),
            ("lpo", "LPO", 110, "w"),
            ("status", "Status", 70, "center"),
            ("lines", "Lines", 50, "center"),
        ]:
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor=anchor)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _e: self._open_selected())
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._sync_action_state())

    # ---- actions -------------------------------------------------------

    def _build_actions(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(6, 0))
        self.btn_open = ttk.Button(bar, text="Open", command=self._open_selected)
        self.btn_open.pack(side="left")
        self.btn_dup = ttk.Button(bar, text="Duplicate", command=self._duplicate_selected)
        self.btn_dup.pack(side="left", padx=4)
        self.btn_del = ttk.Button(bar, text="Delete", command=self._delete_selected)
        self.btn_del.pack(side="left")
        self.btn_pdf = ttk.Button(bar, text="Open PDF", command=self._open_pdf_selected)
        self.btn_pdf.pack(side="left", padx=4)
        self.btn_print = ttk.Button(bar, text="Print", command=self._print_selected)
        self.btn_print.pack(side="left")
        self.status = ttk.Label(bar, text="", anchor="e")
        self.status.pack(side="right", fill="x", expand=True)

    # ---- data ----------------------------------------------------------

    def _debounced_refresh(self) -> None:
        if self._search_after:
            self.after_cancel(self._search_after)
        self._search_after = self.after(SEARCH_DEBOUNCE_MS, self.refresh)

    def refresh(self) -> None:
        status_map = {"All": "", "Draft": "draft", "Final": "final"}
        try:
            with repo.session_scope() as s:
                self._rows = repo.list_requests(
                    s, q=self.q_var.get(), status_filter=status_map[self.status_var.get()]
                )
        except Exception as e:  # noqa: BLE001
            self._set_status(f"Load failed: {e}", error=True)
            return
        self.tree.delete(*self.tree.get_children())
        for r in self._rows:
            self.tree.insert(
                "", "end", iid=str(r.id),
                values=(r.when, r.requestor, r.workcenter, r.lpo, r.status, r.line_count),
            )
        self._set_status(f"{len(self._rows)} request(s)")
        self._sync_action_state()

    def _selected_row(self) -> repo.RequestRow | None:
        sel = self.tree.selection()
        if not sel:
            return None
        rid = int(sel[0])
        return next((r for r in self._rows if r.id == rid), None)

    def _sync_action_state(self) -> None:
        row = self._selected_row()
        has = row is not None
        finalized = bool(row and row.finalized)
        has_pdf = bool(row and row.has_pdf)
        for btn in (self.btn_open, self.btn_dup, self.btn_del):
            btn.configure(state="normal" if has else "disabled")
        # PDF / print only make sense once a PDF exists (finalized).
        self.btn_pdf.configure(state="normal" if has_pdf else "disabled")
        self.btn_print.configure(state="normal" if (has and finalized) else "disabled")

    # ---- handlers ------------------------------------------------------

    def _open_selected(self) -> None:
        row = self._selected_row()
        if row:
            self.shell.open_request(row.id)

    def _duplicate_selected(self) -> None:
        row = self._selected_row()
        if not row:
            return
        try:
            with repo.session_scope() as s:
                new_id = repo.duplicate_request(s, row.id)
        except Exception as e:  # noqa: BLE001
            self._set_status(f"Duplicate failed: {e}", error=True)
            return
        self.shell.open_request(new_id)

    def _delete_selected(self) -> None:
        row = self._selected_row()
        if not row:
            return
        if not messagebox.askyesno(
            "Delete request",
            f"Delete request #{row.id} ({row.requestor or 'no requestor'})? "
            "This cannot be undone.",
            parent=self,
        ):
            return
        try:
            with repo.session_scope() as s:
                repo.delete_request(s, row.id)
        except Exception as e:  # noqa: BLE001
            self._set_status(f"Delete failed: {e}", error=True)
            return
        self.refresh()

    def _open_pdf_selected(self) -> None:
        row = self._selected_row()
        if not row:
            return
        try:
            with repo.session_scope() as s:
                path = repo.pdf_path(s, row.id)
        except Exception as e:  # noqa: BLE001
            self._set_status(f"Lookup failed: {e}", error=True)
            return
        if not path:
            self._set_status("No PDF yet — finalize the request first.", error=True)
            return
        _open_path(path)
        self._set_status(f"Opened {path}")

    def _print_selected(self) -> None:
        row = self._selected_row()
        if not row:
            return
        try:
            with repo.session_scope() as s:
                job = repo.print_request(s, row.id)
        except Exception as e:  # noqa: BLE001
            self._set_status(f"Print failed: {e}", error=True)
            messagebox.showerror("Print failed", str(e), parent=self)
            return
        self._set_status(f"Sent to printer ({job}).")
        self.refresh()

    def _set_status(self, text: str, error: bool = False) -> None:
        self.status.configure(text=text, foreground="#b00020" if error else "")
