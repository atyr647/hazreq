"""Runtime configuration. All filesystem paths are env-driven so the
server stays portable: a hypothetical web port can point HAZREQ_DB_URL
at sqlite:///:memory: and the rest at /tmp without touching code.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = REPO_ROOT / "data"


def _path(env_var: str, default: Path) -> Path:
    raw = os.environ.get(env_var)
    return Path(raw).expanduser().resolve() if raw else default


@dataclass(frozen=True)
class Settings:
    db_url: str
    template_path: Path
    pdf_dir: Path
    backup_dir: Path
    unoserver_host: str
    unoserver_port: int
    persist_pdfs: bool
    pdf_backend: str
    fillable_pdf_path: Path | None
    printer: str | None
    browser_storage: bool
    sessions_dir: Path

    @classmethod
    def load(cls) -> Settings:
        data_dir = _path("HAZREQ_DATA_DIR", DEFAULT_DATA_DIR)
        db_default = f"sqlite:///{data_dir / 'hazreq.db'}"
        fillable_default = data_dir / "templates" / "hazmat_chit_fillable.pdf"
        fillable = _path("HAZREQ_FILLABLE_PDF_PATH", fillable_default)
        sessions_default = Path(tempfile.gettempdir()) / "hazreq-sessions"
        return cls(
            db_url=os.environ.get("HAZREQ_DB_URL", db_default),
            template_path=_path("HAZREQ_TEMPLATE_PATH", data_dir / "templates" / "hazmat_chit.docx"),
            pdf_dir=_path("HAZREQ_PDF_DIR", data_dir / "pdfs"),
            backup_dir=_path("HAZREQ_BACKUP_DIR", data_dir / "backups"),
            unoserver_host=os.environ.get("HAZREQ_UNOSERVER_HOST", "127.0.0.1"),
            unoserver_port=int(os.environ.get("HAZREQ_UNOSERVER_PORT", "2003")),
            persist_pdfs=os.environ.get("HAZREQ_PERSIST_PDFS", "1") != "0",
            pdf_backend=os.environ.get("HAZREQ_PDF_BACKEND", "docx").lower(),
            fillable_pdf_path=fillable,
            printer=(os.environ.get("HAZREQ_PRINTER") or None),
            browser_storage=os.environ.get("HAZREQ_BROWSER_STORAGE", "0") == "1",
            sessions_dir=_path("HAZREQ_SESSIONS_DIR", sessions_default),
        )


settings = Settings.load()
