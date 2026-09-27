"""Thermal statement slip (Thermal Statement Printing, build step 1): layout, computed totals, NULL formatting, the ESC/POS job."""
import sqlite3
from datetime import date

import app
import engines.thermal_statement as ts
from conftest import make_creditor, make_statement
from engines.thermal_statement import (
    COLUMNS, DASH, build_job, build_slip, estimate_lines, fmt_money, fmt_text, get_printer_config, render_text, send_job, statement_totals,
)


def _batch(db):
    creditor_id = make_creditor(db, name="ACME Tires")
    sid = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 9, 1), date(2026, 10, 1), 1000.0, [], sid)
    app.add_invoice(creditor_id, "INV-2", date(2026, 9, 2), date(2026, 10, 2), 2500.5, [], sid)
    app.add_credit_memo(sid, "CM-1", date(2026, 9, 3), 200.0, "damaged")
    app.issue_check(creditor_id, 3300.5, date(2026, 10, 1), statement_id=sid)
    return sid


def test_totals_are_computed_from_the_lines_not_the_stored_total(db):
    sid = _batch(db)
    with sqlite3.connect(db) as conn:                              # a wrong stored total must not reach the slip
        conn.execute("DROP TRIGGER IF EXISTS trg_invoices_au_recompute_total")
        conn.execute("UPDATE order_statements SET total_amount = 999999 WHERE statement_id = ?", (sid,))
    slip = build_slip(sid, "FILE")
    t = slip.totals
    assert (t["gross"], t["memos"], t["net"], t["checks"], t["balance"]) == (3500.5, 200.0, 3300.5, 3300.5, 0.0)
    text = render_text(slip)
    assert "NET AMOUNT DUE" in text and "PHP 3,300.50" in text and "999,999" not in text


def test_no_line_is_wider_than_48_columns_and_copy_labels_and_signature(db):
    sid = _batch(db)
    file_text, supplier_text = render_text(build_slip(sid, "FILE")), render_text(build_slip(sid, "SUPPLIER"))
    for text in (file_text, supplier_text):
        assert max(len(line) for line in text.split("\n")) <= COLUMNS
        assert "None" not in text and "₱" not in text
    assert "FILE COPY" in file_text and "Received by:" in file_text and "Date:" in file_text
    assert "SUPPLIER COPY" in supplier_text and "Received by:" not in supplier_text


def test_reprint_and_revised_headers(db):
    sid = _batch(db)
    assert "REPRINT - Sep 27, 2026" in render_text(build_slip(sid, "FILE", print_type="REPRINT", reprint_date="2026-09-27"))
    assert "REVISED v3 – supersedes v2" in render_text(build_slip(sid, "FILE", print_type="REVISED", version=3))


def test_missing_values_print_a_dash_never_none_or_a_false_zero(monkeypatch):
    data = {
        "statement": {"statement_ref": "OS 0001", "creditor_name": None, "status": "Draft"},
        "invoices": [{"doc_ref": "INV-9", "invoice_date": "2026-09-01", "due_date": None, "amount": None, "status": "Unpaid", "display_status": "Unpaid"},
                     {"doc_ref": "INV-10", "invoice_date": "2026-09-01", "due_date": "2026-10-01", "amount": 500.0, "status": "Unpaid", "display_status": "Unpaid"}],
        "memos": [], "checks": [], "payment_ledger": [],
    }
    t = statement_totals(data)
    assert t["gross"] is None and t["net"] is None and t["balance"] is None and t["missing_amounts"] == 1
    monkeypatch.setattr(ts, "_fetch_batch_data", lambda *a, **k: data)
    text = render_text(build_slip(1, "SUPPLIER"))
    assert "None" not in text
    inv9 = next(line for line in text.split("\n") if line.startswith("INV-9"))
    assert inv9.rstrip().endswith(DASH) and "0.00" not in inv9                        # the missing amount is a dash, not 0.00
    assert next(line for line in text.split("\n") if line.startswith("NET AMOUNT DUE")).rstrip().endswith(DASH)
    assert "Supplier: " + DASH in text and inv9.split()[1] == DASH                              # missing due date -> dash
    assert "2026-09-01" not in text and "Sep 01, 2026" not in text                              # no received date on the slip
    assert fmt_text(None) == fmt_text("") == fmt_text(float("nan")) == DASH and fmt_money(None) == DASH and fmt_money(0) == "PHP 0.00"


