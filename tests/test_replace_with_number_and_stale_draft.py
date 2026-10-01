"""Two fixes (owner, 2026-10-01): (1) a stale "typed but not parked yet" reminder after the same check was recorded as a handwritten check; (2) Void & reissue
in ONE step with the replacement's number (so it is never parked)."""
import sqlite3
from datetime import date, timedelta

from conftest import fetchone, make_creditor
from engines.check_rules import issue_check
from engines.handwritten_check_service import record_handwritten_checks, replace_check_with_number
from engines.park_draft_service import list_park_drafts, load_park_draft, save_park_draft
from engines.statement_rules import add_invoice, create_order_statement


def _batch_with_invoice(db, amount=3430.0):
    sup = make_creditor(db, name="Acom")
    sid = create_order_statement(sup)
    add_invoice(sup, "68978", date.today(), date.today() + timedelta(days=30), amount, [], sid)
    return sup, sid


def test_recording_the_same_check_as_handwritten_clears_the_park_draft(db):
    sup, sid = _batch_with_invoice(db)
    d = date.today() + timedelta(days=60)
    save_park_draft(sid, "By number of checks", 1, 3430.0, [1], [(str(d), 3430.0)])
    assert len(list_park_drafts()) == 1

    ok, msg, _ = record_handwritten_checks(sup, [{"number": "3992554295", "date": d, "amount": 3430.0}], statement_id=sid)

    assert ok, msg
    assert load_park_draft(sid) is None and list_park_drafts() == []


def test_a_draft_for_a_batch_that_still_has_balance_is_kept(db):
    sup, sid = _batch_with_invoice(db)
    d = date.today() + timedelta(days=60)
    save_park_draft(sid, "By number of checks", 2, 3430.0, [1], [(str(d), 1715.0), (str(d), 1715.0)])
    assert len(list_park_drafts()) == 1


def test_an_old_stale_draft_for_a_fully_covered_batch_is_dropped_when_listed(db):
    sup, sid = _batch_with_invoice(db)
    ok, msg, _ = issue_check(sup, 3430.0, date.today() + timedelta(days=60), statement_id=sid)
    assert ok, msg
    save_park_draft(sid, "By number of checks", 1, 3430.0, [1], [(str(date.today()), 3430.0)])

    assert list_park_drafts() == []
    assert load_park_draft(sid) is None                         # removed for good, so the leave-page prompt stops too


def _active_check(db, number="3992554001"):
    sup, sid = _batch_with_invoice(db)
    d = date.today() + timedelta(days=30)
    ok, msg, ids = record_handwritten_checks(sup, [{"number": "3992554001", "date": d, "amount": 3430.0}], statement_id=sid)
    assert ok, msg
    return sup, sid, ids[0], d


def test_void_and_reissue_with_a_number_saves_the_replacement_numbered_printed_and_handwritten(db):
    sup, sid, old, d = _active_check(db)

    ok, msg, new = replace_check_with_number(old, d, "3992554296", "wrong date")

    assert ok, msg
    assert fetchone(db, "SELECT check_number, status, amount, check_date, is_handwritten, printed_at IS NOT NULL FROM checks_issued WHERE check_id = ?", (new,)) \
        == ("3992554296", "Active", 3430.0, str(d), 1, 1)
    assert fetchone(db, "SELECT status, replacement_check_id FROM checks_issued WHERE check_id = ?", (old,)) == ("Replaced", new)
    assert fetchone(db, "SELECT SUM(amount) FROM invoice_check_allocations WHERE check_id = ?", (new,))[0] == 3430.0


def test_a_number_already_used_is_refused_before_anything_is_voided(db):
    sup, sid, old, d = _active_check(db)
    other = make_creditor(db, name="Other")
    osid = create_order_statement(other)
    add_invoice(other, "X1", date.today(), date.today() + timedelta(days=30), 500.0, [], osid)
    ok, msg, taken = issue_check(other, 100.0, d, statement_id=osid)
    assert ok, msg
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE checks_issued SET check_number = '3992554999' WHERE check_id = ?", (taken,))

    ok, msg, new = replace_check_with_number(old, d, "3992554999", "")

    assert not ok and "already on file" in msg and new is None
    assert fetchone(db, "SELECT status FROM checks_issued WHERE check_id = ?", (old,))[0] == "Active"


def test_without_a_number_the_replacement_is_parked_as_before(db):
    sup, sid, old, d = _active_check(db)

    ok, msg, new = replace_check_with_number(old, d, "  ", "")

    assert ok, msg
    assert fetchone(db, "SELECT check_number, is_handwritten FROM checks_issued WHERE check_id = ?", (new,)) == (None, 0)
