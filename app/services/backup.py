"""Backup / restore of the SQLite database (+ generated PDFs).

Shared by the web admin router and the native Tk admin screen. The DB is
copied with SQLite's online backup API (safe while the app is running);
restore validates the schema, swaps the file atomically, and clears the
WAL/SHM sidecars so SQLite doesn't replay stale transactions.
"""

from __future__ import annotations

import io
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.db import engine

REQUIRED_TABLES = {
    "mip", "mrc", "spmig", "hazmat_item", "mrc_item", "request", "request_line",
}


class BackupError(RuntimeError):
    """A backup/restore precondition failed, with a user-facing message."""


def db_path() -> Path | None:
    """Filesystem path of the SQLite DB, or None for a non-sqlite URL."""
    if settings.db_url.startswith("sqlite:///"):
        return Path(settings.db_url.replace("sqlite:///", "", 1)).resolve()
    return None


def default_backup_name() -> str:
    return f"hazreq-backup-{datetime.now().strftime('%Y%m%d-%H%M')}.zip"


def make_backup_zip() -> bytes:
    """A .zip containing the DB (via the online backup API) + all PDFs."""
    src_path = db_path()
    if not src_path or not src_path.exists():
        raise BackupError("No SQLite database to back up.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_db = Path(tmp) / "hazreq.db"
            src = sqlite3.connect(str(src_path))
            dst = sqlite3.connect(str(tmp_db))
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            zf.write(tmp_db, "hazreq.db")
        if settings.pdf_dir.exists():
            for pdf in sorted(settings.pdf_dir.glob("*.pdf")):
                zf.write(pdf, f"pdfs/{pdf.name}")
    return buf.getvalue()


def restore_db_from_bytes(payload: bytes) -> None:
    """Validate a SQLite DB image and swap it in for the live database.

    Accepts either a raw .db image or a backup .zip (the DB is taken from
    its `hazreq.db` member).
    """
    dest = db_path()
    if not dest:
        raise BackupError("Restore is only supported for SQLite deployments.")
    if not payload:
        raise BackupError("Empty upload.")

    # If it's one of our backup zips, pull hazreq.db out of it.
    if payload[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                payload = zf.read("hazreq.db")
        except (zipfile.BadZipFile, KeyError) as e:
            raise BackupError(f"Not a valid hazreq backup zip: {e}") from None

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    try:
        conn = sqlite3.connect(str(tmp_path))
        try:
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        except sqlite3.DatabaseError as e:
            raise BackupError(f"Not a valid SQLite database: {e}") from None
        finally:
            conn.close()
        missing = REQUIRED_TABLES - tables
        if missing:
            raise BackupError(f"Backup is missing tables: {sorted(missing)}")

        # Drop pooled connections, swap the file, clear WAL/SHM sidecars.
        engine.dispose()
        shutil.copyfile(tmp_path, dest)
        for sidecar in (
            dest.with_suffix(dest.suffix + "-wal"),
            dest.with_suffix(dest.suffix + "-shm"),
        ):
            if sidecar.exists():
                sidecar.unlink()
    finally:
        tmp_path.unlink(missing_ok=True)


def restore_db_from_path(path: str | Path) -> None:
    restore_db_from_bytes(Path(path).read_bytes())
