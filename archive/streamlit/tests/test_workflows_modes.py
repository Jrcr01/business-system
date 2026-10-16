"""Every mode, tab and report of every page, opened with real records in view (2026-10-03, goal: test every workflow the system has). The page smoke test opens each page at its first
screen; this one goes into the modes and tabs where the work is done: a batch's invoices, memos and checks, a loan note's schedule and checks, a bank loan, a card, every report, every
Payables due and Bank and cash tab, and the New order form with an item in it."""
import sqlite3
from datetime import date

import pytest
from streamlit.testing.v1 import AppTest

import app
from conftest import make_creditor
from engines.acting_user import acting_as
from engines.check_rules import issue_check
from engines.statement_rules import add_credit_memo, add_invoice
from test_workflows_pages import PAGE_MODULES, _open


@pytest.fixture
def world(db):
    """A supplier with a batch (invoice, memo, a parked check), a lender with a scheduled loan note, a bank loan, a card with a statement."""
    from engines.bank_loan_service import create_bank_loan, fill_flat_schedule
    from engines.card_rules import add_cardholder, add_cc_statement, add_credit_card
    from engines.loan_note_rules import create_loan_note, promote_draft_loan_note
    from engines.loan_schedule_service import generate_schedule
    ids = {}
    ids["cid"] = cid = make_creditor(db, name="Mode Supplier", terms="30")
    ids["sid"] = sid = app.create_order_statement(cid)
    add_invoice(cid, "MD-1", date(2026, 10, 1), date(2026, 11, 1), 10000.0, [], sid)
    with acting_as("owner@example.com", "owner"):
        add_credit_memo(sid, "CM-1", date(2026, 10, 3), 500.0, "return")
        ok, msg, ids["chk"] = issue_check(cid, 4000.0, date(2026, 11, 9), statement_id=sid)
        assert ok, msg
    ids["lender"] = lender = make_creditor(db, name="Mode Lender", creditor_type="LENDER", terms="0")
    ok, msg, ids["ln"] = create_loan_note(lender, date(2026, 10, 1), 30000.0, 1.0)
    promote_draft_loan_note(ids["ln"])
    generate_schedule(ids["ln"], date(2026, 11, 1), 3)
    bank = make_creditor(db, name="Mode Bank", creditor_type="BANK_LOAN", terms="0")
    ids["bl"] = create_bank_loan(bank, "BL-MODE", "", date(2026, 9, 1), date(2027, 9, 1), None, 100000.0, "ADA")
    fill_flat_schedule(ids["bl"], date(2026, 11, 3), 3, 9000.0)
    issuer = make_creditor(db, name="Mode Card Bank", creditor_type="CREDIT_CARD", terms="0")
    holder = add_cardholder("Mode Holder", "", "")
    add_credit_card(issuer, holder, "Platinum", "1111", 50000.0, None, 10)
    card = sqlite3.connect(db).execute("SELECT card_id FROM credit_cards").fetchone()[0]
    add_cc_statement(card, date(2026, 11, 10), 0.0, 3000.0, 0.0, 0.0, "2026-10")
    return ids


def _ok(page, role="admin", **state):
    at = _open(page, role, **state)
    assert not at.exception, f"{page} {state}: {at.exception[0].value if at.exception else ''}"
    return at


@pytest.mark.parametrize("sub", ["Invoices", "Credit Memo", "Checks"])
@pytest.mark.parametrize("mode", ["Browse batches", "New batch"])
def test_order_statements_every_mode_and_tab(world, mode, sub):
    _ok("order-statements", selected_statement_id=world["sid"], os_mode=mode, os_subtab=sub)


@pytest.mark.parametrize("view", ["Checks in this batch", "Assign numbers"])
def test_order_statements_checks_views(world, view):
    _ok("order-statements", selected_statement_id=world["sid"], os_mode="Browse batches", os_subtab="Checks", **{f"os_checks_view__{world['sid']}": view})


@pytest.mark.parametrize("sub", ["Repayment Schedule", "Checks"])
@pytest.mark.parametrize("mode", ["Browse loan notes", "New loan note"])
def test_lenders_every_mode_and_tab(world, mode, sub):
    _ok("lenders", selected_loan_note_id=world["ln"], ln_mode=mode, ln_subtab=sub)


@pytest.mark.parametrize("mode", ["Browse bank loans", "New bank loan"])
def test_bank_loans_every_mode(world, mode):
    _ok("bank-loans", selected_bank_loan_id=world["bl"], bl_mode=mode)


@pytest.mark.parametrize("tab", [":material/receipt_long: Statements", ":material/credit_card: Cards & cardholders", ":material/fact_check: Missing statement check"])
def test_credit_cards_every_tab(world, tab):
    _ok("credit-cards", cc_tab=tab)


@pytest.mark.parametrize("tab", ["Week by week", "By supplier", "Past their date", "Calendar", "Everything owed"])
def test_payables_due_every_tab(world, tab):
    _ok("payables-due", pd_tab=tab)


@pytest.mark.parametrize("tab", [":material/upload_file: Import a statement", ":material/rule: Reconciliation", ":material/hourglass_top: Checks not yet cleared"])
def test_bank_and_cash_every_tab(world, tab):
    _ok("bank-cash", bank_cash_tab=tab)


def test_every_report_opens_with_data_in_the_books(world):
    from views.reports import REPORT_OPTIONS
    for report in REPORT_OPTIONS:
        _ok("reports", rpt_mode="Standard reports", report_type_picker=report)
    for view in ("Supplier history", "Supplier rundown", "Private lender", "Bank loan", "Credit card"):
        _ok("reports", rpt_mode="Standard reports", report_type_picker="Statement of account — supplier, lender, bank loan or card", rpt_soa_view=view)
    _ok("reports", rpt_mode="My reports")


@pytest.mark.parametrize("tab", ["Prices", "Add a price", "Import Excel", "Discounts"])
@pytest.mark.parametrize("show", ["This supplier", "All suppliers side by side"])
def test_price_lists_every_tab_and_view(world, tab, show):
    _ok("price-lists", pl_supplier=world["cid"], pl_tab=tab, pl_show=show)


@pytest.mark.parametrize("mode", ["Orders", "New order", "Receive", "Match invoice"])
def test_purchase_orders_every_mode(world, mode):
    _ok("purchase-orders", po_mode=mode, po_supplier=world["cid"], rcv_supplier=world["cid"])


@pytest.mark.parametrize("kind", ["Invoices / credit memos", "Credit card statement", "Loans", "Records and checkbook", "Handwritten check", "Guarantee check"])
def test_add_data_every_kind(world, kind):
    _ok("add-data", ad_kind=kind, ad_supplier=world["cid"])


@pytest.mark.parametrize("loan", ["New loan note", "Loan note installment", "New bank loan", "Bank loan schedule"])
def test_add_data_every_loan_choice(world, loan):
    _ok("add-data", ad_kind="Loans", ad_loan_what=loan)


@pytest.mark.parametrize("record", ["New creditor", "New checkbook booklet", "Spoiled check"])
def test_add_data_every_record_choice(world, record):
    _ok("add-data", ad_kind="Records and checkbook", ad_rec_what=record)


@pytest.mark.parametrize("tab", ["Numbers", "Keyboard shortcuts", "Users", "Who can do what"])
def test_settings_every_tab(world, tab):
    _ok("settings", settings_tab=tab)


@pytest.mark.parametrize("tab", ["Who can do what", "Controls", "Is main OK?"])
def test_roles_and_controls_page(world, tab):
    _ok("controls")
