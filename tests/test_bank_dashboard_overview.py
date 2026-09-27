"""Reconciliation plan phase 2 (2026-09-28): "due today = still owed" in the Dashboard total and the combined summary; stale checks ignore
checks the bank cleared; the Dashboard's Bank and cash panel figures; the overall report's Bank and cash section (full / part / no
statement) and its PDF page with contents page numbers that match."""
import sqlite3
from datetime import date

import pypdfium2 as pdfium

import app
from conftest import make_creditor, make_statement
from engines.cash_insights_service import dashboard_bank_summary, period_bank_summary
from engines.dashboard_kpi_service import get_stale_checks, get_total_ap_outstanding
from engines.loan_note_rules import create_loan_note
from engines.overview_pdf_service import generate_overview_pdf
from engines.overview_report_service import bank_note, bank_rows, get_overview
from engines.reports_service import get_combined_ap_summary
from test_bank_recon_v2 import STATEMENT, _setup


def test_due_today_counts_as_still_owed(db):
    today = date.today()
    lender = make_creditor(db, name="Lender", creditor_type="LENDER")
    ok, _, lnid = create_loan_note(lender, date(2026, 1, 1), 5000.0, 0.0)
    assert ok
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE loan_notes SET status = 'Active', total_payable = 5000 WHERE loan_note_id = ?", (lnid,))
    app.issue_check(lender, 2000.0, today, loan_note_id=lnid)                       # dated today: not paid yet
    bank = make_creditor(db, name="Metrobank", creditor_type="BANK_LOAN")
    with sqlite3.connect(db) as conn:
        loan = conn.execute("INSERT INTO bank_loans (creditor_id, loan_ref) VALUES (?, 'BL-1')", (bank,)).lastrowid
        conn.execute("INSERT INTO bank_loan_installments (bank_loan_id, installment_no, due_date, amount) VALUES (?, 1, ?, 700)", (loan, today.isoformat()))
    summary = get_combined_ap_summary().set_index("track")["outstanding"]
    assert summary["Private Lender (Loan Notes)"] == 5000.0          # today's check is not subtracted yet
    assert summary["Bank Loan"] == 700.0                             # due today is still owed
    assert get_total_ap_outstanding() == 2000.0 + 700.0


def test_stale_checks_leave_out_what_the_bank_cleared(db):
    sup = make_creditor(db)
    sid = make_statement(db, sup)
    app.add_invoice(sup, "INV-1", date(2024, 12, 1), date(2025, 1, 1), 350.0, [], sid)
    app.issue_check(sup, 100.0, date(2025, 1, 1), statement_id=sid)
    _, _, old2 = app.issue_check(sup, 250.0, date(2025, 1, 2), statement_id=sid)
    assert get_stale_checks(180) == {"n": 2, "total": 350.0}
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE checks_issued SET bank_cleared_date = '2025-01-10' WHERE check_id = ?", (old2,))
    assert get_stale_checks(180) == {"n": 1, "total": 100.0}


def test_dashboard_bank_panel(db, monkeypatch, tmp_path):
    assert dashboard_bank_summary() is None                          # nothing imported yet
    _setup(db, monkeypatch, tmp_path)
    closing = 1_000_000.0 - sum(x[3] for x in STATEMENT) + sum(x[4] for x in STATEMENT)
    s = dashboard_bank_summary(today=date(2026, 10, 9))
    assert abs(s["balance"] - closing) < 0.005 and s["as_of"] == date(2026, 10, 8) and s["age_days"] == 1
    # numbered active checks dated Sep 20 - Oct 8: S, A, E, T, W (U has no number); the bank cleared A
    assert (s["checks_in_period"], s["checks_cleared"], round(s["pct_cleared"])) == (5, 1, 20)
    assert (s["float_count"], s["float_amount"]) == (4, 4800.0) and s["open_items"] == 6
    assert s["lowest"] is not None
    assert dashboard_bank_summary(today=date(2026, 12, 1))["lowest"] is None       # balance too old to forecast from


def test_period_bank_summary_full_part_none(db, monkeypatch, tmp_path):
    _setup(db, monkeypatch, tmp_path)                                # statement covers Sep 20 - Oct 8, 2026
    b = period_bank_summary(date(2026, 10, 5), date(2026, 10, 8), date(2026, 10, 1), date(2026, 10, 4))
    assert b["covered"] == "full" and b["as_of"] == date(2026, 10, 8)
    assert b["money_in"] == 20000.0 and b["money_in_prev"] == 0.0
    assert (b["cleared_n"], b["cleared_amount"]) == (1, 1000.0)                  # A
    assert (b["issued_n"], b["issued_amount"]) == (0, 0.0)                      # no check dated Oct 5-8
    assert (b["float_n"], b["float_amount"]) == (4, 4800.0) and (b["bounced_n"], b["bounced_amount"]) == (1, 300.0)
    assert b["open_items"] == 6
    oct_ = period_bank_summary(date(2026, 10, 1), date(2026, 10, 31), date(2026, 9, 1), date(2026, 9, 30))
    assert oct_["covered"] == "part" and oct_["money_in_prev"] is None           # September is only partly covered
    assert (oct_["issued_n"], oct_["issued_amount"]) == (5, 6100.0)             # A, E, T, W, U
    assert "only part" in bank_note(oct_) and len(bank_rows(oct_)) == 7
    dec = period_bank_summary(date(2026, 12, 1), date(2026, 12, 31), date(2026, 11, 1), date(2026, 11, 30))
    assert dec["covered"] == "none" and bank_rows(dec) == [] and "No imported bank statement" in bank_note(dec)


def _pdf_text(pdf: bytes) -> list[str]:
    d = pdfium.PdfDocument(pdf)
    return [" ".join(d[i].get_textpage().get_text_range().split()) for i in range(len(d))]


def test_overview_bank_section_and_contents_pages(db, monkeypatch, tmp_path):
    _setup(db, monkeypatch, tmp_path)
    ov = get_overview("Month", date(2026, 10, 15))
    assert ov["bank"]["covered"] == "part"
    assert any(h.startswith("Bank: PHP 20,000.00 came in") and "1 customer check(s) bounced" in h for h in ov["highlights"])
    for short in (True, False):
        pages = _pdf_text(generate_overview_pdf(ov, short=short))
        cover = pages[0]
        bank_page = next(i for i, t in enumerate(pages) if "Bank and cash Measure Figure" in t) + 1
        assert f"Bank and cash {bank_page}" in cover
        assert "Money in (deposits and transfers) PHP 20,000.00" in pages[bank_page - 1]
        if not short:
            detail_page = next(i for i, t in enumerate(pages) if "Where the money went" in t) + 1
            assert f"coming up {detail_page}" in cover
    none = get_overview("Month", date(2026, 12, 15))
    assert none["bank"]["covered"] == "none" and not any(h.startswith("Bank:") for h in none["highlights"])
    assert any("No imported bank statement covers this period" in t for t in _pdf_text(generate_overview_pdf(none, short=True)))
