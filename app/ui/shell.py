"""Application shell for the Tk app.

A single top-level window with a persistent nav bar and a swappable
content area. Owns navigation between screens (New Request / History) and
constructs each screen against the in-process repo. Adding a screen later
(Catalog, Admin) is a matter of another nav button + a content factory.
"""

from __future__ import annotations

import contextlib
import tkinter as tk
from tkinter import ttk

from app.ui import repo
from app.ui.admin import AdminScreen
from app.ui.builder import BuilderApp
from app.ui.catalog import CatalogScreen
from app.ui.history import HistoryScreen


class AppShell(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("hazreq")
        self.geometry("1100x740")
        with contextlib.suppress(tk.TclError):
            ttk.Style().theme_use("clam")

        self._nav = ttk.Frame(self, padding=(8, 6))
        self._nav.pack(fill="x")
        ttk.Button(self._nav, text="＋ New request", command=self.new_request).pack(side="left")
        ttk.Button(self._nav, text="History", command=self.show_history).pack(side="left", padx=6)
        ttk.Button(self._nav, text="Catalog", command=self.show_catalog).pack(side="left")
        ttk.Button(self._nav, text="Admin", command=self.show_admin).pack(side="left", padx=6)
        self._crumb = ttk.Label(self._nav, text="", anchor="e")
        self._crumb.pack(side="right", fill="x", expand=True)

        ttk.Separator(self, orient="horizontal").pack(fill="x")

        self._content = ttk.Frame(self)
        self._content.pack(fill="both", expand=True)
        self._current: tk.Widget | None = None
        self._current_key: str | None = None
        # Long-lived singleton screens, kept alive and re-shown rather than
        # rebuilt on every visit. Re-showing just calls refresh() (a cheap DB
        # read) instead of tearing down and reconstructing the whole widget
        # tree, which is what made navigation feel sluggish.
        self._cache: dict[str, tk.Widget] = {}

        self.show_history()

    # ---- content swapping ----------------------------------------------

    def _leave_current(self) -> None:
        """Hide the current page. Cached singletons are only unmapped;
        transient pages (request builders) are destroyed to free them."""
        if self._current is None:
            return
        self._current.pack_forget()
        if self._current_key not in self._cache:
            self._current.destroy()

    def _show_singleton(self, key: str, factory, crumb: str) -> None:
        """Show a reusable screen. No-op if it's already the active page so
        clicking the nav button you're already on does nothing."""
        if key == self._current_key:
            return
        self._leave_current()
        page = self._cache.get(key)
        if page is None:
            page = factory(self._content)
            self._cache[key] = page
        else:
            page.refresh()  # already built — just pull fresh data
        page.pack(fill="both", expand=True)
        self._current, self._current_key = page, key
        self._crumb.configure(text=crumb)

    def _show_transient(self, key: str, factory, crumb: str) -> None:
        """Show a one-off screen (always rebuilt; never cached)."""
        self._leave_current()
        page = factory(self._content)
        page.pack(fill="both", expand=True)
        self._current, self._current_key = page, key
        self._crumb.configure(text=crumb)

    def show_history(self) -> None:
        self._show_singleton("history", lambda p: HistoryScreen(p, self), "History")

    def show_catalog(self) -> None:
        self._show_singleton("catalog", lambda p: CatalogScreen(p, self), "Catalog")

    def show_admin(self) -> None:
        self._show_singleton("admin", lambda p: AdminScreen(p, self), "Admin")

    def open_request(self, request_id: int) -> None:
        # Transient and always rebuilt: reopening the same id (e.g. after
        # un-finalizing) must reflect the new state, so no same-key guard.
        self._show_transient(
            f"request:{request_id}",
            lambda parent: BuilderApp(parent, request_id, shell=self),
            f"Request #{request_id}",
        )

    def new_request(self) -> None:
        with repo.session_scope() as s:
            request_id = repo.create_draft(s)
        self.open_request(request_id)


def run() -> None:
    AppShell().mainloop()
