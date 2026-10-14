"""Correcting a written check (owner, 2026-10-15: "what if i incorrectly entered a check like this instead of 1/30/2027. i entered 1/30/2026"): the same
check and number, the right date (or amount, or number), a reason, the audit log. Not a void."""
import sqlite3
from datetime import date

from conftest import make_creditor
from tests.test_web_d0 import _admin


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _q(sql, params=()):
    with sqlite3.connect(_db()) as conn:
        return conn.execute(sql, params).fetchall()


def _lender_check(db):
    from engines.acting_user import acting_as
    from engines.check_rules import issue_check
    from engines.loan_schedule_service import create_note_with_payments
    cid = make_creditor(db, "Neth Canlas", creditor_type="LENDER")
    with acting_as("admin@slicks.test", "admin"):
        ok, msg, lnid = create_note_with_payments(cid, date(2026, 9, 1), 100467.0, 0.0, [(date(2027, 1, 30), 100467.0)])
        assert ok, msg
        inst = _q("SELECT installment_id FROM loan_installments WHERE loan_note_id = ?", (lnid,))[0][0]
        ok, msg, chk = issue_check(cid, 100467.0, date(2026, 1, 30), loan_note_id=lnid, installment_id=inst)    # the year typed wrong
        assert ok, msg
    with sqlite3.connect(_db()) as conn:
        conn.execute("UPDATE checks_issued SET check_number = '3992575932', is_handwritten = 1 WHERE check_id = ?", (chk,))
    return lnid, chk


def test_a_written_check_typed_wrong_is_corrected_not_voided(web, db):
    _admin(web)
    lnid, chk = _lender_check(db)
    page = web.get(f"/lenders/{lnid}/?tab=checks").content.decode()
    assert f"fix={chk}" in page and "Correct</a>" in page
    page = web.get(f"/lenders/{lnid}/?tab=checks&fix={chk}").content.decode()
    assert "Correct check 3992575932" in page and "typed 2026 instead of 2027" in page
    assert web.post(f"/lenders/{lnid}/", {"action": "chk_fix", "check_id": chk, "field": "date", "new_date": "2027-01-30", "reason": ""}).status_code == 302
    assert _q("SELECT check_date FROM checks_issued WHERE check_id = ?", (chk,))[0][0][:10] == "2026-01-30"          # no reason: nothing changed
    web.post(f"/lenders/{lnid}/", {"action": "chk_fix", "check_id": chk, "field": "date", "new_date": "2027-01-30", "reason": "typed 2026 instead of 2027"})
    row = _q("SELECT check_date, check_number, status FROM checks_issued WHERE check_id = ?", (chk,))[0]
    assert row[0][:10] == "2027-01-30" and row[1] == "3992575932" and row[2] == "Active"
    assert _q("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CHECK_CORRECTION' AND entity_id = ?", (chk,))[0][0] == 1
    web.post(f"/lenders/{lnid}/", {"action": "chk_fix", "check_id": chk, "field": "amount", "new_amount": "200,000", "reason": "test"})
    assert _q("SELECT amount FROM checks_issued WHERE check_id = ?", (chk,))[0][0] == 100467.0                         # never above what the payment owes