def test_cancelled_items_are_left_off_the_slip_so_every_line_adds_up(monkeypatch):
    data = {
        "statement": {"statement_ref": "OS 0002", "creditor_name": "ACME", "status": "Draft"},
        "invoices": [{"doc_ref": "A", "invoice_date": "2026-09-01", "due_date": "2026-10-01", "amount": 100.0, "status": "Unpaid", "display_status": "Unpaid"},
                     {"doc_ref": "B", "invoice_date": "2026-09-01", "due_date": "2026-10-01", "amount": 50.0, "status": "Cancelled", "display_status": "Cancelled"}],
        "memos": [], "checks": [{"check_number": "3990000001", "check_date": "2026-10-01", "amount": 70.0, "status": "Cancelled"}], "payment_ledger": [],
    }
    monkeypatch.setattr(ts, "_fetch_batch_data", lambda *a, **k: data)
    slip = build_slip(1, "FILE")
    assert slip.totals["gross"] == 100.0 and slip.totals["checks"] == 0 and slip.totals["balance"] == 100.0
    text = render_text(slip)
    lines = text.splitlines()
    assert not any(line.startswith(("B ", "3990000001")) for line in lines)   # cancelled invoice / check: only in the A4 copy (QR)
    assert "INVOICES (1)" in lines and "CHECKS (0)" in lines and "Cancelled" not in text


def test_the_escpos_job_initializes_prints_the_qr_feeds_and_cuts_once_per_copy(db):
    sid = _batch(db)
    link = "https://drive.google.com/file/d/abc123/view"
    job = build_job([build_slip(sid, "FILE", qr_link=link), build_slip(sid, "SUPPLIER", qr_link=link)])
    assert job.startswith(b"\x1b@")                                   # ESC @
    assert job.count(b"\x1b@") == 2 and job.count(b"\x1dV") == 2       # initialize + one partial cut per copy
    assert job.count(link.encode()) == 2 and b"\x1d(k" in job          # native QR with the link, once per copy
    assert job.split(b"\x1dV")[0].endswith(b"\x1bd\x06")              # feed (ESC d 6) right before the (first) cut
    no_qr = build_job([build_slip(sid, "FILE")])
    assert b"\x1d(k" not in no_qr
    assert estimate_lines(build_slip(sid, "FILE", qr_link=link)) > estimate_lines(build_slip(sid, "FILE"))


def test_send_job_writes_in_chunks():
    class Fake:
        def __init__(self):
            self.chunks = []

        def _raw(self, data):
            self.chunks.append(data)

    fake = Fake()
    send_job(b"x" * 2500, fake, chunk_bytes=1024)
    assert [len(c) for c in fake.chunks] == [1024, 1024, 452]


def test_printer_settings_come_from_the_settings_table_with_defaults(db):
    cfg = get_printer_config()
    assert cfg["thermal_backend"] == "dummy" and cfg["thermal_bt_line_threshold"] == 60 and cfg["thermal_network_port"] == 9100
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO settings (key, value, category) VALUES ('thermal_bt_line_threshold', '75', 'thermal_printer')")
    assert get_printer_config()["thermal_bt_line_threshold"] == 75



def test_the_slip_is_one_line_per_item_with_no_status_lines_and_points_to_the_qr(db):
    sid = _batch(db)
    text = render_text(build_slip(sid, "SUPPLIER", qr_link="https://drive.google.com/file/d/abc/view"))
    for word in ("No check yet", "Check issued", "Partly covered", "damaged"):          # statuses and the memo reason are in the A4 copy
        assert word not in text
    lines = [line.strip() for line in text.splitlines()]
    assert lines.index("Scan for the full statement:") < lines.index("[ QR ]")
