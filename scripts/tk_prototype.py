#!/usr/bin/env python3
"""Launch the native Tk app.

    PYTHONPATH=. python scripts/tk_prototype.py

What it does, in order:

  1. Ensures the DB schema is current — but only runs Alembic if the DB
     is actually behind head (the gated-migration approach; a no-op run
     on an up-to-date DB is what makes native startup feel instant vs.
     the current "migrate on every launch").
  2. Opens the app shell (History + New Request) against an empty catalog.

The catalog starts empty. Set HAZREQ_SEED_SAMPLE=1 to populate a small
sample catalog on first run (developer/demo convenience only) — by default
nothing is pre-filled, so deleting catalog rows sticks across relaunches.

Requires Tk (python3-tk / python3-tkinter on the host) and a display.

PDF backend: defaults to the pure-Python "overlay" backend so finalize is
LibreOffice-free and near-instant (~20 ms). Override by exporting
HAZREQ_PDF_BACKEND=docx (LibreOffice) before launching.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Prefer the fast, dependency-free PDF path unless the user asked otherwise.
# Must happen before app.config is imported (settings load at import time).
os.environ.setdefault("HAZREQ_PDF_BACKEND", "overlay")


def ensure_migrated() -> None:
    """Upgrade to head only if the DB isn't already there."""
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from app.config import settings
    from app.db import engine

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", "app/migrations")
    cfg.set_main_option("sqlalchemy.url", settings.db_url)
    head = ScriptDirectory.from_config(cfg).get_current_head()

    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()

    if current == head:
        return  # already current — skip Alembic entirely
    from alembic import command

    command.upgrade(cfg, "head")


def main() -> int:
    ensure_migrated()

    # Catalog stays empty unless explicitly asked to seed sample data. This
    # keeps fresh installs clean and means deleted rows don't reappear.
    if os.environ.get("HAZREQ_SEED_SAMPLE") == "1":
        from app.ui import seed

        if seed.seed_if_empty():
            print("Seeded sample catalog (HAZREQ_SEED_SAMPLE=1).")

    try:
        import tkinter  # noqa: F401
    except ModuleNotFoundError:
        print(
            "Tk is not available for this Python. Install it on the host:\n"
            "  Debian/Raspberry Pi OS:  sudo apt install python3-tk\n"
            "  Void Linux:              sudo xbps-install -S python3-tkinter",
            file=sys.stderr,
        )
        return 1

    from app.ui import shell

    shell.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
