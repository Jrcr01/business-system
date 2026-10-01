"""Voided / replaced / spoiled checks register (owner, 2026-09-30: "I voided a check, why is it not in the voided checks?").
A void used to throw its reason away, left no date on the check and appeared in no list; deleting a loan or bank-loan installment left
no audit trace at all. Now: the reason and date are kept, one list shows every voided, replaced and spoiled check, and both deletes are audited."""
import sqlite3
from datetime import date, timedelta

import app
from conftest import fetchone, make_creditor, make_statement
from engines.bank_loan_service import add_bank_loan_installment, create_bank_loan, delete_bank_loan_installment
from engines.check_rules import cancel_check, replace_check
from engines.loan_schedule_service import add_loan_installment, delete_loan_installment
from engines.voided_checks_service import voided_checks


def _batch_with_check(db, number="3992565434", amount=30400.0, name="Express Tire"):
    creditor_id = make_creditor(db, name=name)
    statement_id = make_statement(db, creditor_id)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE order_statements SET statement_ref = ? WHERE statement_id = ?", (f"TEST-{statement_id}", statement_id))      # make_statement uses one fixed ref
    cur = conn.execute("INSERT INTO checks_issued (bank_account_id, statement_id, creditor_id, check_number, amount, check_date, status) "
                       "VALUES (1, ?, ?, ?, ?, '2026-12-05', 'Active')", (statement_id, creditor_id, number, amount))
    conn.commit()
    check_id = cur.lastrowid
    conn.close()
    return creditor_id, statement_id, check_id


def test_a_void_keeps_its_reason_and_date(db):
    _, _, check_id = _batch_with_check(db)

    ok, _ = cancel_check(check_id, "wrong supplier on the check")

    assert ok
    status, reason, at = fetchone(db, "SELECT status, cancel_reason, cancelled_at FROM checks_issued WHERE check_id = ?", (check_id,))
    assert (status, reason) == ("Cancelled", "wrong supplier on the check") and at
    assert "wrong supplier on the check" in fetchone(db, "SELECT new_values FROM audit_logs WHERE user_action = 'CHECK_CANCELLED'")[0]


def test_a_void_without_a_reason_still_works_as_before(db):
    _, _, check_id = _batch_with_check(db)
    assert cancel_check(check_id)[0]
    assert fetchone(db, "SELECT cancel_reason FROM checks_issued WHERE check_id = ?", (check_id,))[0] in (None, "")


def test_the_register_lists_voided_replaced_and_spoiled_checks(db):
    creditor_id, statement_id, voided = _batch_with_check(db, "3992565434", 30400.0)
    _, _, to_replace = _batch_with_check(db, "3992565440", 5000.0, name="Other Supplier")
    cancel_check(voided, "duplicate")
    ok, _, new_id = replace_check(to_replace, date(2026, 12, 10), "wrong date")
    assert ok
    app.create_booklet(1, "B1", 3992554301, 3992554400, date(2026, 1, 1))
    assert app.log_spoiled_check("3992554305", "printer jam")[0]

    df = voided_checks()

    by_no = df.set_index("check_number")
    assert set(by_no.index) == {"3992565434", "3992565440", "3992554305"}
    v = by_no.loc["3992565434"]
    assert (v["what"], v["payee"], v["amount"], v["reason"]) == ("Voided", "Express Tire", 30400.0, "duplicate")
    assert v["happened_at"]
    r = by_no.loc["3992565440"]
    assert (r["what"], r["reason"], r["payee"]) == ("Replaced", "wrong date", "Other Supplier")
    assert r["replaced_by"] == ""                                    # the new check has no number yet
    s = by_no.loc["3992554305"]
    assert (s["what"], s["reason"]) == ("Spoiled", "printer jam")


def test_a_replaced_check_shows_the_number_of_its_replacement(db):
    _, _, old_id = _batch_with_check(db, "3992565440", 5000.0)
    ok, _, new_id = replace_check(old_id, date(2026, 12, 10), "wrong date")
    assert app.assign_check_number(new_id, None, None, "3992554297")[0]

    row = voided_checks().set_index("check_number").loc["3992565440"]

    assert row["replaced_by"] == "3992554297"


def test_a_voided_check_the_bank_paid_anyway_is_flagged(db):
    _, _, check_id = _batch_with_check(db)
    cancel_check(check_id, "voided")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE checks_issued SET bank_cleared_date = '2026-12-07' WHERE check_id = ?", (check_id,))
    conn.commit()
    conn.close()

    row = voided_checks().iloc[0]

    assert row["bank_paid_anyway"] == "Yes - bank cleared it 2026-12-07"


def test_an_old_void_without_a_saved_date_takes_it_from_the_audit_log(db):
    _, _, check_id = _batch_with_check(db)
    cancel_check(check_id)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE checks_issued SET cancelled_at = NULL WHERE check_id = ?", (check_id,))      # how voids made before this change look
    conn.commit()
    conn.close()

    row = voided_checks().iloc[0]

    assert row["happened_at"]


def test_the_register_filters_by_date_and_payee(db):
    _, _, a = _batch_with_check(db, "3992565434", 100.0, name="Alpha")
    _, _, b = _batch_with_check(db, "3992565435", 200.0, name="Beta")
    cancel_check(a, "x")
    cancel_check(b, "y")
    beta = fetchone(db, "SELECT creditor_id FROM creditors WHERE creditor_name = 'Beta'")[0]

    assert list(voided_checks(creditor_id=beta)["payee"]) == ["Beta"]
    tomorrow = date.today() + timedelta(days=1)
    assert voided_checks(date_from=tomorrow).empty


def test_deleting_a_loan_note_installment_is_audited(db):
    creditor_id = make_creditor(db, creditor_type="LENDER")
    ok, _, loan_note_id = app.create_loan_note(creditor_id, date(2026, 1, 1), 100000.0, 2.0)
    add_loan_installment(loan_note_id, date(2026, 2, 1), 10000.0, 500.0)
    installment_id = fetchone(db, "SELECT installment_id FROM loan_installments")[0]

    assert delete_loan_installment(installment_id)[0]

    action, entity_id, old = fetchone(db, "SELECT user_action, entity_id, old_values FROM audit_logs WHERE user_action = 'LOAN_INSTALLMENT_DELETED'")
    assert entity_id == installment_id and "10,000.00" in old and "2026-02-01" in old


def test_deleting_a_bank_loan_installment_is_audited(db):
    creditor_id = make_creditor(db, creditor_type="BANK_LOAN")
    bank_loan_id = create_bank_loan(creditor_id, "BL-1", "ACC-1", date(2026, 1, 1), date(2030, 1, 1), None, 500000.0, "ADA")
    future = date.today() + timedelta(days=60)
    add_bank_loan_installment(bank_loan_id, future, 20000.0, 1000.0)
    installment_id = fetchone(db, "SELECT installment_id FROM bank_loan_installments")[0]

    assert delete_bank_loan_installment(installment_id)[0]

    action, entity_id, old = fetchone(db, "SELECT user_action, entity_id, old_values FROM audit_logs WHERE user_action = 'BANK_LOAN_INSTALLMENT_DELETED'")
    assert entity_id == installment_id and "20,000.00" in old and str(future) in old
