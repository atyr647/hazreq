"""Catalog management screen for the Tk app.

Two tabs:
  • MIPs / MRCs — tree of MIP → MRC, with per-MRC item links (add / remove
    / reorder). This is what feeds the request builder's bulk-load.
  • SPMIGs / Items — tree of SPMIG → hazmat item.

CRUD goes through app.ui.catalog_repo; deletion guards surface as dialogs.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from app.ui import catalog_repo as cat
from app.ui import repo


class CatalogScreen(ttk.Frame):
    def __init__(self, master, shell=None) -> None:
        super().__init__(master, padding=8)
        self.shell = shell
        self._mrc_parent: dict[int, int] = {}  # mrc_id -> mip_id

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        self._mip_tab = ttk.Frame(nb, padding=6)
        self._spmig_tab = ttk.Frame(nb, padding=6)
        nb.add(self._mip_tab, text="MIPs / MRCs")
        nb.add(self._spmig_tab, text="SPMIGs / Items")

        self.status = ttk.Label(self, text="", anchor="w")
        self.status.pack(fill="x", pady=(4, 0))

        self._build_mip_tab()
        self._build_spmig_tab()
        self.refresh()

    def _set_status(self, text: str, error: bool = False) -> None:
        self.status.configure(text=text, foreground="#b00020" if error else "")

    # ===== MIP / MRC tab ================================================

    def _build_mip_tab(self) -> None:
        pane = ttk.PanedWindow(self._mip_tab, orient="horizontal")
        pane.pack(fill="both", expand=True)

        left = ttk.Frame(pane)
        pane.add(left, weight=3)
        self.mip_tree = ttk.Treeview(left, columns=("info", "items"), show="tree headings")
        self.mip_tree.heading("#0", text="MIP / MRC")
        self.mip_tree.heading("info", text="Title / Periodicity")
        self.mip_tree.heading("items", text="Items")
        self.mip_tree.column("#0", width=220)
        self.mip_tree.column("info", width=240)
        self.mip_tree.column("items", width=50, anchor="center")
        self.mip_tree.pack(fill="both", expand=True)
        self.mip_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_mip_select())

        b = ttk.Frame(left)
        b.pack(fill="x", pady=(6, 0))
        ttk.Button(b, text="New MIP", command=self._new_mip).pack(side="left")
        ttk.Button(b, text="New MRC", command=self._new_mrc).pack(side="left", padx=4)
        ttk.Button(b, text="Edit", command=self._edit_mip_node).pack(side="left")
        ttk.Button(b, text="Delete", command=self._delete_mip_node).pack(side="left", padx=4)

        # Right: items linked to the selected MRC
        right = ttk.LabelFrame(pane, text="Items on selected MRC", padding=6)
        pane.add(right, weight=2)
        self.link_tree = ttk.Treeview(
            right, columns=("spmig", "niin"), show="tree headings", selectmode="browse"
        )
        self.link_tree.heading("#0", text="Nomenclature")
        self.link_tree.heading("spmig", text="SPMIG")
        self.link_tree.heading("niin", text="NIIN")
        self.link_tree.column("#0", width=220)
        self.link_tree.column("spmig", width=70)
        self.link_tree.column("niin", width=90)
        self.link_tree.pack(fill="both", expand=True)
        lb = ttk.Frame(right)
        lb.pack(fill="x", pady=(6, 0))
        self._link_btns = [
            ttk.Button(lb, text="Add item…", command=self._add_link),
            ttk.Button(lb, text="Remove", command=self._remove_link),
            ttk.Button(lb, text="↑", width=3, command=lambda: self._move_link("up")),
            ttk.Button(lb, text="↓", width=3, command=lambda: self._move_link("down")),
        ]
        for btn in self._link_btns:
            btn.pack(side="left", padx=(0, 4))

    def _selected_mip_node(self) -> tuple[str, int] | None:
        sel = self.mip_tree.selection()
        if not sel:
            return None
        kind, _id = sel[0].split(":")
        return kind, int(_id)

    def _current_mrc_id(self) -> int | None:
        node = self._selected_mip_node()
        return node[1] if node and node[0] == "mrc" else None

    def _on_mip_select(self) -> None:
        mrc_id = self._current_mrc_id()
        state = "normal" if mrc_id else "disabled"
        for btn in self._link_btns:
            btn.configure(state=state)
        self._reload_links(mrc_id)

    def _reload_links(self, mrc_id: int | None) -> None:
        self.link_tree.delete(*self.link_tree.get_children())
        if mrc_id is None:
            return
        with repo.session_scope() as s:
            rows = cat.mrc_items(s, mrc_id)
        for li in rows:
            self.link_tree.insert(
                "", "end", iid=str(li.id), text=li.nomenclature,
                values=(li.spmig_code, li.niin),
            )

    # ===== SPMIG / item tab =============================================

    def _build_spmig_tab(self) -> None:
        self.sp_tree = ttk.Treeview(
            self._spmig_tab, columns=("niin", "uoi"), show="tree headings"
        )
        self.sp_tree.heading("#0", text="SPMIG / Item")
        self.sp_tree.heading("niin", text="NIIN")
        self.sp_tree.heading("uoi", text="U/I")
        self.sp_tree.column("#0", width=380)
        self.sp_tree.column("niin", width=100)
        self.sp_tree.column("uoi", width=60, anchor="center")
        self.sp_tree.pack(fill="both", expand=True)

        b = ttk.Frame(self._spmig_tab)
        b.pack(fill="x", pady=(6, 0))
        ttk.Button(b, text="New SPMIG", command=self._new_spmig).pack(side="left")
        ttk.Button(b, text="New item", command=self._new_item).pack(side="left", padx=4)
        ttk.Button(b, text="Edit", command=self._edit_spmig_node).pack(side="left")
        ttk.Button(b, text="Delete", command=self._delete_spmig_node).pack(side="left", padx=4)

    def _selected_spmig_node(self) -> tuple[str, int] | None:
        sel = self.sp_tree.selection()
        if not sel:
            return None
        kind, _id = sel[0].split(":")
        return kind, int(_id)

    # ===== refresh ======================================================

    def refresh(self) -> None:
        try:
            with repo.session_scope() as s:
                mips = cat.mip_tree(s)
                spmigs = cat.spmig_tree(s)
        except Exception as e:  # noqa: BLE001
            self._set_status(f"Load failed: {e}", error=True)
            return

        self.mip_tree.delete(*self.mip_tree.get_children())
        self._mrc_parent.clear()
        for m in mips:
            mip_iid = f"mip:{m.id}"
            self.mip_tree.insert("", "end", iid=mip_iid, text=m.code, values=(m.title, ""), open=True)
            for mr in m.mrcs:
                self._mrc_parent[mr.id] = m.id
                self.mip_tree.insert(
                    mip_iid, "end", iid=f"mrc:{mr.id}", text=f"  {mr.code}",
                    values=(mr.periodicity or mr.description, mr.item_count),
                )

        self.sp_tree.delete(*self.sp_tree.get_children())
        for sp in spmigs:
            sp_iid = f"sp:{sp.id}"
            label = f"{sp.code}  —  {sp.description}" if sp.description else sp.code
            self.sp_tree.insert("", "end", iid=sp_iid, text=label, values=("", ""), open=False)
            for it in sp.items:
                self.sp_tree.insert(
                    sp_iid, "end", iid=f"item:{it.id}", text=f"  {it.nomenclature}",
                    values=(it.niin, it.unit_of_issue),
                )
        self._on_mip_select()
        self._set_status(f"{len(mips)} MIP(s), {len(spmigs)} SPMIG(s)")

    # ===== MIP / MRC actions ============================================

    def _new_mip(self) -> None:
        vals = _FormDialog(self, "New MIP", [("code", "Code", ""), ("title", "Title", ""),
                                             ("notes", "Notes", "")]).show()
        if vals is None:
            return
        self._run(lambda s: cat.create_mip(s, vals["code"], vals["title"], vals["notes"]),
                  "MIP created.")

    def _new_mrc(self) -> None:
        node = self._selected_mip_node()
        if not node:
            self._set_status("Select a MIP (or one of its MRCs) first.", error=True)
            return
        mip_id = node[1] if node[0] == "mip" else self._mrc_parent.get(node[1])
        vals = _FormDialog(self, "New MRC", [("code", "Code", ""),
                                             ("periodicity", "Periodicity", ""),
                                             ("description", "Description", "")]).show()
        if vals is None:
            return
        self._run(
            lambda s: cat.create_mrc(s, mip_id, vals["code"], vals["periodicity"], vals["description"]),
            "MRC created.",
        )

    def _edit_mip_node(self) -> None:
        node = self._selected_mip_node()
        if not node:
            return
        kind, _id = node
        if kind == "mip":
            with repo.session_scope() as s:
                cur = cat.get_mip(s, _id)
            vals = _FormDialog(self, "Edit MIP", [("code", "Code", cur["code"]),
                                                  ("title", "Title", cur["title"]),
                                                  ("notes", "Notes", cur["notes"])]).show()
            if vals is None:
                return
            self._run(lambda s: cat.update_mip(s, _id, vals["code"], vals["title"], vals["notes"]),
                      "MIP updated.")
        else:
            with repo.session_scope() as s:
                cur = cat.get_mrc(s, _id)
            vals = _FormDialog(self, "Edit MRC", [("code", "Code", cur["code"]),
                                                  ("periodicity", "Periodicity", cur["periodicity"]),
                                                  ("description", "Description", cur["description"])]).show()
            if vals is None:
                return
            self._run(
                lambda s: cat.update_mrc(s, _id, vals["code"], vals["periodicity"], vals["description"]),
                "MRC updated.",
            )

    def _delete_mip_node(self) -> None:
        node = self._selected_mip_node()
        if not node:
            return
        kind, _id = node
        what = "MIP (and all its MRCs)" if kind == "mip" else "MRC"
        if not messagebox.askyesno("Delete", f"Delete this {what}? This cannot be undone.", parent=self):
            return
        fn = cat.delete_mip if kind == "mip" else cat.delete_mrc
        self._run(lambda s: fn(s, _id), f"{kind.upper()} deleted.")

    # ----- MRC item links ------

    def _add_link(self) -> None:
        mrc_id = self._current_mrc_id()
        if mrc_id is None:
            return
        item_id = _ItemPicker(self).show()
        if item_id is None:
            return
        self._run(lambda s: cat.add_mrc_item(s, mrc_id, item_id), "Item linked.",
                  after=lambda: self._reload_links(mrc_id))

    def _remove_link(self) -> None:
        mrc_id = self._current_mrc_id()
        sel = self.link_tree.selection()
        if mrc_id is None or not sel:
            return
        item_id = int(sel[0])
        self._run(lambda s: cat.remove_mrc_item(s, mrc_id, item_id), "Item removed.",
                  after=lambda: self._reload_links(mrc_id))

    def _move_link(self, direction: str) -> None:
        mrc_id = self._current_mrc_id()
        sel = self.link_tree.selection()
        if mrc_id is None or not sel:
            return
        item_id = int(sel[0])
        self._run(lambda s: cat.move_mrc_item(s, mrc_id, item_id, direction), "",
                  after=lambda: (self._reload_links(mrc_id), self.link_tree.selection_set(str(item_id))))

    # ===== SPMIG / item actions =========================================

    def _new_spmig(self) -> None:
        vals = _FormDialog(self, "New SPMIG", [("code", "Code", ""),
                                               ("description", "Description", ""),
                                               ("notes", "Notes", "")]).show()
        if vals is None:
            return
        self._run(lambda s: cat.create_spmig(s, vals["code"], vals["description"], vals["notes"]),
                  "SPMIG created.")

    def _new_item(self) -> None:
        node = self._selected_spmig_node()
        if not node:
            self._set_status("Select a SPMIG (or one of its items) first.", error=True)
            return
        spmig_id = node[1] if node[0] == "sp" else self._item_parent_spmig(node[1])
        if spmig_id is None:
            return
        vals = _FormDialog(self, "New item", [("nomenclature", "Nomenclature", ""),
                                              ("niin", "NIIN", ""),
                                              ("unit_of_issue", "Unit of issue", ""),
                                              ("notes", "Notes", "")]).show()
        if vals is None:
            return
        self._run(
            lambda s: cat.create_item(s, spmig_id, vals["nomenclature"], vals["niin"],
                                      vals["unit_of_issue"], vals["notes"]),
            "Item created.",
        )

    def _item_parent_spmig(self, item_id: int) -> int | None:
        parent = self.sp_tree.parent(f"item:{item_id}")
        return int(parent.split(":")[1]) if parent else None

    def _edit_spmig_node(self) -> None:
        node = self._selected_spmig_node()
        if not node:
            return
        kind, _id = node
        if kind == "sp":
            with repo.session_scope() as s:
                cur = cat.get_spmig(s, _id)
            vals = _FormDialog(self, "Edit SPMIG", [("code", "Code", cur["code"]),
                                                    ("description", "Description", cur["description"]),
                                                    ("notes", "Notes", cur["notes"])]).show()
            if vals is None:
                return
            self._run(lambda s: cat.update_spmig(s, _id, vals["code"], vals["description"], vals["notes"]),
                      "SPMIG updated.")
        else:
            with repo.session_scope() as s:
                cur = cat.get_item(s, _id)
            vals = _FormDialog(self, "Edit item", [("nomenclature", "Nomenclature", cur["nomenclature"]),
                                                   ("niin", "NIIN", cur["niin"]),
                                                   ("unit_of_issue", "Unit of issue", cur["unit_of_issue"]),
                                                   ("notes", "Notes", cur["notes"])]).show()
            if vals is None:
                return
            self._run(
                lambda s: cat.update_item(s, _id, vals["nomenclature"], vals["niin"],
                                          vals["unit_of_issue"], vals["notes"]),
                "Item updated.",
            )

    def _delete_spmig_node(self) -> None:
        node = self._selected_spmig_node()
        if not node:
            return
        kind, _id = node
        what = "SPMIG" if kind == "sp" else "item"
        if not messagebox.askyesno("Delete", f"Delete this {what}? This cannot be undone.", parent=self):
            return
        fn = cat.delete_spmig if kind == "sp" else cat.delete_item
        self._run(lambda s: fn(s, _id), f"{what} deleted.")

    # ===== shared runner ================================================

    def _run(self, op, ok_msg: str, after=None) -> None:
        """Run a catalog mutation in a session; on CatalogError show the
        message; otherwise refresh and report success."""
        try:
            with repo.session_scope() as s:
                op(s)
        except cat.CatalogError as e:
            self._set_status(str(e), error=True)
            messagebox.showerror("Not allowed", str(e), parent=self)
            return
        except Exception as e:  # noqa: BLE001
            self._set_status(f"Failed: {e}", error=True)
            return
        if after is not None:
            after()
        else:
            self.refresh()
        if ok_msg:
            self._set_status(ok_msg)


class _FormDialog(tk.Toplevel):
    """Generic field form. fields = [(key, label, initial), ...]."""

    def __init__(self, master, title: str, fields: list[tuple[str, str, str]]) -> None:
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.transient(master.winfo_toplevel())
        self.result: dict | None = None
        self._vars: dict[str, tk.StringVar] = {}
        for row, (key, label, initial) in enumerate(fields):
            ttk.Label(self, text=label).grid(row=row, column=0, sticky="e", padx=6, pady=4)
            var = tk.StringVar(value=initial)
            ent = ttk.Entry(self, textvariable=var, width=42)
            ent.grid(row=row, column=1, padx=6, pady=4)
            if row == 0:
                ent.focus_set()
            self._vars[key] = var
        btns = ttk.Frame(self)
        btns.grid(row=len(fields), column=0, columnspan=2, pady=8)
        ttk.Button(btns, text="Save", command=self._ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()

    def _ok(self) -> None:
        self.result = {k: v.get() for k, v in self._vars.items()}
        self.destroy()

    def show(self) -> dict | None:
        self.wait_window()
        return self.result


class _ItemPicker(tk.Toplevel):
    """Search the catalog and pick one hazmat item; returns its id."""

    def __init__(self, master) -> None:
        super().__init__(master)
        self.title("Add item to MRC")
        self.transient(master.winfo_toplevel())
        self.result: int | None = None
        self._ids: list[int] = []

        ttk.Label(self, text="Search items (SPMIG / nomenclature / NIIN):").pack(
            anchor="w", padx=8, pady=(8, 2)
        )
        self.q = tk.StringVar()
        ent = ttk.Entry(self, textvariable=self.q, width=56)
        ent.pack(fill="x", padx=8)
        ent.bind("<KeyRelease>", lambda _e: self._search())
        ent.focus_set()
        self.listbox = tk.Listbox(self, width=66, height=14)
        self.listbox.pack(fill="both", expand=True, padx=8, pady=6)
        self.listbox.bind("<Double-1>", lambda _e: self._ok())
        btns = ttk.Frame(self)
        btns.pack(pady=(0, 8))
        ttk.Button(btns, text="Add", command=self._ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        self.grab_set()
        self._search()

    def _search(self) -> None:
        with repo.session_scope() as s:
            rows = repo.search_items(s, self.q.get())
            data = [(i.id, f"{(i.spmig.code if i.spmig else '?'):>6}  ·  {i.nomenclature}  ({i.niin or '-'})")
                    for i in rows]
        self._ids = [d[0] for d in data]
        self.listbox.delete(0, "end")
        for _id, label in data:
            self.listbox.insert("end", label)

    def _ok(self) -> None:
        sel = self.listbox.curselection()
        if sel:
            self.result = self._ids[sel[0]]
        self.destroy()

    def show(self) -> int | None:
        self.wait_window()
        return self.result
