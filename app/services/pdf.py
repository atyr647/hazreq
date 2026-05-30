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
import threading
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
    if backend == "overlay":
        return _render_overlay_pdf(snap)
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


# The source form prints six blank line-item rows. Pad to this minimum
# so a short request still looks like the original chit.
DOCX_MIN_LINE_ROWS = 6


def _render_docx_bytes(snap: RequestSnapshot) -> bytes:
    from docxtpl import DocxTemplate  # lazy import

    template_path = settings.template_path
    if not template_path.exists():
        raise PdfRenderError(
            f"Template not found at {template_path}. Run scripts/prepare_template.py."
        )
    lines = [
        {"spmig": ln.spmig, "nomenclature": ln.nomenclature, "niin": ln.niin, "qty": ln.qty}
        for ln in snap.lines
    ]
    while len(lines) < DOCX_MIN_LINE_ROWS:
        lines.append({"spmig": "", "nomenclature": "", "niin": "", "qty": ""})

    tpl = DocxTemplate(str(template_path))
    tpl.render(
        {
            "name": snap.name,
            "workcenter": snap.workcenter,
            "lpo": snap.lpo,
            "location": snap.location,
            "datetime": snap.datetime,
            "lines": lines,
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
# Backend: overlay (pure-Python, no LibreOffice)
# ============================================================
#
# Draws the request's values onto the static blank chit
# ("Hazmat Request Blank.pdf") with reportlab, then merges that overlay
# onto the real form pages with pypdf. No AcroForm, no docx, no soffice —
# a render is a few milliseconds, which is the whole point on a Pi 400.
#
# Coordinates were measured once from the blank form (Letter, 612x792 pt)
# with pdfminer. Origin here is top-left (y grows downward); the renderer
# converts to PDF's bottom-left origin at draw time. If the master form
# is re-laid-out, re-measure these.

PAGE_W, PAGE_H = 612.0, 792.0

# attr on RequestSnapshot -> (value_x, label_baseline_y). The baseline is
# the label's own text baseline (measured from the form), so the value
# sits on the same line as its label rather than floating in the cell.
_OVERLAY_HEADER = {
    "location": (184.0, 259.3),
    "name": (122.0, 311.4),
    "datetime": (151.0, 337.1),
    "workcenter": (158.0, 363.1),
    "lpo": (111.0, 389.1),
}

# Line-item table column edges (x): SPMIG | NOMENCLATURE | NIIN | QTY
_OVERLAY_COLS = {
    "spmig": (76.5, 206.8),
    "nomenclature": (206.8, 360.1),
    "niin": (360.1, 503.2),
    "qty": (503.2, 553.7),
}
_COL_EDGES = (76.5, 206.8, 360.1, 503.2, 553.7)

# The data-row table is drawn DYNAMICALLY: exactly N rows for N items, so
# it grows/shrinks instead of using the form's fixed pre-printed grid. The
# pre-printed rows in this band are whited out and the seal re-painted
# behind the new rows.
_TABLE_X0, _TABLE_X1 = 76.5, 553.7
_TABLE_TOP = 453.6        # top of the first data row on page 1 (top-origin y)
_ROW_H = 25.98            # matches the form's row pitch
_CLEAR_BOTTOM = 745.0     # clear past the form's bottom border (712.7) — no stray line
_BOTTOM_LIMIT = 740.0     # table + footer may extend down to here
_GRID_LINE_W = 0.6

# Continuation pages are pure item-line tables (no form header): a column
# header near the top, then rows filling the page, then the footer.
_CONT_TABLE_TOP = 110.0
_COLHEADER_BASE = 13.3    # label baseline within the column-header row
_COLHEADER = [            # x, text — matches the form (incl. its spelling)
    (81.8, "SPMIG/SPIN"), (212.1, "NONMENCLATURE"), (365.4, "NIIN   7"), (508.7, "QTY"),
]

# Footer block ("ISSUING HAZMAT" + signature box), redrawn directly under
# the table so it follows the rows. Offsets are top-origin from the footer
# block's own top; measured from the form (height 712.7 - 635.5 = 77.2).
_FOOTER_H = 77.2
_FOOTER_MID = 26.0         # ISSUING HAZMAT row height
_ISSUING_BASE = 13.5       # "ISSUING HAZMAT" baseline within the block
_MAINT_BASE = 39.5         # "MAINTENANCE REQUESTOR :" baseline
_SIG_BASE = 64.7           # "DIGITAL SIGNATURE:" baseline

# Watermark seal placement in the source form (bottom-origin). The form
# draws it at ca=1 (the fade is baked into the JPEG), so we re-paint it
# as-is, clipped to the cleared band.
_WM_RECT = (74.25, 110.32, 515.25, 517.43)
_WM_CACHE: dict[int, bytes] = {}


def _watermark_image(blank_bytes: bytes):
    """ImageReader for the form's seal (extracted once), or None."""
    from reportlab.lib.utils import ImageReader

    data = _WM_CACHE.get(len(blank_bytes))
    if data is None:
        try:
            from pypdf import PdfReader

            imgs = PdfReader(io.BytesIO(blank_bytes)).pages[0].images
            data = imgs[0].data if imgs else b""
        except Exception as e:  # noqa: BLE001
            log.warning("watermark extraction failed: %s", e)
            data = b""
        _WM_CACHE[len(blank_bytes)] = data
    return ImageReader(io.BytesIO(data)) if data else None

_HEADER_SIZE = 11.0  # matches the form's Calibri 11 labels
_HEADER_BASELINE_FIX = 2.4  # font descent: lift values onto the label baseline
_ROW_SIZE = 10.0
_ROW_MIN_SIZE = 7.5
_CELL_PAD = 4.0

# Lazily-registered overlay font: Carlito (metric-compatible with the
# form's Calibri), falling back to Helvetica if the TTF isn't available.
_FONT_LOCK = threading.Lock()
_FONT_NAME: str | None = None


def _overlay_font() -> str:
    global _FONT_NAME
    if _FONT_NAME is not None:
        return _FONT_NAME
    with _FONT_LOCK:
        if _FONT_NAME is not None:
            return _FONT_NAME
        name = "Helvetica"
        path = settings.overlay_font_path
        try:
            if path and Path(path).exists():
                from reportlab.pdfbase import pdfmetrics
                from reportlab.pdfbase.ttfonts import TTFont

                pdfmetrics.registerFont(TTFont("HazreqForm", str(path)))
                name = "HazreqForm"
        except Exception as e:  # noqa: BLE001
            log.warning("overlay font load failed (%s); using Helvetica", e)
        _FONT_NAME = name
        return name


_FONT_BOLD_NAME: str | None = None


def _overlay_font_bold() -> str:
    """Bold variant (Carlito-Bold) for the redrawn footer labels, which are
    bold on the form. Falls back to the regular overlay font."""
    global _FONT_BOLD_NAME
    if _FONT_BOLD_NAME is not None:
        return _FONT_BOLD_NAME
    with _FONT_LOCK:
        if _FONT_BOLD_NAME is not None:
            return _FONT_BOLD_NAME
        name = _overlay_font()
        try:
            bold = Path(settings.overlay_font_path).with_name("Carlito-Bold.ttf")
            if bold.exists():
                from reportlab.pdfbase import pdfmetrics
                from reportlab.pdfbase.ttfonts import TTFont

                pdfmetrics.registerFont(TTFont("HazreqFormBold", str(bold)))
                name = "HazreqFormBold"
        except Exception as e:  # noqa: BLE001
            log.warning("overlay bold font load failed (%s)", e)
        _FONT_BOLD_NAME = name
        return name


def _fit_text(text: str, max_w: float, size: float) -> tuple[str, float]:
    """Return (text, size) shrinking the font (to _ROW_MIN_SIZE) then
    truncating with an ellipsis so the string fits within max_w."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    font = _overlay_font()
    text = text or ""
    s = size
    while s > _ROW_MIN_SIZE and stringWidth(text, font, s) > max_w:
        s -= 0.5
    if stringWidth(text, font, s) <= max_w:
        return text, s
    while text and stringWidth(text + "…", font, s) > max_w:
        text = text[:-1]
    return (text + "…") if text else "", s


def _draw_cell(c, col: str, text: str, row_top: float, row_bottom: float, center: bool) -> None:
    x0, x1 = _OVERLAY_COLS[col]
    max_w = (x1 - x0) - 2 * _CELL_PAD
    text, size = _fit_text(text, max_w, _ROW_SIZE)
    if not text:
        return
    from reportlab.pdfbase.pdfmetrics import stringWidth

    font = _overlay_font()
    baseline_top = (row_top + row_bottom) / 2 + size * 0.35
    y = PAGE_H - baseline_top
    c.setFont(font, size)
    x = (x0 + x1) / 2 - stringWidth(text, font, size) / 2 if center else x0 + _CELL_PAD
    c.drawString(x, y, text)


def _draw_footer(c, footer_top: float) -> None:
    """Redraw the form's footer block (ISSUING HAZMAT row + signature box)
    with its top at footer_top, so it follows directly under the table."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    bold = _overlay_font_bold()
    c.setLineWidth(_GRID_LINE_W)
    # Box rules: top, the ISSUING/MAINTENANCE divider, and the bottom.
    for off in (0.0, _FOOTER_MID, _FOOTER_H):
        yy = PAGE_H - (footer_top + off)
        c.line(_TABLE_X0, yy, _TABLE_X1, yy)
    # Side borders down the whole block.
    for x in (_TABLE_X0, _TABLE_X1):
        c.line(x, PAGE_H - footer_top, x, PAGE_H - (footer_top + _FOOTER_H))
    # "ISSUING HAZMAT" centred in the top row.
    c.setFont(bold, _HEADER_SIZE)
    txt = "ISSUING HAZMAT"
    cx = (_TABLE_X0 + _TABLE_X1) / 2 - stringWidth(txt, bold, _HEADER_SIZE) / 2
    c.drawString(cx, PAGE_H - (footer_top + _ISSUING_BASE), txt)
    # Requestor + signature labels.
    c.drawString(81.8, PAGE_H - (footer_top + _MAINT_BASE), "MAINTENANCE REQUESTOR :")
    sig = "DIGITAL SIGNATURE:"
    c.drawString(81.8, PAGE_H - (footer_top + _SIG_BASE), sig)
    # Signature rule after the label.
    sig_x = 81.8 + stringWidth(sig, bold, _HEADER_SIZE) + 8
    sy = PAGE_H - (footer_top + _SIG_BASE) + 1
    c.line(sig_x, sy, 360.0, sy)


def _draw_rows(c, top_y: float, lines: list[LineSnapshot]) -> float:
    """Draw an N-row grid + cell text starting at top_y; return its bottom."""
    n = len(lines)
    bottom = top_y + n * _ROW_H
    c.setLineWidth(_GRID_LINE_W)
    for i in range(n + 1):
        yy = PAGE_H - (top_y + i * _ROW_H)
        c.line(_TABLE_X0, yy, _TABLE_X1, yy)
    for x in _COL_EDGES:
        c.line(x, PAGE_H - top_y, x, PAGE_H - bottom)
    for i, line in enumerate(lines):
        rt = top_y + i * _ROW_H
        rb = rt + _ROW_H
        _draw_cell(c, "spmig", line.spmig, rt, rb, center=False)
        _draw_cell(c, "nomenclature", line.nomenclature, rt, rb, center=False)
        _draw_cell(c, "niin", line.niin, rt, rb, center=True)
        _draw_cell(c, "qty", line.qty, rt, rb, center=True)
    return bottom


def _draw_colheader(c, top_y: float) -> float:
    """Draw the table's column-header row at top_y; return its bottom."""
    bold = _overlay_font_bold()
    bottom = top_y + _ROW_H
    c.setLineWidth(_GRID_LINE_W)
    for yy in (PAGE_H - top_y, PAGE_H - bottom):
        c.line(_TABLE_X0, yy, _TABLE_X1, yy)
    for x in _COL_EDGES:
        c.line(x, PAGE_H - top_y, x, PAGE_H - bottom)
    c.setFont(bold, _HEADER_SIZE)
    for x, txt in _COLHEADER:
        c.drawString(x, PAGE_H - (top_y + _COLHEADER_BASE), txt)
    return bottom


def _paint_seal(c, wm, top: float, bottom: float, *, clear: bool) -> None:
    """Re-paint the form seal in [top, bottom]. When `clear`, first white out
    that band (used on page 1 to hide the form's pre-printed rows/footer)."""
    box_x, box_w = _TABLE_X0 - 1.0, (_TABLE_X1 - _TABLE_X0) + 2.0
    by, bh = PAGE_H - bottom, bottom - top
    if clear:
        c.setFillColorRGB(1, 1, 1)
        c.rect(box_x, by, box_w, bh, fill=1, stroke=0)
        c.setFillColorRGB(0, 0, 0)
    if wm is None:
        return
    c.saveState()
    clip = c.beginPath()
    clip.rect(box_x, by, box_w, bh)
    c.clipPath(clip, stroke=0, fill=0)
    x, y, w, h = _WM_RECT
    c.drawImage(wm, x, y, width=w, height=h, mask="auto")
    c.restoreState()


def _page1_bytes(snap: RequestSnapshot, lines: list[LineSnapshot], wm) -> bytes:
    """Page 1: the form's header values overlaid, then the dynamic table +
    footer drawn over the cleared lower area of the blank-form background."""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))
    # Header values sit on each label's baseline (lifted by the font descent).
    c.setFont(_overlay_font(), _HEADER_SIZE)
    for attr, (vx, baseline) in _OVERLAY_HEADER.items():
        val = getattr(snap, attr, "") or ""
        if val:
            c.drawString(vx, PAGE_H - (baseline - _HEADER_BASELINE_FIX), val)
    _paint_seal(c, wm, _TABLE_TOP, _CLEAR_BOTTOM, clear=True)
    bottom = _draw_rows(c, _TABLE_TOP, lines)
    _draw_footer(c, bottom)
    c.showPage()
    c.save()
    return buf.getvalue()


