"""Generates data/templates/hazmat_chit.docx programmatically.

This is a placeholder template — clean and complete, but not pixel-
identical to the source form. When a definitive form (PDF or .docx) is
provided, replace this with a hand-prepared docxtpl version that
preserves the exact original layout. The rest of the pipeline stays
the same.

Run:
    PYTHONPATH=. .venv/bin/python scripts/prepare_template.py
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from lxml import etree

from app.config import settings


def set_repeat_table_header(row) -> None:
    """Mark a row as a repeating header (Word: 'Repeat as header row at top of each page')."""
    tr_pr = row._tr.get_or_add_trPr()
    th = etree.SubElement(tr_pr, qn("w:tblHeader"))
    th.set(qn("w:val"), "true")


def add_kv(table, row, label, placeholder):
    cells = table.rows[row].cells
    cells[0].text = label
    cells[1].text = placeholder
    for c in cells:
        for p in c.paragraphs:
            for run in p.runs:
                if c is cells[0]:
                    run.bold = True


def main() -> None:
    out = settings.template_path
    out.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()
    section = doc.sections[0]
    section.top_margin = Cm(1.5)
    section.bottom_margin = Cm(1.5)
    section.left_margin = Cm(1.2)
    section.right_margin = Cm(1.2)

    # Title
    h = doc.add_paragraph()
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = h.add_run("HAZMAT ISSUE CHIT")
    run.bold = True
    run.font.size = Pt(20)

    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub_run = sub.add_run(
        "HAZMAT ISSUE: 0830–1030  |  HAZMAT RETURN: 1330–1530  |  HAZWASTE DROP-OFF: 1330–1530 (call 4520)"
    )
    sub_run.font.size = Pt(8)

    # Header fields table (2 columns, 5 rows of label/value)
    header = doc.add_table(rows=5, cols=2)
    header.autofit = False
    header.columns[0].width = Cm(4.5)
    header.columns[1].width = Cm(13.5)
    add_kv(header, 0, "HAZMAT LOCATION:", "{{ location }}")
    add_kv(header, 1, "NAME:", "{{ name }}")
    add_kv(header, 2, "DATE / TIME:", "{{ datetime }}")
    add_kv(header, 3, "WORKCENTER:", "{{ workcenter }}")
    add_kv(header, 4, "LPO:", "{{ lpo }}")
    for r in header.rows:
        for c in r.cells:
            c.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

    # Apply borders to the header table
    _apply_borders(header)

    doc.add_paragraph()

    # Line items table — 4 columns, header row + control rows + data row.
    # docxtpl needs {%tr for%} and {%tr endfor %} in their OWN rows; the
    # row containing the marker is consumed (replaced with the bare jinja
    # tag), wrapping the data row in between.
    items = doc.add_table(rows=4, cols=4)
    items.autofit = False
    items.columns[0].width = Cm(3.5)
    items.columns[1].width = Cm(9.5)
    items.columns[2].width = Cm(3.0)
    items.columns[3].width = Cm(2.0)

    hdr_cells = items.rows[0].cells
    hdr_cells[0].text = "SPMIG / SPIN"
    hdr_cells[1].text = "NOMENCLATURE"
    hdr_cells[2].text = "NIIN"
    hdr_cells[3].text = "QTY"
    for c in hdr_cells:
        for p in c.paragraphs:
            for run in p.runs:
                run.bold = True
        c.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    set_repeat_table_header(items.rows[0])

    # Row 1 — for-loop marker (entire row is consumed)
    items.rows[1].cells[0].text = "{%tr for line in lines %}"

    # Row 2 — actual data row (this is the one that repeats)
    data_cells = items.rows[2].cells
    data_cells[0].text = "{{ line.spmig }}"
    data_cells[1].text = "{{ line.nomenclature }}"
    data_cells[2].text = "{{ line.niin }}"
    data_cells[3].text = "{{ line.qty }}"

    # Row 3 — endfor marker (entire row is consumed)
    items.rows[3].cells[0].text = "{%tr endfor %}"

    _apply_borders(items)

    # Signatures
    doc.add_paragraph()
    sig = doc.add_table(rows=2, cols=2)
    sig.rows[0].cells[0].text = "MAINTENANCE REQUESTOR"
    sig.rows[0].cells[1].text = "HAZMAT ISSUING PERSONNEL"
    sig.rows[1].cells[0].text = "Signature: ________________________"
    sig.rows[1].cells[1].text = "Signature: ________________________"
    for r in sig.rows:
        for c in r.cells:
            for p in c.paragraphs:
                for run in p.runs:
                    if "Signature" not in run.text:
                        run.bold = True

    doc.add_paragraph()
    notes = doc.add_paragraph()
    notes.add_run("NOTES\n").bold = True
    notes.add_run(
        "• All requests shall be submitted at least 24HR in advance, due 1500 the day before.\n"
        "• All hazardous materials must be turned in daily 1330–1530.\n"
        "• All HAZWASTE turned in to S-3 for inspection prior to disposal."
    )
    for run in notes.runs:
        run.font.size = Pt(8)

    doc.save(str(out))
    print(f"Wrote {out}")


def _apply_borders(table) -> None:
    tbl = table._tbl
    tbl_pr = tbl.tblPr
    borders = etree.SubElement(tbl_pr, qn("w:tblBorders"))
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        b = etree.SubElement(borders, qn(f"w:{edge}"))
        b.set(qn("w:val"), "single")
        b.set(qn("w:sz"), "4")
        b.set(qn("w:space"), "0")
        b.set(qn("w:color"), "000000")


if __name__ == "__main__":
    main()
