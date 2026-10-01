"""Batch locking follows the money (owner, 2026-10-01: "suggest a solution for seamless workflow").

A batch locks itself the moment its active checks cover its net total, and reopens itself when a check change leaves a balance. There is no lock
button to remember: printing a statement never locks, issuing the last check does. Cancelled batches stay locked for good."""
import sqlite3
from datetime import date

import app
from conftest import fetchone, make_creditor
from engines.check_rules import apply_check_correction, cancel_check, edit_parked_check, issue_check, issue_checks_grid, replace_check, unpark_checks
from engines.handwritten_check_service import record_handwritten_checks
from engines.lock_rules import apply_lock_catchup, lock_catchup_preview


def _batch(db, gross=1000.0, doc="INV-1"):
    cid = make_creditor(db)
    sid = app.create_order_statement(cid)
    app.add_invoice(cid, doc, date(2026, 1, 1), date(2026, 1, 31), gross, [], sid)
    return cid, sid


def _locked(db, sid):
    return fetchone(db, "SELECT is_locked FROM order_statements WHERE statement_id = ?", (sid,))[0]


def _audit(db, action):
    return fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = ?", (action,))[0]


def test_the_last_check_that_covers_the_batch_locks_it(db):
    cid, sid = _batch(db)
    ok, _, _ = issue_check(cid, 400.0, date(2026, 2, 1), statement_id=sid)
    assert ok and _locked(db, sid) == 0                           # partly covered: still open
    ok, _, _ = issue_check(cid, 600.0, date(2026, 3, 1), statement_id=sid)
    assert ok and _locked(db, sid) == 1                           # fully covered: locked by itself
    assert _audit(db, "BATCH_LOCKED") == 1


def test_parking_a_grid_that_covers_the_batch_locks_it(db):
    cid, sid = _batch(db)
    ok, _, _ = issue_checks_grid(cid, sid, [{"check_date": date(2026, 2, 1), "amount": 500.0}, {"check_date": date(2026, 3, 1), "amount": 500.0}])
    assert ok and _locked(db, sid) == 1


def test_a_partly_covered_batch_stays_open(db):
    cid, sid = _batch(db)
    issue_checks_grid(cid, sid, [{"check_date": date(2026, 2, 1), "amount": 999.0}])
    assert _locked(db, sid) == 0


def test_a_batch_with_no_checks_or_no_invoices_is_never_locked(db):
    cid = make_creditor(db)
    sid = app.create_order_statement(cid)                          # nothing in it: net 0 and checks 0 must not count as "covered"
    from engines.lock_rules import sync_batch_lock
    assert sync_batch_lock(sid) is None and _locked(db, sid) == 0
    app.add_invoice(cid, "INV-9", date(2026, 1, 1), date(2026, 1, 31), 500.0, [], sid)
    assert sync_batch_lock(sid) is None and _locked(db, sid) == 0


def test_voiding_a_check_in_a_locked_batch_just_works_and_reopens_it(db):
    cid, sid = _batch(db)
    _, _, check_id = issue_check(cid, 1000.0, date(2026, 2, 1), statement_id=sid)
    assert _locked(db, sid) == 1
    ok, msg = cancel_check(check_id, "wrong amount")
    assert ok, msg
    assert _locked(db, sid) == 0                                   # the balance is open again, so the batch is
    assert _audit(db, "BATCH_UNLOCKED") == 1


def test_replacing_a_check_in_a_locked_batch_keeps_it_locked(db):
    cid, sid = _batch(db)
    _, _, check_id = issue_check(cid, 1000.0, date(2026, 2, 1), statement_id=sid)
    ok, msg, new_id = replace_check(check_id, date(2026, 2, 15), "re-date")
    assert ok, msg
    assert _locked(db, sid) == 1                                   # same amount, so it is covered again straight away
    assert fetchone(db, "SELECT status FROM checks_issued WHERE check_id = ?", (new_id,))[0] == "Active"


def test_un_parking_in_a_locked_batch_reopens_it(db):
    cid, sid = _batch(db)
    _, _, ids = issue_checks_grid(cid, sid, [{"check_date": date(2026, 2, 1), "amount": 400.0}, {"check_date": date(2026, 3, 1), "amount": 600.0}])
    assert _locked(db, sid) == 1
    ok, msg = unpark_checks(sid, [ids[1]])
    assert ok, msg
    assert _locked(db, sid) == 0


def test_correcting_a_printed_check_amount_down_reopens_the_batch(db):
    cid, sid = _batch(db)
    _, _, check_id = issue_check(cid, 1000.0, date(2026, 2, 1), statement_id=sid)
    sqlite3.connect(db).execute("UPDATE checks_issued SET printed_at = CURRENT_TIMESTAMP, check_number = '3992000001' WHERE check_id = ?", (check_id,)).connection.commit()
    ok, msg = apply_check_correction(check_id, "Amount", "900", "typo")
    assert ok, msg
    assert _locked(db, sid) == 0                                   # 100 left to cover