def _continuation_bytes(lines: list[LineSnapshot], wm) -> bytes:
    """A continuation page: purely the item-line table — seal, a column
    header near the top, the rows, then the footer. No form header."""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))
    _paint_seal(c, wm, _CONT_TABLE_TOP, _BOTTOM_LIMIT, clear=False)
    rows_top = _draw_colheader(c, _CONT_TABLE_TOP)
    bottom = _draw_rows(c, rows_top, lines)
    _draw_footer(c, bottom)
    c.showPage()
    c.save()
    return buf.getvalue()


def _rows_per_page(table_top: float) -> int:
    """How many rows fit above the footer when the table starts at table_top."""
    return max(1, int((_BOTTOM_LIMIT - _FOOTER_H - table_top) // _ROW_H))


def _render_overlay_pdf(snap: RequestSnapshot) -> bytes:
    src = settings.overlay_pdf_path
    if not src or not Path(src).exists():
        raise PdfRenderError(
            f"Overlay form not found at {src}. Set HAZREQ_OVERLAY_PDF_PATH to the blank chit PDF."
        )
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as e:  # pragma: no cover
        raise PdfRenderError("overlay backend needs pypdf") from e

    blank_bytes = Path(src).read_bytes()
    n_form_pages = len(PdfReader(io.BytesIO(blank_bytes)).pages)
    wm = _watermark_image(blank_bytes)

    rows = list(snap.lines)
    cap1 = _rows_per_page(_TABLE_TOP)
    cap_cont = _rows_per_page(_CONT_TABLE_TOP + _ROW_H)  # account for the column header
    page1_rows, rest = rows[:cap1], rows[cap1:]
    cont_chunks = [rest[i : i + cap_cont] for i in range(0, len(rest), cap_cont)]

    writer = PdfWriter()
    # Page 1: overlay onto the blank-form background.
    page = writer.add_page(PdfReader(io.BytesIO(blank_bytes)).pages[0])
    page.merge_page(PdfReader(io.BytesIO(_page1_bytes(snap, page1_rows, wm))).pages[0])
    # Continuation pages: clean item-line tables (footer on each).
    for chunk in cont_chunks:
        writer.add_page(PdfReader(io.BytesIO(_continuation_bytes(chunk, wm))).pages[0])
    # Original signature / notes page appended last.
    if n_form_pages > 1:
        for p in PdfReader(io.BytesIO(blank_bytes)).pages[1:]:
            writer.add_page(p)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


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
