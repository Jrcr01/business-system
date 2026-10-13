"""Phase F1.4 (2026-09-28): safe saves for a few users. Two people doing the same thing at the same moment -- run for real in two threads,
with a pause injected between the check and the save so that, without the lock, both would pass the check:
  * two checks on the same batch that only has room for one -> exactly one is issued (never overpaid);
  * two batches promoted at once -> two different OS numbers; the same for loan notes;
  * two new drafts at once -> two different DRAFT refs;
  * the number counter never reuses a number and never goes below a number already on file."""
import threading
import time
from datetime import date

import app
import engines.check_rules as check_rules
import engines.db_engine as db_engine
import engines.statement_rules as statement_rules
from conftest import make_creditor
from engines.common import get_conn, write_tx
from engines.db_engine import next_doc_number, promote_draft_statement
from engines.loan_note_rules import create_loan_note, promote_draft_loan_note


def _together(fn, args_list):
    """Run fn(*args) for every args at the same moment (one thread each); return the results in order."""
    barrier = threading.Barrier(len(args_list))
    results = [None] * len(args_list)

    def run(i, args):
        barrier.wait()
        results[i] = fn(*args)

    threads = [threading.Thread(target=run, args=(i, a)) for i, a in enumerate(args_list)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    return results


def _slow(monkeypatch, module, name, pause=0.3):
    real = getattr(module, name)

    def slow(*a, **k):
        out = real(*a, **k)
        time.sleep(pause)            # widen the gap between the check and the save
        return out

    monkeypatch.setattr(module, name, slow)


def test_two_checks_at_once_never_overpay_a_batch(db, monkeypatch):
    sup = make_creditor(db, name="Race Supplier")
    sid = statement_rules.create_order_statement(sup)
    app.add_invoice(sup, "RACE-1", date(2026, 9, 1), date(2026, 10, 1), 1000.0, [], sid)
    _slow(monkeypatch, check_rules, "get_batch_balance")
    results = _together(check_rules.issue_check, [(sup, 800.0, date(2026, 10, 1), sid), (sup, 800.0, date(2026, 10, 2), sid)])
    assert sorted(r[0] for r in results) == [False, True]
    assert "exceeds the payment statement's remaining balance" in next(r[1] for r in results if not r[0])
    with get_conn(db) as conn:
        assert conn.execute("SELECT COALESCE(SUM(amount), 0) FROM checks_issued WHERE statement_id = ? AND status = 'Active'",
                            (sid,)).fetchone()[0] == 800.0


def test_two_check_grids_at_once_never_overpay_a_batch(db, monkeypatch):
    sup = make_creditor(db, name="Grid Race")
    sid = statement_rules.create_order_statement(sup)
    app.add_invoice(sup, "GRID-1", date(2026, 9, 1), date(2026, 10, 1), 1000.0, [], sid)
    _slow(monkeypatch, check_rules, "get_batch_balance")
    rows = [{"check_date": date(2026, 10, 1), "amount": 300.0}, {"check_date": date(2026, 10, 8), "amount": 300.0}]
    results = _together(check_rules.issue_checks_grid, [(sup, sid, rows), (sup, sid, rows)])
    assert sorted(r[0] for r in results) == [False, True]


def test_two_promotions_at_once_get_different_numbers(db, monkeypatch):
    sup = make_creditor(db, name="Promo Supplier")
    a, b = statement_rules.create_order_statement(sup), statement_rules.create_order_statement(sup)
    _slow(monkeypatch, db_engine, "next_doc_number")
    refs = _together(promote_draft_statement, [(a, "Issued"), (b, "Issued")])
    assert len(set(refs)) == 2 and all(" - PS " in r for r in refs)


def test_two_loan_note_promotions_at_once_get_different_numbers(db, monkeypatch):
    lender = make_creditor(db, name="Lender Race", creditor_type="LENDER")
    ids = [create_loan_note(lender, date(2026, 9, 1), 10000.0, 1.0)[2] for _ in range(2)]
    import engines.loan_note_rules as lnr
    _slow(monkeypatch, lnr, "next_doc_number")
    refs = _together(promote_draft_loan_note, [(ids[0],), (ids[1],)])
    assert len(set(refs)) == 2 and all(" - LN " in r for r in refs)


def test_two_new_drafts_at_once_get_different_refs(db, monkeypatch):
    sup = make_creditor(db, name="Draft Race")
    real = statement_rules.new_draft_ref

    def slow(*a, **k):
        out = real(*a, **k)
        time.sleep(0.3)
        return out

    monkeypatch.setattr(statement_rules, "new_draft_ref", slow)
    ids = _together(statement_rules.create_order_statement, [(sup,), (sup,)])
    with get_conn(db) as conn:
        refs = [conn.execute("SELECT statement_ref FROM order_statements WHERE statement_id = ?", (i,)).fetchone()[0] for i in ids]
    assert len(set(refs)) == 2


def test_the_counter_never_reuses_and_respects_numbers_on_file(db):
    with write_tx(db) as conn:
        assert next_doc_number(conn, "OS", 2030) == 1
        assert next_doc_number(conn, "OS", 2030) == 2
        assert next_doc_number(conn, "OS", 2030, in_use=41) == 42        # a number typed in / made before the counter existed
        assert next_doc_number(conn, "OS", 2030, in_use=5) == 43          # never goes back
        assert next_doc_number(conn, "LN", 2030) == 1                     # each series and year counts on its own


def test_a_failed_step_saves_nothing(db):
    sup = make_creditor(db, name="Rollback Supplier")
    try:
        with write_tx(db) as conn:
            conn.execute("INSERT INTO order_statements (creditor_id, statement_ref, total_amount, status) VALUES (?, 'RB-1', 0, 'Draft')", (sup,))
            raise RuntimeError("something went wrong half way")
    except RuntimeError:
        pass
    with get_conn(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM order_statements WHERE statement_ref = 'RB-1'").fetchone()[0] == 0
