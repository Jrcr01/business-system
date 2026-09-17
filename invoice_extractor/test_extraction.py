"""Functional tests for invoice_extraction_service.py — run directly with
python, not pytest (matches this project's existing test-script convention).

1. extract_text_from_file() on a synthetic digital PDF and a synthetic scanned
   image (rendered text -> PNG -> OCR'd back), per the spec's unit-test ask.
2. Full pipeline (annotate first invoice's boxes -> learn_pattern_from_first_invoice
   -> process_batch) across 5 synthetic invoices sharing one layout, per the
   spec's integration-test ask, then a CSV round-trip check.
"""
import io
import os
import shutil
import sys
import tempfile

import pandas as pd
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas as rl_canvas

sys.path.insert(0, os.path.dirname(__file__))
from invoice_extraction_service import (
    extract_text_from_file,
    learn_pattern_from_first_invoice,
    process_batch,
    FIELDS,
)

FAILURES = []


def check(label, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def make_invoice_pdf(path, inv_no, inv_date, amount):
    """Same layout every time (fixed y-positions), varying values — the
    "single supplier template repeated per batch" case Phase 7 targets."""
    c = rl_canvas.Canvas(path, pagesize=(400, 250))
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, 210, "ACME TRADING CORP")
    c.setFont("Helvetica", 10)
    c.drawString(40, 180, f"Invoice No: {inv_no}")
    c.drawString(40, 160, f"Date: {inv_date}")
    c.drawString(40, 100, "Description ......... Qty ... Price")
    c.drawString(40, 80, "Widgets ............. 10 .... 100.00")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, 50, f"Total Amount Due: {amount}")
    c.showPage()
    c.save()


def find_word_box(page, target_text):
    """Test helper standing in for the UI's click-drag: finds the pixel box of
    a specific word (or the first word of a phrase) already known to be in the
    synthetic PDF, since there's no real mouse in a script."""
    for w in page["words"]:
        if w["text"] == target_text:
            return (w["x0"] - 2, w["y0"] - 2, w["x1"] + 2, w["y1"] + 2)
    raise AssertionError(f"word {target_text!r} not found on page")


def box_spanning(page, start_text, end_text, pad=2):
    """Box covering from the start word through the end word on the same line
    — e.g. spanning 'INV-2024-001' through itself, or a whole value phrase."""
    b1 = find_word_box(page, start_text)
    b2 = find_word_box(page, end_text)
    return (min(b1[0], b2[0]) - pad, min(b1[1], b2[1]) - pad, max(b1[2], b2[2]) + pad, max(b1[3], b2[3]) + pad)


def test_pdf_extraction(tmpdir):
    path = os.path.join(tmpdir, "sample.pdf")
    make_invoice_pdf(path, "INV-2024-001", "2026-01-15", "1,234.56")
    doc = extract_text_from_file(path)
    check("PDF: file_type == 'pdf'", doc["file_type"] == "pdf")
    check("PDF: no warning on a normal digital PDF", doc["warning"] is None, str(doc["warning"]))
    check("PDF: text contains invoice number", "INV-2024-001" in doc["text"])
    check("PDF: text contains amount", "1,234.56" in doc["text"])
    check("PDF: pages non-empty with word geometry", bool(doc["pages"]) and bool(doc["pages"][0]["words"]))
    return doc


def test_image_extraction(tmpdir):
    """Renders text onto a blank image (simulating a 'scanned' invoice) and
    runs it back through pytesseract — a real OCR round trip, not a mock."""
    img = Image.new("RGB", (900, 500), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except Exception:
        font = ImageFont.load_default()
    draw.text((40, 40), "Invoice No: INV-2024-777", fill="black", font=font)
    draw.text((40, 100), "Date: 2026-02-20", fill="black", font=font)
    draw.text((40, 160), "Total Amount Due: 999.00", fill="black", font=font)
    path = os.path.join(tmpdir, "sample.png")
    img.save(path)

    doc = extract_text_from_file(path)
    check("Image: file_type == 'image'", doc["file_type"] == "image")
    if doc["warning"]:
        print(f"    (OCR warning, environment-dependent: {doc['warning']})")
    else:
        check("Image: OCR found some text", len(doc["text"]) > 0)
        check("Image: word geometry present", bool(doc["pages"][0]["words"]))
    return doc


def test_batch_pipeline(tmpdir):
    invoices = [
        ("INV-2024-001", "2026-01-15", "1,234.56"),
        ("INV-2024-002", "2026-01-16", "2,000.00"),
        ("INV-2024-003", "2026-01-17", "500.25"),
        ("INV-2024-004", "2026-01-18", "10,000.00"),
        ("INV-2024-005", "2026-01-19", "75.00"),
    ]
    paths = []
    for i, (no, dt, amt) in enumerate(invoices):
        p = os.path.join(tmpdir, f"invoice_{i}.pdf")
        make_invoice_pdf(p, no, dt, amt)
        paths.append(p)

    first_doc = extract_text_from_file(paths[0])
    first_page = first_doc["pages"][0]

    boxes = {
        "invoice_number": find_word_box(first_page, "INV-2024-001"),
        "invoice_date": find_word_box(first_page, "2026-01-15"),
        "total_amount": find_word_box(first_page, "1,234.56"),
    }
    pattern = learn_pattern_from_first_invoice(boxes, first_page)
    check("Pattern: has all 3 fields", set(pattern.keys()) == set(FIELDS))
    check(
        "Pattern: invoice_number keywords picked up label words",
        any(k.lower() in ("invoice", "no") for k in pattern["invoice_number"]["keywords"]),
        str(pattern["invoice_number"]["keywords"]),
    )
    check(
        "Pattern: total_amount keywords picked up 'Total'/'Amount'/'Due'",
        any(k.lower() in ("total", "amount", "due") for k in pattern["total_amount"]["keywords"]),
        str(pattern["total_amount"]["keywords"]),
    )

    progress_calls = []
    results = process_batch(
        paths, pattern,
        first_invoice_values={"invoice_number": "INV-2024-001", "invoice_date": "2026-01-15", "total_amount": "1234.56"},
        on_progress=lambda done, total, name: progress_calls.append((done, total, name)),
    )
    check("Batch: one row per file", len(results) == len(paths))
    check("Batch: progress callback fired", len(progress_calls) == len(paths) + 1)
    check("Batch: first row is 'manual'", results[0]["extracted_by"] == "manual")
    check("Batch: rows 2-5 are 'auto'", all(r["extracted_by"] == "auto" for r in results[1:]))

    expected = invoices[1:]
    for row, (no, dt, amt) in zip(results[1:], expected):
        check(f"Auto-extract invoice_number for {no}", row["invoice_number"] == no, row["invoice_number"])
        check(f"Auto-extract invoice_date for {no}", row["invoice_date"] == dt, row["invoice_date"])
        check(f"Auto-extract total_amount for {no}", row["total_amount"] == amt.replace(",", ""), row["total_amount"])

    df = pd.DataFrame(results, columns=["filename", "invoice_number", "invoice_date", "total_amount", "extracted_by", "reviewed_by", "notes"])
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    reloaded = pd.read_csv(io.BytesIO(csv_bytes))
    check("CSV: round-trips with correct columns", list(reloaded.columns) == list(df.columns))
    check("CSV: round-trips with correct row count", len(reloaded) == len(df))
    return results


def main():
    tmpdir = tempfile.mkdtemp(prefix="invoice_extractor_test_")
    try:
        test_pdf_extraction(tmpdir)
        test_image_extraction(tmpdir)
        test_batch_pipeline(tmpdir)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        sys.exit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
