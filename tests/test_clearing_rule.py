"""The clearing rule (owner, 2026-10-08): a check is paid only when it is cleared -- by the bank (an imported statement), settled outside the bank, or ticked
cleared by a person -- never by its date alone. A tick is confirmed by the bank statement; a statement covering the tick's date that does not show the check
puts a line on the Review list. Once, at go-live, the checks from before the first bank statement are marked "cleared before go-live"."""
import sqlite3
from datetime import date, timedelta

import app
from conftest import fetchone, make_creditor, make_statement
from engines import clearing
from engines.clock import manila_today

TODAY = manila_today()


def _issued(db, number="0004471", day=None, amount=1000.0):
    sup = make_creditor(db, name="Goodyear Philippines")
    sid = make_statement(db, sup)
    app.add_invoice(sup, f"INV-{number}", TODAY - timedelta(days=40), TODAY - timedelta(days=10), amount, [], sid)
    _, _, cid = app.issue_check(sup, amount, day or TODAY - timedelta(days=5), statement_id=sid)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE checks_issued SET check_number = ? WHERE check_id = ?", (number, cid))
    return sid, cid


def _bank_lines(db, *days):
    """An imported bank statement with one deposit line on each day (covering those dates)."""
    with sqlite3.connect(db) as conn:
        imp = conn.execute("INSERT INTO bank_statement_imports (bank_account_id, file_name, file_hash) VALUES (1, 's.csv', 'h')").lastrowid
        for n, d in enumerate(days):
            conn.execute("INSERT INTO bank_transactions (import_id, txn_date, posting_date, description, debit, credit, kind, line_key) VALUES (?, ?, ?, 'x', 0, 10, 'DEPOSIT', ?)",
                         (imp, str(d), str(d), f"k{n}"))


def _status(db, sid):
    return fetchone(db, "SELECT status FROM invoices WHERE statement_id = ?", (sid,))[0]


def test_a_past_dated_check_stays_owed_until_it_is_ticked_and_the_tick_can_be_undone(db):
    sid, cid = _issued(db)
    assert _status(db, sid) == "Unpaid"
    assert list(clearing.waiting_for_bank()["check_id"]) == [cid]
    ok, msg = clearing.mark_cleared(cid, TODAY - timedelta(days=1), "seen in online banking")
    assert ok, msg
    assert _status(db, sid) == "Paid" and bool(clearing.waiting_for_bank()["ticked"].iloc[0])
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CHECK_MARKED_CLEARED'")[0] == 1
    assert clearing.unmark_cleared(cid)[0] and _status(db, sid) == "Unpaid"


def test_a_tick_is_refused_for_a_future_date_a_check_without_number_or_one_the_bank_cleared(db):
    sid, cid = _issued(db)
    assert not clearing.mark_cleared(cid, TODAY + timedelta(days=1))[0]
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE checks_issued SET check_number = NULL WHERE check_id = ?", (cid,))
    assert "no number" in clearing.mark_cleared(cid)[1]
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE checks_issued SET check_number = '0004471', bank_cleared_date = ? WHERE check_id = ?", (str(TODAY), cid))
    assert "already cleared" in clearing.mark_cleared(cid)[1]


def test_the_bank_statement_confirms_or_flags_a_tick(db):
    sid, cid = _issued(db)
    clearing.mark_cleared(cid, TODAY - timedelta(days=2))
    assert clearing.check_ticks_against_bank() == 0                         # no statement yet: nothing to compare
    _bank_lines(db, TODAY - timedelta(days=4), TODAY)                       # a statement covering the tick date, without this check
    assert clearing.check_ticks_against_bank() == 1
    assert fetchone(db, "SELECT kind FROM review_flags WHERE ref_id = ?", (cid,))[0] == "CLEARED_NOT_ON_BANK"


def test_the_cutoff_marks_checks_before_the_first_statement_once(db):
    sid_old, old = _issued(db, "0001000", TODAY - timedelta(days=90))
    sup = fetchone(db, "SELECT creditor_id FROM order_statements WHERE statement_id = ?", (sid_old,))[0]
    with sqlite3.connect(db) as conn:                       # a second check, dated inside the statement period
        new = conn.execute("INSERT INTO checks_issued (bank_account_id, creditor_id, statement_id, check_number, check_date, amount, status) VALUES (1, ?, ?, '0002000', ?, 500, 'Active')",
                           (sup, sid_old, str(TODAY - timedelta(days=5)))).lastrowid
    _bank_lines(db, TODAY - timedelta(days=30))
    prev = clearing.cutoff_preview()
    assert prev["first_day"] == TODAY - timedelta(days=30) and list(prev["before"]["check_id"]) == [old] and list(prev["inside"]["check_id"]) == [new]
    ok, msg = clearing.apply_cutoff()
    assert ok, msg
    assert fetchone(db, "SELECT manual_cleared_note, manual_cleared_at FROM checks_issued WHERE check_id = ?", (old,)) == (clearing.CUTOFF_NOTE, str(TODAY - timedelta(days=90)))
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CLEARING_CUTOFF'")[0] == 1
    assert fetchone(db, "SELECT manual_cleared_at FROM checks_issued WHERE check_id = ?", (new,))[0] is None      # inside the statement period: left for the owner
    assert clearing.check_ticks_against_bank() == 0                                                                 # a cut-off mark is not a tick to confirm
