"""PDF backend smoke tests.

Verifies the docx backend produces bytes with snapshot data filled in.
The fillable_pdf backend is skipped unless a fillable source PDF is
present (HAZREQ_FILLABLE_PDF_PATH).
"""

from __future__ import annotations

import os
import re
import zipfile

import pytest


def _docx_text(b: bytes) -> str:
    with zipfile.ZipFile.__call__(__import__("io").BytesIO(b)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    text = re.sub(r"<[^>]+>", " ", xml)
    return re.sub(r"\s+", " ", text)


def test_docx_backend_fills_snapshot(client):
    from app.services.pdf import LineSnapshot, RequestSnapshot, _render_docx_bytes

    snap = RequestSnapshot(
        id=1,
        name="John Doe",
        workcenter="N43",
        lpo="LPO Smith",
        location="LCU MAIN BASE",
        base_location="Yokose",
        datetime="2026-05-01 10:30",
        lines=[
            LineSnapshot(spmig="SPMIG-1", nomenclature="Item A", niin="111", qty="2"),
            LineSnapshot(spmig="SPMIG-2", nomenclature="Item B", niin="222", qty="1"),
        ],
    )
    b = _render_docx_bytes(snap)
    text = _docx_text(b)
    assert "John Doe" in text
    assert "N43" in text
    assert "Item A" in text
    assert "Item B" in text
    # 24h date format
    assert "2026-05-01 10:30" in text


def test_docx_backend_handles_many_lines(client):
    from app.services.pdf import LineSnapshot, RequestSnapshot, _render_docx_bytes

    lines = [
        LineSnapshot(spmig=f"SPMIG-{i:04d}", nomenclature=f"Item {i}", niin=f"{i:07d}", qty="1")
        for i in range(1, 36)
    ]
    snap = RequestSnapshot(
        id=99, name="N", workcenter="W", lpo="L", location="X", base_location="",
        datetime="2026-05-01 09:00", lines=lines,
    )
    b = _render_docx_bytes(snap)
    text = _docx_text(b)
    # First and last line both present; loop expanded fully.
    assert "Item 1 " in text
    assert "Item 35" in text


def _pdf_text(b: bytes) -> str:
    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(b))
    return " ".join(page.extract_text() or "" for page in reader.pages)


def _pdf_pages(b: bytes) -> int:
    import io

    from pypdf import PdfReader

    return len(PdfReader(io.BytesIO(b)).pages)


def test_overlay_backend_fills_form_over_blank(client):
    """Pure-Python overlay onto the shipped blank chit — no LibreOffice."""
    from app.config import settings
    from app.services.pdf import LineSnapshot, RequestSnapshot, _render_overlay_pdf

    assert settings.overlay_pdf_path.exists(), "blank chit PDF should ship in the repo"
    snap = RequestSnapshot(
        id=1, name="SN J. DOE", workcenter="ENG-2", lpo="PO1 SMITH",
        location="BLDG 7", base_location="Yokose", datetime="2026-05-29 09:30",
        lines=[
            LineSnapshot(spmig="M0001", nomenclature="CLEANER", niin="001234567", qty="2"),
            LineSnapshot(spmig="G0102", nomenclature="GREASE", niin="034567890", qty="1"),
        ],
    )
    b = _render_overlay_pdf(snap)
    assert b.startswith(b"%PDF")
    # one line-item page + the form's trailing signature page
    assert _pdf_pages(b) == 2
    text = _pdf_text(b)
    for needle in ("SN J. DOE", "ENG-2", "PO1 SMITH", "2026-05-29 09:30", "001234567"):
        assert needle in text, needle


def test_overlay_backend_paginates_overflow(client):
    from app.services.pdf import LineSnapshot, RequestSnapshot, _render_overlay_pdf

    lines = [
        LineSnapshot(spmig=f"S{i:04d}", nomenclature=f"ITEM-{i}", niin=f"{i:09d}", qty="1")
        for i in range(40)
    ]
    snap = RequestSnapshot(
        id=2, name="N", workcenter="W", lpo="L", location="X", base_location="LCU Main Base",
        datetime="2026-05-29 09:00", lines=lines,
    )
    b = _render_overlay_pdf(snap)
    # page 1 + continuation page(s) + the trailing signature page
    assert _pdf_pages(b) >= 3
    text = _pdf_text(b)
    assert "ITEM-0" in text and "ITEM-39" in text  # first and last rows present
    assert "RETURNING HAZMAT" in text              # signature page appended last


@pytest.mark.skipif(
    not os.environ.get("HAZREQ_FILLABLE_PDF_PATH"),
    reason="No fillable PDF source provided",
)
def test_fillable_pdf_backend_runs(monkeypatch, client):
    monkeypatch.setenv("HAZREQ_PDF_BACKEND", "fillable_pdf")
    # Force settings reload.
    import importlib
    from app import config as cfg
    importlib.reload(cfg)
    from app.services import pdf as pdf_service
    importlib.reload(pdf_service)

    snap = pdf_service.RequestSnapshot(
        id=1, name="X", workcenter="W", lpo="L", location="Loc", base_location="",
        datetime="2026-05-01 10:30", lines=[],
    )
    b = pdf_service.render_pdf_bytes(snap)
    assert b.startswith(b"%PDF")
