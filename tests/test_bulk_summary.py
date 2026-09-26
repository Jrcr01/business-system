"""Cover page for a bulk statement print (2026-09-27): which order statements are in the combined PDF."""
import io
from datetime import date

import pdfplumber

import app
from conftest import make_creditor
from engines.batch_statement_service import bulk_summary_rows, generate_bulk_summary_pdf


def _batch(db, ref, supplier, n_invoices, n_checks):
    import sqlite3
    creditor_id = make_creditor(db, name=supplier)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO order_statements (creditor_id, statement_ref, total_amount, status) VALUES (?, ?, 0, 'Draft')", (creditor_id, ref))
        sid = conn.execute("SELECT statement_id FROM order_statements WHERE statement_ref = ?", (ref,)).fetchone()[0]
    for i in range(n_invoices):
        app.add_invoice(creditor_id, f"{ref}-INV-{i}", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], sid)
    for i in range(n_checks):
        app.issue_check(creditor_id, 1000.0, date(2026, 3, 1 + i), statement_id=sid)
    return sid


def test_summary_rows_count_invoices_checks_and_show_the_stage(db):
    a = _batch(db, "2026 - OS 0001", "Alpha Co", 3, 3)
    b = _batch(db, "2026 - OS 0002", "Beta Co", 2, 0)

    rows = bulk_summary_rows([a, b])

    assert [(r["ref"], r["supplier"], r["stage"], r["invoices"], r["checks"]) for r in rows] == [
        ("2026 - OS 0001", "Alpha Co", "Parked", 3, 3), ("2026 - OS 0002", "Beta Co", "Draft", 2, 0)]
    assert rows[0]["net_total"] == 3000.0 and rows[0]["checks_total"] == 3000.0


def test_summary_pdf_lists_every_batch_and_the_totals(db):
    a = _batch(db, "2026 - OS 0001", "Alpha Co", 3, 3)
    b = _batch(db, "2026 - OS 0002", "Beta Co", 2, 0)

    pdf = generate_bulk_summary_pdf(bulk_summary_rows([a, b]), "Since last check print (2026-09-27)", "Batch Statement (A4) · both copies")

    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        text = doc.pages[0].extract_text()
    assert "Bulk print summary".upper() in text.upper()
    assert "2026 - OS 0001" in text and "Alpha Co" in text and "2026 - OS 0002" in text and "Beta Co" in text
    assert "2 order statement(s)" in text and "Since last check print" in text
    assert "TOTAL" in text and "5,000.00" in text        # 3000 + 2000 net
