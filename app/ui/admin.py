"""Admin screen for the Tk app: health, backup/restore, catalog CSV and
whole-DB JSON import/export, and the audit log — the native equivalent of
the web /admin page. Reuses the same services the web router uses.
"""

from __future__ import annotations

import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.services import backup as backup_service
from app.services import csvio, jsonio
from app.ui import admin_repo, repo

AUDIT_DEBOUNCE_MS = 200


class AdminScreen(ttk.Frame):
    def __init__(self, master, shell=None) -> None:
        super().__init__(master, padding=8)
        self.shell = shell
        self._audit_after: str | None = None
        self._build_health()
        self._build_actions()
        self._build_audit()
        self.status = ttk.Label(self, text="", anchor="w")
        self.status.pack(fill="x", pady=(4, 0))
        self.refresh()

    def _set_status(self, text: str, error: bool = False) -> None:
        self.status.configure(text=text, foreground="#b00020" if error else "")

    # ---- health --------------------------------------------------------

    def _build_health(self) -> None:
        self.health = ttk.LabelFrame(self, text="Health", padding=8)
        self.health.pack(fill="x")
        self._health_labels: dict[str, ttk.Label] = {}
        rows = [
            ("db", "Database"), ("size", "DB size"), ("backend", "PDF backend"),
            ("overlay", "Overlay form"), ("pdfs", "Generated PDFs"), ("printing", "Printing"),
        ]
        for i, (key, label) in enumerate(rows):
            r, c = divmod(i, 2)
            ttk.Label(self.health, text=f"{label}:", width=14, anchor="e").grid(
                row=r, column=c * 2, sticky="e", padx=(4, 4), pady=2
            )
            val = ttk.Label(self.health, text="—", anchor="w")
            val.grid(row=r, column=c * 2 + 1, sticky="w", padx=(0, 16), pady=2)
            self._health_labels[key] = val

    # ---- backup / import-export actions --------------------------------

    def _build_actions(self) -> None:
        f = ttk.LabelFrame(self, text="Backup & data", padding=8)
        f.pack(fill="x", pady=(8, 0))
        row1 = ttk.Frame(f)
        row1.pack(fill="x")
        ttk.Button(row1, text="Backup (DB + PDFs)…", command=self._backup).pack(side="left")
        ttk.Button(row1, text="Restore from backup…", command=self._restore).pack(side="left", padx=4)
        row2 = ttk.Frame(f)
        row2.pack(fill="x", pady=(6, 0))
        ttk.Button(row2, text="Export catalog (CSV)…", command=self._export_csv).pack(side="left")
        ttk.Button(row2, text="Import catalog (CSV)…", command=self._import_csv).pack(side="left", padx=4)
        ttk.Button(row2, text="Export all (JSON)…", command=self._export_json).pack(side="left")
        ttk.Button(row2, text="Import all (JSON)…", command=self._import_json).pack(side="left", padx=4)

    # ---- audit log -----------------------------------------------------

    def _build_audit(self) -> None:
        f = ttk.LabelFrame(self, text="Audit log", padding=6)
        f.pack(fill="both", expand=True, pady=(8, 0))
        bar = ttk.Frame(f)
        bar.pack(fill="x")
        ttk.Label(bar, text="Type").pack(side="left")
        self.audit_type = tk.StringVar(value="all")
        ttk.Combobox(
            bar, textvariable=self.audit_type, width=12, state="readonly",
            values=["all", "MIP", "MRC", "SPMIG", "HazmatItem", "MRCItem"],
        ).pack(side="left", padx=(2, 10))
        ttk.Label(bar, text="Action").pack(side="left")
        self.audit_action = tk.StringVar(value="all")
        ttk.Combobox(
            bar, textvariable=self.audit_action, width=8, state="readonly",
            values=["all", "create", "update", "delete"],
        ).pack(side="left", padx=(2, 10))
        ttk.Label(bar, text="Search").pack(side="left")
        self.audit_q = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=self.audit_q, width=24)
        ent.pack(side="left", padx=2)
        ent.bind("<KeyRelease>", lambda _e: self._debounced_audit())
        for var in (self.audit_type, self.audit_action):
            var.trace_add("write", lambda *_: self.refresh_audit())

        cols = ("ts", "action", "type", "key", "summary")
        self.audit = ttk.Treeview(f, columns=cols, show="headings")
        for c, txt, w in [
            ("ts", "When", 150), ("action", "Action", 70), ("type", "Type", 90),
            ("key", "Entity", 160), ("summary", "Summary", 360),
        ]:
            self.audit.heading(c, text=txt)
            self.audit.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(f, orient="vertical", command=self.audit.yview)
        self.audit.configure(yscrollcommand=sb.set)
        self.audit.pack(side="left", fill="both", expand=True, pady=(6, 0))
        sb.pack(side="right", fill="y")

    def _debounced_audit(self) -> None:
        if self._audit_after:
            self.after_cancel(self._audit_after)
        self._audit_after = self.after(AUDIT_DEBOUNCE_MS, self.refresh_audit)

    # ---- refresh -------------------------------------------------------

    def refresh(self) -> None:
        h = admin_repo.health_info()
        self._health_labels["db"].configure(text=h["db_path"])
        self._health_labels["size"].configure(text=f"{h['db_size_mb']} MB")
        self._health_labels["backend"].configure(text=h["pdf_backend"])
        self._health_labels["overlay"].configure(
            text="present" if h["overlay_present"] else "MISSING"
        )
        self._health_labels["pdfs"].configure(text=str(h["pdf_count"]))
        printers = f"{len(h['printers'])} printer(s)" if h["printing"] else "no CUPS"
        self._health_labels["printing"].configure(text=printers)
        self.refresh_audit()

    def refresh_audit(self) -> None:
        et = "" if self.audit_type.get() == "all" else self.audit_type.get()
        ac = "" if self.audit_action.get() == "all" else self.audit_action.get()
        try:
            with repo.session_scope() as s:
                rows = admin_repo.list_audit(s, entity_type=et, action=ac, q=self.audit_q.get())
        except Exception as e:  # noqa: BLE001
            self._set_status(f"Audit load failed: {e}", error=True)
            return
        self.audit.delete(*self.audit.get_children())
        for r in rows:
            self.audit.insert("", "end", values=(r.ts, r.action, r.entity_type, r.entity_key, r.summary))

    # ---- handlers ------------------------------------------------------

    def _backup(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self, title="Save backup", defaultextension=".zip",
            initialfile=backup_service.default_backup_name(),
            filetypes=[("Zip archive", "*.zip")],
        )
        if not path:
            return
        try:
            data = backup_service.make_backup_zip()
            with open(path, "wb") as fh:
                fh.write(data)
        except Exception as e:  # noqa: BLE001
            self._set_status(f"Backup failed: {e}", error=True)
            messagebox.showerror("Backup failed", str(e), parent=self)
            return
        self._set_status(f"Backup written to {path}")

    def _restore(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="Restore from backup",
            filetypes=[("Backup", "*.zip *.db"), ("All files", "*.*")],
        )
        if not path:
            return
        if not messagebox.askyesno(
            "Restore", "Replace the current database with this backup? "
            "Unsaved data will be lost.", parent=self,
        ):
            return
        try:
            backup_service.restore_db_from_path(path)
        except Exception as e:  # noqa: BLE001
            self._set_status(f"Restore failed: {e}", error=True)
            messagebox.showerror("Restore failed", str(e), parent=self)
            return
        self._set_status("Database restored.")
        self.refresh()

    def _export_csv(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self, title="Export catalog CSV", defaultextension=".zip",
            initialfile="hazreq-catalog.zip", filetypes=[("Zip archive", "*.zip")],
        )
        if not path:
            return
        try:
            with repo.session_scope() as s:
                data = csvio.export_zip(s)
            with open(path, "wb") as fh:
                fh.write(data)
        except Exception as e:  # noqa: BLE001
            self._set_status(f"Export failed: {e}", error=True)
            return
        self._set_status(f"Catalog exported to {path}")

    def _import_csv(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="Import catalog CSV", filetypes=[("Zip archive", "*.zip")]
        )
        if not path:
            return
        try:
            with open(path, "rb") as fh:
                payload = fh.read()
            with repo.session_scope() as s:
                report = csvio.import_zip(s, payload)
        except Exception as e:  # noqa: BLE001
            self._set_status(f"Import failed: {e}", error=True)
            messagebox.showerror("Import failed", str(e), parent=self)
            return
        self._set_status(f"Catalog imported: {report}")
        self.refresh()

    def _export_json(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self, title="Export all (JSON)", defaultextension=".json",
            initialfile="hazreq-backup.json", filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        try:
            with repo.session_scope() as s:
                payload = jsonio.export_to_dict(s)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            self._set_status(f"Export failed: {e}", error=True)
            return
        self._set_status(f"Exported to {path}")

    def _import_json(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="Import all (JSON)", filetypes=[("JSON", "*.json")]
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
            with repo.session_scope() as s:
                counts = jsonio.import_from_dict(s, payload)
        except Exception as e:  # noqa: BLE001
            self._set_status(f"Import failed: {e}", error=True)
            messagebox.showerror("Import failed", str(e), parent=self)
            return
        self._set_status(f"Imported: {counts}")
        self.refresh()
