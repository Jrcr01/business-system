"""Payment run page (2026-09-27): what waits at each stage of the payment cycle, and today's quick report. Read-only -- the
numbers must agree with the Dashboard and the Reports schedule, which use the same functions."""
import io
from datetime import date, timedelta

from pypdf import PdfReader

import app
from conftest import fetchone, make_creditor, make_statement
from engines.check_rules import assign_check_number
from engines.check_run_service import create_check_print_batch, mark_batch_printed
from engines.dashboard_kpi_service import get_upcoming_cash_obligations
from engines.payment_run_service import get_quick_report, get_run_stages, waiting_count
from engines.reports_service import next_banking_day
from engines.today_report_pdf import generate_today_report_pdf


def _batch_with_invoice(db, amount=5000.0, due=date(2030, 1, 31)):
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2030, 1, 1), due, amount, [], statement_id)
    return creditor_id, statement_id


def test_a_batch_moves_through_every_stage_of_the_cycle(db):
    creditor_id, statement_id = _batch_with_invoice(db)
    s = get_run_stages()
    assert list(s["needs_checks"]["statement_id"]) == [statement_id] and s["needs_checks"]["balance"].iloc[0] == 5000.0
    assert s["parked"].empty and s["unprinted"].empty and s["to_number"].empty

    _, _, check_id = app.issue_check(creditor_id, 5000.0, date(2030, 1, 31), statement_id=statement_id)
    s = get_run_stages()
    assert s["needs_checks"].empty                                   # fully covered by the parked check
    assert list(s["parked"]["check_id"]) == [check_id]

    run_id = create_check_print_batch([check_id])                    # exported to Chrysanth
    s = get_run_stages()
    assert s["parked"].empty and list(s["unprinted"]["print_batch_id"]) == [run_id]
    assert s["to_number"].empty                                      # numbers only once the run is marked printed

    mark_batch_printed(run_id, True)
    s = get_run_stages()
    assert s["unprinted"].empty
    assert list(s["to_number"]["statement_id"]) == [statement_id] and s["to_number"]["checks"].iloc[0] == 1
    assert waiting_count(s) == 1

    ok, msg = assign_check_number(check_id, None, None, "3992000001")
    assert ok, msg
    s = get_run_stages()
    assert s["to_number"].empty and waiting_count(s) == 0


def test_a_partly_covered_batch_still_needs_checks(db):
    creditor_id, statement_id = _batch_with_invoice(db, amount=8000.0)
    app.issue_check(creditor_id, 3000.0, date(2030, 1, 31), statement_id=statement_id)
    need = get_run_stages()["needs_checks"]
    assert list(need["statement_id"]) == [statement_id] and need["balance"].iloc[0] == 5000.0


def test_next_7_days_equals_the_dashboard_and_due_today_uses_the_bank_day(db):
    creditor_id, statement_id = _batch_with_invoice(db, amount=9000.0, due=date.today() + timedelta(days=3))
    app.issue_check(creditor_id, 4000.0, date.today(), statement_id=statement_id)
    app.issue_check(creditor_id, 5000.0, date.today() + timedelta(days=3), statement_id=statement_id)
    r = get_quick_report()
    assert round(r["next7_total"], 2) == round(get_upcoming_cash_obligations(7), 2) == 9000.0
    assert r["bank_day"] == next_banking_day(date.today())
    assert list(r["checks_today"]["amount"]) == [4000.0]
    if r["bank_day"] == date.today():                                # on a weekend the check dated today is paid Monday
        assert 4000.0 in list(r["due_today"]["amount"])


def test_an_invoice_past_due_with_no_check_is_overdue_until_a_check_covers_it(db):
    creditor_id, statement_id = _batch_with_invoice(db, amount=7000.0, due=date.today() - timedelta(days=2))
    od = get_quick_report()["overdue"]
    assert list(od["kind"]) == ["Supplier invoice"] and od["amount"].iloc[0] == 7000.0

    invoice_id = fetchone(db, "SELECT invoice_id FROM invoices WHERE doc_ref = 'INV-1'")[0]
    _, _, check_id = app.issue_check(creditor_id, 7000.0, date.today(), statement_id=statement_id)
    ok, msg = app.allocate_check_to_invoices(check_id, [(invoice_id, 7000.0)])
    assert ok, msg
    assert get_quick_report()["overdue"].empty


def test_the_today_pdf_is_always_one_page(db):
    creditor_id, statement_id = _batch_with_invoice(db, amount=200 * 100.0, due=date.today() + timedelta(days=1))
    for i in range(60):                                               # a very busy day: 60 checks dated today
        app.issue_check(creditor_id, 100.0, date.today(), statement_id=statement_id)
    pdf = generate_today_report_pdf(get_quick_report())
    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) == 1
    assert "more (see the Payment run page" in reader.pages[0].extract_text()
