"""Other payables -> Bank loans and Credit cards in Django (owner, 2026-10-09; mock-ups A and B approved). Bank loans: create, schedule (add, fill monthly,
edit, delete -- a paid row is locked), details, cancel. Credit cards: cards and cardholders, a statement from the SOA that confirms the earlier one (and flags
a difference with the payments recorded day by day), the missing-statement check."""
import sqlite3
from datetime import date, timedelta

from conftest import fetchone, make_creditor
from engines.clock import manila_today
from tests.test_web_d0 import _admin

TODAY = manila_today()


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def test_a_bank_loan_from_creation_to_its_schedule(web, db):
    _admin(web)
    bank = make_creditor(db, "BDO Unibank", creditor_type="BANK_LOAN")
    assert "New bank loan for" in web.get("/bank-loans/").content.decode()
    r = web.post("/bank-loans/", {"action": "create", "bank": bank, "ref": "BL-77", "start": "2026-01-01", "maturity": "2028-01-01", "principal": "1,000,000", "mode": "ADA"})
    blid = fetchone(_db(), "SELECT bank_loan_id FROM bank_loans WHERE loan_ref = 'BL-77'")[0]
    assert r["Location"] == f"/bank-loans/{blid}/"
    web.post(f"/bank-loans/{blid}/", {"action": "fill", "monthly": "50,000", "n": "3", "first_due": str(TODAY - timedelta(days=10))})
    rows = sqlite3.connect(_db()).execute("SELECT installment_id, amount FROM bank_loan_installments WHERE bank_loan_id = ? ORDER BY due_date", (blid,)).fetchall()
    assert len(rows) == 3 and rows[0][1] == 50000.0
    page = web.get(f"/bank-loans/{blid}/").content.decode()
    assert "BL-77" in page and "overdue" in page and "not yet" in page                         # past due but not paid: still owed
    assert "150,000.00" in web.get("/bank-loans/").content.decode()                              # still to pay on the list
    web.post(f"/bank-loans/{blid}/", {"action": "edit", "installment_id": rows[1][0], "due": str(TODAY + timedelta(days=25)), "amount": "55,000"})
    assert fetchone(_db(), "SELECT amount FROM bank_loan_installments WHERE installment_id = ?", (rows[1][0],))[0] == 55000.0
    with sqlite3.connect(_db()) as conn:                                                         # ticked paid on Daily payables
        conn.execute("UPDATE bank_loan_installments SET cleared_at = ? WHERE installment_id = ?", (str(TODAY), rows[0][0]))
    web.post(f"/bank-loans/{blid}/", {"action": "delete_row", "installment_id": rows[0][0]})
    assert fetchone(_db(), "SELECT COUNT(*) FROM bank_loan_installments WHERE installment_id = ?", (rows[0][0],))[0] == 1   # paid: locked
    web.post(f"/bank-loans/{blid}/", {"action": "details", "bank": bank, "ref": "BL-77A", "start": "2026-01-01", "maturity": "2028-01-01", "principal": "1,000,000", "mode": "ADA"})
    assert fetchone(_db(), "SELECT loan_ref FROM bank_loans WHERE bank_loan_id = ?", (blid,))[0] == "BL-77A"
    web.post(f"/bank-loans/{blid}/", {"action": "cancel"})
    assert fetchone(_db(), "SELECT is_cancelled FROM bank_loans WHERE bank_loan_id = ?", (blid,))[0] == 1


def test_cards_and_a_statement_from_the_soa_confirming_the_earlier_one(web, db):
    _admin(web)
    issuer = make_creditor(db, "RCBC", creditor_type="CREDIT_CARD")
    web.post("/credit-cards/", {"action": "add_holder", "name": "Rhodora Roxas"})
    holder = fetchone(_db(), "SELECT cardholder_id FROM cardholders WHERE name = 'Rhodora Roxas'")[0]
    web.post("/credit-cards/", {"action": "add_card", "issuer": issuer, "card_name": "Gold", "last4": "1234", "holder": holder, "limit": "100,000", "statement_day": "5"})
    card = fetchone(_db(), "SELECT card_id FROM credit_cards WHERE card_number_last4 = '1234'")[0]
    assert "Gold - 1234" in web.get("/credit-cards/?tab=cards").content.decode()
    web.post("/credit-cards/", {"action": "statement", "card": card, "month": "2026-08", "due": str(TODAY - timedelta(days=30)), "total": "10,000"})
    first = fetchone(_db(), "SELECT cc_statement_id, total_amount_due, paid_confirmed FROM cc_statements WHERE card_id = ?", (card,))
    assert first[1:] == (10000.0, 0)
    from engines import clearing
    assert clearing.pay_card(first[0], TODAY - timedelta(days=20), amount=6000)[0]              # a co-owner records 6,000 on Daily payables
    web.post("/credit-cards/", {"action": "statement", "card": card, "month": "2026-09", "due": str(TODAY), "total": "9,000", "payments": "6,000", "interest": "0"})
    assert fetchone(_db(), "SELECT amount_paid, status, paid_confirmed FROM cc_statements WHERE cc_statement_id = ?", (first[0],)) == (6000.0, "Partially Paid", 1)
    assert fetchone(_db(), "SELECT COUNT(*) FROM review_flags WHERE kind = 'CC_PAYMENT_MISMATCH'")[0] == 0     # the SOA agrees with the daily record
    page = web.get(f"/credit-cards/?card={card}&show=all").content.decode()
    assert "9,000.00" in page and "6,000.00" in page and "confirmed" in page
    assert "Missing statement check" in web.get("/credit-cards/?tab=missing").content.decode()


def test_release_checks_is_gone_from_the_main_system(web, db):
    _admin(web)
    home = web.get("/").content.decode()
    assert "Release checks" not in home and "Bank loans" in home and "Credit cards" in home
