"""PDF rendering. Snapshot-only — never reads from the catalog tables.

Render path (default, .docx source):
  1. docxtpl fills the template with snapshot fields.
  2. unoserver (warm headless LibreOffice) converts .docx -> PDF.

The catalog is intentionally invisible to this module. Callers pass a
RequestSnapshot built from request_line columns.
"""

from __future__ import annotations

import io
import logging
import shutil
import socket
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.models import Request as ReqModel

log = logging.getLogger(__name__)


# ============================================================
# Snapshot types — what the renderer sees
# ============================================================

@dataclass(frozen=True)
class LineSnapshot:
    spmig: str
    nomenclature: str
    niin: str
    qty: str


@dataclass(frozen=True)
class RequestSnapshot:
    id: int
    name: str
    workcenter: str
    lpo: str
    location: str
    datetime: str
    lines: list[LineSnapshot]


def snapshot_from_model(r: ReqModel) -> RequestSnapshot:
    def _f(s: float | None) -> str:
        if s is None:
            return ""
        if float(s).is_integer():
            return str(int(s))
        return f"{s:g}"

    when = r.datetime_of_request or r.created_at
    return RequestSnapshot(
        id=r.id,
        name=(r.requestor_name or "").strip(),
        workcenter=(r.workcenter or "").strip(),
        lpo=(r.lpo or "").strip(),
        location=(r.hazmat_location or "").strip(),
        datetime=when.strftime("%Y-%m-%d %H:%M") if when else "",
        lines=[
            LineSnapshot(
                spmig=(ln.spmig_code or "").strip(),
                nomenclature=(ln.nomenclature or "").strip(),
                niin=(ln.niin or "").strip(),
                qty=_f(ln.qty),
            )
            for ln in sorted(r.lines, key=lambda x: x.sort_order)
        ],
    )


# ============================================================
# Render API
# ============================================================

class PdfRenderError(RuntimeError):
    pass


def render_request_pdf(r: ReqModel) -> Path | None:
    """Render PDF for this request and persist it (if persist_pdfs is on).

    Returns the persisted path, or None if persistence is disabled.
    Raises PdfRenderError on failure.
    """
    snap = snapshot_from_model(r)
    pdf_bytes = render_pdf_bytes(snap)
    if not settings.persist_pdfs:
        return None
    settings.pdf_dir.mkdir(parents=True, exist_ok=True)
    out = settings.pdf_dir / f"request-{snap.id}.pdf"
    out.write_bytes(pdf_bytes)
    return out


def render_pdf_bytes(snap: RequestSnapshot) -> bytes:
    docx_bytes = _render_docx_bytes(snap)
    return _convert_docx_to_pdf(docx_bytes)


# ============================================================
# Internals
# ============================================================

def _render_docx_bytes(snap: RequestSnapshot) -> bytes:
    from docxtpl import DocxTemplate  # lazy import

    template_path = settings.template_path
    if not template_path.exists():
        raise PdfRenderError(
            f"Template not found at {template_path}. Run scripts/prepare_template.py."
        )
    tpl = DocxTemplate(str(template_path))
    tpl.render(
        {
            "name": snap.name,
            "workcenter": snap.workcenter,
            "lpo": snap.lpo,
            "location": snap.location,
            "datetime": snap.datetime,
            "lines": [
                {
                    "spmig": ln.spmig,
                    "nomenclature": ln.nomenclature,
                    "niin": ln.niin,
                    "qty": ln.qty,
                }
                for ln in snap.lines
            ],
        }
    )
    buf = io.BytesIO()
    tpl.save(buf)
    return buf.getvalue()


def _convert_docx_to_pdf(docx_bytes: bytes) -> bytes:
    """Try unoserver first; fall back to spawning libreoffice headless."""
    if _unoserver_alive():
        try:
            return _convert_via_unoserver(docx_bytes)
        except Exception as e:
            log.warning("unoserver conversion failed (%s); falling back to headless soffice", e)
    return _convert_via_soffice(docx_bytes)


def _unoserver_alive() -> bool:
    try:
        with socket.create_connection((settings.unoserver_host, settings.unoserver_port), timeout=0.25):
            return True
    except OSError:
        return False


def _convert_via_unoserver(docx_bytes: bytes) -> bytes:
    """Use the `unoconvert` CLI shipped with unoserver."""
    cli = shutil.which("unoconvert")
    if not cli:
        raise PdfRenderError("unoconvert CLI not found in PATH")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"req-{uuid.uuid4().hex}.docx"
        dst = src.with_suffix(".pdf")
        src.write_bytes(docx_bytes)
        cmd = [
            cli,
            "--host", settings.unoserver_host,
            "--port", str(settings.unoserver_port),
            "--convert-to", "pdf",
            str(src),
            str(dst),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        if result.returncode != 0 or not dst.exists():
            raise PdfRenderError(
                f"unoconvert failed: {result.stderr.decode('utf-8', 'ignore').strip()}"
            )
        return dst.read_bytes()


def _convert_via_soffice(docx_bytes: bytes) -> bytes:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise PdfRenderError(
            "Neither unoserver nor LibreOffice is available. Install libreoffice-writer "
            "or start the hazreq-unoserver service."
        )
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"req-{uuid.uuid4().hex}.docx"
        src.write_bytes(docx_bytes)
        cmd = [
            soffice,
            "--headless",
            "--convert-to", "pdf",
            "--outdir", tmp,
            str(src),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        pdfs = list(Path(tmp).glob("*.pdf"))
        if result.returncode != 0 or not pdfs:
            raise PdfRenderError(
                f"libreoffice failed: {result.stderr.decode('utf-8', 'ignore').strip()}"
            )
        return pdfs[0].read_bytes()


# Marker so callers that need a "rendered at" timestamp can use it.
def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")
