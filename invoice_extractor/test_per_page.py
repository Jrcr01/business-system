"""Functional test for process_batch_per_page() -- the "one invoice per page"
mode, for a batch that arrives as one or more multi-page PDFs each holding
several invoices back-to-back (e.g. a combined scan), instead of one file per
invoice. Uses a real multi-page PDF (built with reportlab, multiple showPage()
calls before save()), not a mock.
"""
import os
import shutil
import sys
import tempfile

import pandas as pd
from reportlab.pdfgen import canvas as rl_canvas

sys.path.insert(0, os.path.dirname(__file__))
from invoice_extraction_service import (
    extract_text_from_file,
    learn_pattern_from_first_invoice,
    process_batch_per_page,
    FIELDS,
)

FAILURES = []


def check(label, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def add_invoice_page(c, inv_no, inv_date, amount):
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, 210, "ACME TRADING CORP")
    c.setFont("Helvetica", 10)
    c.drawString(40, 180, f"Invoice No: {inv_no}")
    c.drawString(40, 160, f"Date: {inv_date}")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, 50, f"Total Amount Due: {amount}")
    c.showPage()


def find_word_box(page, target_text):
    for w in page["words"]:
        if w["text"] == target_text:
            return (w["x0"] - 2, w["y0"] - 2, w["x1"] + 2, w["y1"] + 2)
    raise AssertionError(f"word {target_text!r} not found on page")


def main():
    tmpdir = tempfile.mkdtemp(prefix="per_page_test_")
    try:
        invoices = [
            ("INV-3001", "2026-06-01", "1,000.00"),
            ("INV-3002", "2026-06-02", "2,000.00"),
            ("INV-3003", "2026-06-03", "3,000.00"),
        ]
        combined_path = os.path.join(tmpdir, "combined_scan.pdf")
        c = rl_canvas.Canvas(combined_path, pagesize=(400, 250))
        for no, dt, amt in invoices:
            add_invoice_page(c, no, dt, amt)
        c.save()

        doc = extract_text_from_file(combined_path)
        check("Combined PDF has 3 pages", len(doc["pages"]) == 3)

        first_page = doc["pages"][0]
        boxes = {
            "invoice_number": find_word_box(first_page, "INV-3001"),
            "invoice_date": find_word_box(first_page, "2026-06-01"),
            "total_amount": find_word_box(first_page, "1,000.00"),
        }
        pattern = learn_pattern_from_first_invoice(boxes, first_page)

        progress_calls = []
        results = process_batch_per_page(
            [combined_path], pattern,
            first_invoice_values={"invoice_number": "INV-3001", "invoice_date": "2026-06-01", "total_amount": "1000.00"},
            on_progress=lambda done, total, name: progress_calls.append((done, total, name)),
        )

        check("One row per page, not per file", len(results) == 3, f"got {len(results)}")
        check("Progress callback fired once per page + done", len(progress_calls) == 4)
        check("First row filename has no page suffix collision issue", results[0]["filename"] == "combined_scan.pdf (page 1)")
        check("Second row labeled with page number", results[1]["filename"] == "combined_scan.pdf (page 2)")
        check("Third row labeled with page number", results[2]["filename"] == "combined_scan.pdf (page 3)")
        check("First row is 'manual' (the annotated reference page)", results[0]["extracted_by"] == "manual")
        check("Second and third rows are 'auto'", results[1]["extracted_by"] == "auto" and results[2]["extracted_by"] == "auto")

        for row, (no, dt, amt) in zip(results[1:], invoices[1:]):
            check(f"Auto-extract invoice_number for {no}", row["invoice_number"] == no, row["invoice_number"])
            check(f"Auto-extract invoice_date for {no}", row["invoice_date"] == dt, row["invoice_date"])
            check(f"Auto-extract total_amount for {no}", row["total_amount"] == amt.replace(",", ""), row["total_amount"])

        # Mixed batch: the multi-page combined PDF plus a normal single-page file --
        # single-page files must NOT get a "(page 1)" suffix (only multi-page ones do).
        single_path = os.path.join(tmpdir, "single_invoice.pdf")
        c2 = rl_canvas.Canvas(single_path, pagesize=(400, 250))
        add_invoice_page(c2, "INV-3004", "2026-06-04", "4,000.00")
        c2.save()

        mixed_results = process_batch_per_page(
            [combined_path, single_path], pattern,
            first_invoice_values={"invoice_number": "INV-3001", "invoice_date": "2026-06-01", "total_amount": "1000.00"},
        )
        check("Mixed batch: 3 pages + 1 single file = 4 rows", len(mixed_results) == 4, f"got {len(mixed_results)}")
        check("Single-page file keeps its plain filename (no page suffix)", mixed_results[3]["filename"] == "single_invoice.pdf", mixed_results[3]["filename"])
        check("Single-page file's invoice auto-extracted correctly", mixed_results[3]["invoice_number"] == "INV-3004", mixed_results[3]["invoice_number"])

        df = pd.DataFrame(results, columns=["filename", "invoice_number", "invoice_date", "total_amount", "extracted_by", "reviewed_by", "notes"])
        check("CSV-shape columns intact", list(df.columns) == ["filename", "invoice_number", "invoice_date", "total_amount", "extracted_by", "reviewed_by", "notes"])
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        sys.exit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
