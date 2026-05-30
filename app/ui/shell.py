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

        self.show_history()

    # ---- content swapping ----------------------------------------------

    def _swap(self, factory, crumb: str) -> None:
        if self._current is not None:
            self._current.destroy()
        self._current = factory(self._content)
        self._current.pack(fill="both", expand=True)
        self._crumb.configure(text=crumb)

    def show_history(self) -> None:
        self._swap(lambda parent: HistoryScreen(parent, self), "History")

    def show_catalog(self) -> None:
        self._swap(lambda parent: CatalogScreen(parent, self), "Catalog")

    def show_admin(self) -> None:
        self._swap(lambda parent: AdminScreen(parent, self), "Admin")

    def open_request(self, request_id: int) -> None:
        self._swap(
            lambda parent: BuilderApp(parent, request_id, shell=self),
            f"Request #{request_id}",
        )

    def new_request(self) -> None:
        with repo.session_scope() as s:
            request_id = repo.create_draft(s)
        self.open_request(request_id)


def run() -> None:
    AppShell().mainloop()
