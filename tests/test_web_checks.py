"""Pay suppliers -> Daily payables in Django (owner, 2026-10-08 / 2026-10-09): the checklist of every track -- supplier and lender checks, bank loans,
cards -- day by day; an item stays payable until it is paid (the bank shows it, or it is ticked here); many are ticked at once with one date and note.
The one-time cut-off is the System admin's. The rules themselves are tested in test_clearing_rule.py."""
import sqlite3
from datetime import date, timedelta

from conftest import fetchone, make_creditor
from engines.bank_loan_service import add_bank_loan_installment, create_bank_loan
from engines.clock import manila_today
from tests.test_clearing_rule import _issued
from tests.test_web_d0 import _admin, _sign_in_new

TODAY = manila_today()


def _loan_and_card(db, day):
    bank = make_creditor(db, name="BDO", creditor_type="BANK_LOAN")
    blid = create_bank_loan(bank, "L-77", "A", date(2026, 1, 1), date(2028, 1, 1), None, 500000.0, "ADA")
    add_bank_loan_installment(blid, day, 20000.0)
    issuer = make_creditor(db, name="RCBC", creditor_type="CREDIT_CARD")
    with sqlite3.connect(db) as conn:
        card = conn.execute("INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit) VALUES (?, 'Gold', '1234', 1)", (issuer,)).lastrowid
        csid = conn.execute("INSERT INTO cc_statements (card_id, due_date, total_amount_due, status, paid_confirmed) VALUES (?, ?, 3000, 'Unpaid', 0)", (card, str(day))).lastrowid
        iid = conn.execute("SELECT installment_id FROM bank_loan_installments WHERE bank_loan_id = ?", (blid,)).fetchone()[0]
    return iid, csid


def test_the_checklist_shows_every_track_and_ticks_many_at_once(web, db):
    _admin(web)
    sid, cid = _issued(db)                                                   # a supplier check dated 5 days ago
    iid, csid = _loan_and_card(db, TODAY - timedelta(days=3))                # a bank-loan payment and a card statement, both overdue
    page = web.get("/checks/").content.decode()
    assert "Daily payables" in page and "0004471" in page and "L-77" in page and "Gold - 1234" in page and "overdue" in page
    assert "Released" not in page
    r = web.post("/checks/", {"action": "clear_many", f"tick_check_{cid}": "1", f"tick_loan_{iid}": "1", f"tick_card_{csid}": "1",
                              "on": str(TODAY), "note": "online banking", "show": "due"})
    assert r.status_code == 302
    assert fetchone(db, "SELECT manual_cleared_at FROM checks_issued WHERE check_id = ?", (cid,))[0] == str(TODAY)
    assert fetchone(db, "SELECT status FROM invoices WHERE statement_id = ?", (sid,))[0] == "Paid"
    assert fetchone(db, "SELECT cleared_at, cleared_note FROM bank_loan_installments WHERE installment_id = ?", (iid,)) == (str(TODAY), "online banking")
    assert fetchone(db, "SELECT status FROM cc_statements WHERE cc_statement_id = ?", (csid,))[0] == "Paid"
    ticked = web.get("/checks/?show=ticked").content.decode()
    assert "0004471" in ticked and "L-77" in ticked
    web.post("/checks/", {"action": "unclear", "check_id": cid})
    web.post("/checks/", {"action": "unclear_loan", "item_id": iid})
    assert fetchone(db, "SELECT manual_cleared_at FROM checks_issued WHERE check_id = ?", (cid,))[0] is None
    assert fetchone(db, "SELECT cleared_at FROM bank_loan_installments WHERE installment_id = ?", (iid,))[0] is None


def test_a_payable_not_due_yet_is_refused_and_the_rest_still_go_through(web, db):
    _admin(web)
    _, cid = _issued(db)
    iid, _ = _loan_and_card(db, TODAY + timedelta(days=6))                   # not due yet
    assert "L-77" not in web.get("/checks/?show=due").content.decode()      # only today and overdue by default
    assert "L-77" in web.get("/checks/?show=week").content.decode()
    web.post("/checks/", {"action": "clear_many", f"tick_check_{cid}": "1", f"tick_loan_{iid}": "1", "on": str(TODAY)})
    assert fetchone(db, "SELECT manual_cleared_at FROM checks_issued WHERE check_id = ?", (cid,))[0] == str(TODAY)
    assert fetchone(db, "SELECT cleared_at FROM bank_loan_installments WHERE installment_id = ?", (iid,))[0] is None


def test_only_the_admin_sees_the_cutoff(web, db):
    _admin(web)
    _issued(db)
    assert "Cut-off" in web.get("/checks/").content.decode()
    family = _sign_in_new("family@slicks.test", "owner")
    assert "Cut-off" not in family.get("/checks/").content.decode()
    family.post("/checks/", {"action": "cutoff"})
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CLEARING_CUTOFF'")[0] == 0


def test_a_card_can_be_paid_in_part_and_each_payment_is_on_its_statement(web, db):
    # owner, 2026-10-09: "credit card payment can be recorded partially ... payments reflect post date"
    _admin(web)
    _, csid = _loan_and_card(db, TODAY - timedelta(days=2))                   # a 3,000 card statement, due 2 days ago
    web.post("/checks/", {"action": "clear_many", f"tick_card_{csid}": "1", f"amt_card_{csid}": "1,000", "on": str(TODAY - timedelta(days=1))})
    assert fetchone(db, "SELECT status, amount_paid, unpaid_balance_next FROM cc_statements WHERE cc_statement_id = ?", (csid,)) == ("Partially Paid", 1000.0, 2000.0)
    assert fetchone(db, "SELECT payment_date, amount FROM cc_payments WHERE cc_statement_id = ?", (csid,)) == (str(TODAY - timedelta(days=1)), 1000.0)
    page = web.get("/checks/").content.decode()
    assert 'name="amt_card_%d"' % csid not in page                       # paid in part: done here, the other 2,000 carries to the next statement
    assert "Gold - 1234" in web.get("/checks/?show=ticked").content.decode()
    pay_id = fetchone(db, "SELECT MAX(payment_id) FROM cc_payments")[0]
    web.post("/checks/", {"action": "unpay_card", "item_id": pay_id})
    assert fetchone(db, "SELECT status, amount_paid FROM cc_statements WHERE cc_statement_id = ?", (csid,)) == ("Unpaid", 0.0)   # taken back: owed again
    assert 'name="amt_card_%d"' % csid in web.get("/checks/").content.decode()
