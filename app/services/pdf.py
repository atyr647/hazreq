"""PDF rendering. Snapshot-only — never reads from the catalog tables.

Two render backends, switchable via HAZREQ_PDF_BACKEND:

  docx           (default) — fill .docx template via docxtpl, then convert
                 to PDF via unoserver/LibreOffice. Use this until a
                 fillable-PDF source form is provided.

  fillable_pdf   Fill an AcroForm-style PDF directly via pypdf and
                 flatten it. No LibreOffice dependency. Activated when
                 HAZREQ_PDF_BACKEND=fillable_pdf and the source PDF
                 lives at HAZREQ_FILLABLE_PDF_PATH.

Both backends consume a RequestSnapshot built from request_line columns
only. The catalog is invisible here.
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
        # 24h end-to-end on the printed form.
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
# Public API
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
    backend = (settings.pdf_backend or "docx").lower()
    if backend == "fillable_pdf":
        return _render_fillable_pdf(snap)
    if backend == "docx":
        return _render_docx_to_pdf(snap)
    raise PdfRenderError(f"Unknown PDF backend: {backend!r}")


# ============================================================
# Backend: docx → PDF (default until fillable PDF is provided)
# ============================================================

def _render_docx_to_pdf(snap: RequestSnapshot) -> bytes:
    docx_bytes = _render_docx_bytes(snap)
    return _convert_docx_to_pdf(docx_bytes)


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


# ============================================================
# Backend: fillable PDF (pypdf AcroForm fill + flatten)
# ============================================================

# Field-name convention expected on the source PDF. When the definitive
# fillable form is provided, name the AcroForm fields exactly:
#
#   header.location, header.name, header.datetime, header.workcenter, header.lpo
#   line.{i}.spmig, line.{i}.nomenclature, line.{i}.niin, line.{i}.qty
#       where i = 1..N (one-based, matches the row count on the form)
#
# Lines beyond the form's row count are listed on a continuation page if
# the source form has one (named with the same convention but offset),
# else they overflow into a plain "additional lines" appended page.

HEADER_FIELDS = {
    "header.location": "location",
    "header.name": "name",
    "header.datetime": "datetime",
    "header.workcenter": "workcenter",
    "header.lpo": "lpo",
}


def _render_fillable_pdf(snap: RequestSnapshot) -> bytes:
    src_path = settings.fillable_pdf_path
    if not src_path or not src_path.exists():
        raise PdfRenderError(
            f"Fillable PDF source not found at {src_path}. "
            "Set HAZREQ_FILLABLE_PDF_PATH to the AcroForm template."
        )

    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import BooleanObject, NameObject

    reader = PdfReader(str(src_path))
    writer = PdfWriter(clone_from=reader)

    # Build the field-value map.
    values: dict[str, str] = {}
    for field_name, attr in HEADER_FIELDS.items():
        values[field_name] = getattr(snap, attr) or ""

    # Detect available row slots by scanning existing field names.
    available_rows = _detect_row_slots(reader)
    for i, line in enumerate(snap.lines, start=1):
        if i > available_rows:
            break  # overflow handled below
        values[f"line.{i}.spmig"] = line.spmig
        values[f"line.{i}.nomenclature"] = line.nomenclature
        values[f"line.{i}.niin"] = line.niin
        values[f"line.{i}.qty"] = line.qty

    # Apply values to all pages that have form fields.
    for page in writer.pages:
        try:
            writer.update_page_form_field_values(page, values)
        except Exception as e:  # noqa: BLE001
            log.debug("update_page_form_field_values: %s", e)

    # Mark form as flattened-on-display. pypdf doesn't fully flatten
    # AcroForm fields yet, but setting NeedAppearances ensures viewers
    # render the supplied values.
    if "/AcroForm" in writer._root_object:
        writer._root_object["/AcroForm"][NameObject("/NeedAppearances")] = BooleanObject(True)

    # Append a continuation page for overflow lines, if any.
    overflow = snap.lines[available_rows:] if available_rows else snap.lines
    if overflow:
        cont = _build_overflow_page(snap, overflow)
        cont_reader = PdfReader(io.BytesIO(cont))
        for page in cont_reader.pages:
            writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _detect_row_slots(reader) -> int:
    fields = reader.get_form_text_fields() or {}
    max_row = 0
    for name in fields.keys():
        # Match "line.{i}.something"
        if not name.startswith("line."):
            continue
        try:
            i = int(name.split(".")[1])
        except (IndexError, ValueError):
            continue
        if i > max_row:
            max_row = i
    return max_row


def _build_overflow_page(snap: RequestSnapshot, lines: list[LineSnapshot]) -> bytes:
    """Render a simple continuation page using reportlab (pure-Python)."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.pdfgen import canvas
    except ImportError as e:
        raise PdfRenderError(
            "Overflow rendering needs reportlab. Add it to requirements."
        ) from e

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter
    y = height - 0.75 * inch

    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(width / 2, y, "HAZMAT ISSUE CHIT — Continuation")
    y -= 0.35 * inch
    c.setFont("Helvetica", 9)
    c.drawString(0.75 * inch, y, f"Request #{snap.id} · {snap.name}")
    y -= 0.3 * inch

    cols = [(0.75 * inch, "SPMIG/SPIN"), (2.0 * inch, "NOMENCLATURE"),
            (5.5 * inch, "NIIN"), (6.7 * inch, "QTY")]
    c.setFont("Helvetica-Bold", 9)
    for x, label in cols:
        c.drawString(x, y, label)
    y -= 0.18 * inch
    c.line(0.75 * inch, y, width - 0.75 * inch, y)
    y -= 0.18 * inch

    c.setFont("Helvetica", 9)
    for line in lines:
        if y < 0.75 * inch:
            c.showPage()
            y = height - 0.75 * inch
            c.setFont("Helvetica", 9)
        c.drawString(cols[0][0], y, line.spmig[:18])
        c.drawString(cols[1][0], y, line.nomenclature[:55])
        c.drawString(cols[2][0], y, line.niin[:12])
        c.drawString(cols[3][0], y, line.qty[:8])
        y -= 0.22 * inch

    c.save()
    return buf.getvalue()


# Tiny accessor so the overflow renderer can stringify the requestor.
def _requestor_name(snap: RequestSnapshot) -> str:
    return snap.name or ""


# Attach as a method on RequestSnapshot for convenience.
RequestSnapshot.requestor_name_display = staticmethod(_requestor_name)
