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
        id=99, name="N", workcenter="W", lpo="L", location="X",
        datetime="2026-05-01 09:00", lines=lines,
    )
    b = _render_docx_bytes(snap)
    text = _docx_text(b)
    # First and last line both present; loop expanded fully.
    assert "Item 1 " in text
    assert "Item 35" in text


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
        id=1, name="X", workcenter="W", lpo="L", location="Loc",
        datetime="2026-05-01 10:30", lines=[],
    )
    b = pdf_service.render_pdf_bytes(snap)
    assert b.startswith(b"%PDF")