def test_correcting_a_printed_check_date_relocks_the_batch(db):
    cid, sid = _batch(db)
    _, _, check_id = issue_check(cid, 1000.0, date(2026, 2, 1), statement_id=sid)
    sqlite3.connect(db).execute("UPDATE checks_issued SET printed_at = CURRENT_TIMESTAMP, check_number = '3992000001' WHERE check_id = ?", (check_id,)).connection.commit()
    ok, msg = apply_check_correction(check_id, "Check Date", "2026-02-20", "moved")
    assert ok, msg
    assert _locked(db, sid) == 1                                   # unlocked for the correction, covered again, locked again


def test_editing_a_parked_checks_amount_follows_the_balance(db):
    cid, sid = _batch(db)
    _, _, check_id = issue_check(cid, 1000.0, date(2026, 2, 1), statement_id=sid)
    ok, _ = edit_parked_check(check_id, 800.0, date(2026, 2, 1))
    assert ok and _locked(db, sid) == 0
    ok, _ = edit_parked_check(check_id, 1000.0, date(2026, 2, 1))
    assert ok and _locked(db, sid) == 1


def test_a_failed_change_leaves_the_lock_alone(db):
    cid, sid = _batch(db)
    _, _, check_id = issue_check(cid, 1000.0, date(2026, 2, 1), statement_id=sid)
    ok, _ = cancel_check(99999, "no such check")
    assert not ok and _locked(db, sid) == 1
    ok, _ = apply_check_correction(check_id, "Amount", "-5", "bad")
    assert not ok and _locked(db, sid) == 1


def test_a_cancelled_batch_stays_locked_and_refuses_check_changes(db):
    cid, sid = _batch(db)
    _, _, check_id = issue_check(cid, 1000.0, date(2026, 2, 1), statement_id=sid)
    sqlite3.connect(db).execute("UPDATE order_statements SET status = 'Cancelled', is_locked = 1 WHERE statement_id = ?", (sid,)).connection.commit()
    ok, msg = cancel_check(check_id, "x")
    assert not ok and "cancelled" in msg.lower() and _locked(db, sid) == 1


def test_editing_an_invoice_after_reopening_locks_the_batch_again_when_covered(db):
    cid, sid = _batch(db)
    issue_check(cid, 1000.0, date(2026, 2, 1), statement_id=sid)
    assert _locked(db, sid) == 1
    app.unlock_statement(sid)                                      # the one-step "edit this batch" dialog does this, with a reason
    inv_id = fetchone(db, "SELECT invoice_id FROM invoices WHERE statement_id = ?", (sid,))[0]
    ok, msg = app.edit_invoice(inv_id, "INV-1A", date(2026, 1, 1), date(2026, 1, 31), 1000.0, 0.0)
    assert ok, msg
    assert _locked(db, sid) == 1                                   # nothing changed in the total: covered, so closed again


def test_editing_an_invoice_up_leaves_the_new_balance_open(db):
    cid, sid = _batch(db)
    issue_check(cid, 1000.0, date(2026, 2, 1), statement_id=sid)
    app.unlock_statement(sid)
    inv_id = fetchone(db, "SELECT invoice_id FROM invoices WHERE statement_id = ?", (sid,))[0]
    ok, msg = app.edit_invoice(inv_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 1200.0, 0.0)
    assert ok, msg
    assert _locked(db, sid) == 0                                   # 200 still to cover


def test_a_handwritten_check_that_covers_the_batch_locks_it(db):
    cid = make_creditor(db, name="Express Tire")
    sid = app.create_order_statement(cid)
    ok, msg, ids = record_handwritten_checks(
        cid, [{"number": "3992554297", "date": date(2026, 12, 5), "amount": 30400.0}], statement_id=sid,
        invoice={"doc_ref": "INV-1", "invoice_date": date(2026, 9, 25), "gross": 30400.0, "less_pct": 0.0})
    assert ok, msg
    assert _locked(db, sid) == 1


def test_catch_up_lists_then_fixes_batches_that_are_out_of_step(db):
    cid1, covered_open = _batch(db, doc="A")                       # fully covered but left unlocked (the old behaviour)
    cid2, locked_with_balance = _batch(db, doc="B")                # locked by a PDF export while still owing
    con = sqlite3.connect(db)
    con.execute("INSERT INTO checks_issued (creditor_id, bank_account_id, statement_id, check_date, amount, status) VALUES (?, 1, ?, '2026-02-01', 1000, 'Active')", (cid1, covered_open))
    con.execute("UPDATE order_statements SET is_locked = 1 WHERE statement_id = ?", (locked_with_balance,))
    con.commit()
    con.close()

    preview = lock_catchup_preview()
    assert list(preview["to_lock"]["statement_id"]) == [covered_open]
    assert list(preview["to_unlock"]["statement_id"]) == [locked_with_balance]
    assert _locked(db, covered_open) == 0 and _locked(db, locked_with_balance) == 1     # a preview changes nothing

    result = apply_lock_catchup()
    assert result == {"locked": 1, "unlocked": 1}
    assert _locked(db, covered_open) == 1 and _locked(db, locked_with_balance) == 0
    assert lock_catchup_preview()["to_lock"].empty and lock_catchup_preview()["to_unlock"].empty
