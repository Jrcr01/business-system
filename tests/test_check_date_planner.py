"""Check-date planner (2026-09-28): the owner's rules -- never before the due date, keep cash at or above the buffer, a daily limit,
priority suppliers first, move checks later (up to N days) when cash is short, never two checks to one supplier on one day -- plus
what needs a check (credit memos / older checks taken off the oldest invoices) and handing a batch's dates to its Issue-checks grid.
Made-up data in a fixed future week: Monday Jan 7, 2030."""
import sqlite3
from datetime import date, timedelta

import app  # noqa: F401  (loads the app's modules, like the other tests)
from conftest import make_creditor
from engines import check_date_planner as cp
from engines.park_draft_service import load_park_draft
from engines.statement_rules import get_batch_balance, get_invoices_with_remaining

MON = date(2030, 1, 7)
D = lambda n: MON + timedelta(days=n)  # noqa: E731


def batch(db, supplier_id, ref, invoices, memo=0.0):
    """invoices: [(doc_ref, due_date, amount)]"""
    with sqlite3.connect(db) as conn:
        sid = conn.execute("INSERT INTO order_statements (creditor_id, statement_ref, total_amount, status) VALUES (?, ?, ?, 'Issued')",
                           (supplier_id, ref, sum(a for _, _, a in invoices))).lastrowid
        for doc, due, amt in invoices:
            conn.execute("""INSERT INTO invoices (creditor_id, doc_ref, invoice_date, due_date, gross_amount, amount, status, statement_id)
                            VALUES (?, ?, ?, ?, ?, ?, 'Unpaid', ?)""", (supplier_id, doc, (due - timedelta(days=30)).isoformat(), due.isoformat(), amt, amt, sid))
        if memo:
            conn.execute("INSERT INTO credit_memos (statement_id, memo_ref, memo_date, amount, reason) VALUES (?, ?, ?, ?, 'test')",
                         (sid, f"CM-{ref}", MON.isoformat(), memo))
    return sid


def settings(**kw):
    return cp.PlanSettings(**{"max_days_late": 7, "daily_limit": 0.0, "daily_max_checks": 0, "priority": set(), "buffer": 0.0, **kw})


def by_doc(p):
    return {r.doc_ref: r for r in p.items.itertuples()}


def test_what_needs_a_check(db):
    a = make_creditor(db, name="Alpha")
    sid = batch(db, a, "OS-1", [("A1", D(0), 6000.0), ("A2", D(7), 5000.0)], memo=1000.0)
    items = cp.open_items(MON)
    assert list(zip(items["doc_ref"], items["amount"])) == [("A1", 5000.0), ("A2", 5000.0)]      # the memo comes off the oldest
    with sqlite3.connect(db) as conn:                                                             # an older check with no invoice link
        conn.execute("INSERT INTO checks_issued (creditor_id, bank_account_id, statement_id, check_date, amount, status) VALUES (?, 1, ?, ?, 4000, 'Active')",
                     (a, sid, D(-10).isoformat()))
    items = cp.open_items(MON)
    assert list(zip(items["doc_ref"], items["amount"])) == [("A1", 1000.0), ("A2", 5000.0)] and items["amount"].sum() == get_batch_balance(sid)[2]


def test_enough_cash_everything_on_its_due_date(db):
    a, b = make_creditor(db, name="Alpha"), make_creditor(db, name="Beta")
    batch(db, a, "OS-1", [("A1", D(0), 1000.0), ("A2", D(5), 2000.0)])      # D(5) is a Saturday -> Monday D(7)
    batch(db, b, "OS-2", [("B1", D(-3), 500.0)])                            # already overdue -> first bank day, today
    p = cp.plan(today=MON, start_cash=1_000_000, inflow_per_day=0, settings=settings())
    r = by_doc(p)
    assert (r["A1"].suggested_date, r["A1"].status, r["A1"].reason) == (D(0), "On time", "On its due date")
    assert (r["A2"].suggested_date, r["A2"].status) == (D(7), "On time") and "weekend" in r["A2"].reason
    assert (r["B1"].suggested_date, r["B1"].status) == (D(0), "Overdue")
    assert all(row.suggested_date >= max(row.due, MON) for row in p.items.itertuples())       # never before the due date
    assert p.summary["doesnt_fit"] == 0 and p.summary["moved"] == 0


def test_short_cash_priority_first_others_moved_later(db):
    a, b = make_creditor(db, name="Alpha"), make_creditor(db, name="Beta")
    batch(db, a, "OS-A", [("A1", D(0), 8000.0)])
    batch(db, b, "OS-B", [("B1", D(0), 8000.0)])
    # 10,000 today, 3,000 in per bank day from tomorrow: only one fits today; Beta (not priority) waits until the cash is there
    p = cp.plan(today=MON, start_cash=10_000, inflow_per_day=3_000, settings=settings(priority={b}))
    r = by_doc(p)
    assert (r["B1"].suggested_date, r["B1"].status) == (D(0), "On time")                     # priority supplier first
    assert (r["A1"].suggested_date, r["A1"].status, r["A1"].days_late) == (D(2), "Moved", 2)
    assert "cash would go under" in r["A1"].reason
    assert (p.daily["balance"] >= -0.005).all()
    # with a buffer of 3,000 Alpha needs one more day
    p2 = cp.plan(today=MON, start_cash=10_000, inflow_per_day=3_000, settings=settings(priority={b}, buffer=3_000))
    assert by_doc(p2)["A1"].suggested_date == D(3)


def test_doesnt_fit_is_flagged_not_dropped(db):
    a = make_creditor(db, name="Alpha")
    batch(db, a, "OS-A", [("A1", D(0), 50_000.0)])
    p = cp.plan(today=MON, start_cash=1_000, inflow_per_day=1_000, settings=settings(max_days_late=3))
    r = by_doc(p)["A1"]
    assert r.status == "Doesn't fit" and r.suggested_date == D(3) and "under the buffer" in r.reason
    assert p.summary["doesnt_fit"] == 1


def test_one_supplier_one_check_a_day(db):
    a = make_creditor(db, name="Alpha")
    s1 = batch(db, a, "OS-1", [("X1", D(1), 100.0), ("X2", D(1), 200.0)])   # same batch, same day -> ONE check
    s2 = batch(db, a, "OS-2", [("Y1", D(1), 300.0)])                         # another batch, same supplier -> next bank day
    with sqlite3.connect(db) as conn:                                          # an existing check on Wednesday too
        conn.execute("INSERT INTO checks_issued (creditor_id, bank_account_id, statement_id, check_date, amount, status) VALUES (?, 1, ?, ?, 50, 'Active')",
                     (a, s2, D(2).isoformat()))
        conn.execute("UPDATE order_statements SET total_amount = total_amount + 50 WHERE statement_id = ?", (s2,))
    p = cp.plan(today=MON, start_cash=1_000_000, inflow_per_day=0, settings=settings())
    r = by_doc(p)
    assert r["X1"].suggested_date == r["X2"].suggested_date == D(1)
    assert r["Y1"].suggested_date == D(3) and "already has a check" in r["Y1"].reason
    checks = p.checks.set_index("statement_id")
    assert checks.loc[s1, "amount"] == 300.0 and checks.loc[s1, "n_invoices"] == 2 and len(p.checks) == 2


def test_daily_limits(db):
    a, b, c = make_creditor(db, name="Alpha"), make_creditor(db, name="Beta"), make_creditor(db, name="Gamma")
    for sup, ref in ((a, "OS-A"), (b, "OS-B"), (c, "OS-C")):
        batch(db, sup, ref, [(f"{ref}-1", D(0), 6000.0)])
    p = cp.plan(today=MON, start_cash=1_000_000, inflow_per_day=0, settings=settings(daily_limit=13_000))
    assert sorted(p.items["suggested_date"]) == [D(0), D(0), D(1)] and "daily limit" in p.items.iloc[-1]["reason"]
    p2 = cp.plan(today=MON, start_cash=1_000_000, inflow_per_day=0, settings=settings(daily_max_checks=1))
    assert sorted(p2.items["suggested_date"]) == [D(0), D(1), D(2)]


def test_marked_days_are_skipped(db):
    a = make_creditor(db, name="Alpha")
    batch(db, a, "OS-A", [("A1", D(1), 100.0)])
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO calendar_blocked_dates (blocked_date, label, kind) VALUES (?, 'Test holiday', 'holiday')", (D(1).isoformat(),))
    p = cp.plan(today=MON, start_cash=1_000_000, inflow_per_day=0, settings=settings())
    assert by_doc(p)["A1"].suggested_date == D(2)


def test_send_to_batch_fills_the_issue_checks_grid(db):
    a = make_creditor(db, name="Alpha")
    sid = batch(db, a, "OS-1", [("A1", D(0), 6000.0), ("A2", D(7), 5000.0)], memo=1000.0)
    p = cp.plan(today=MON, start_cash=1_000_000, inflow_per_day=0, settings=settings())
    ok, msg = cp.send_to_batch(p, sid)
    assert ok, msg
    d = load_park_draft(sid)
    net, _, remaining = get_batch_balance(sid)
    # exactly what the Issue-checks grid checks before it restores a draft
    assert d["mode"] == "By number of checks" and d["count"] == 2 and abs(d["balance"] - round(remaining, 2)) <= 0.005
    assert d["invoice_ids"] == tuple(get_invoices_with_remaining(sid)["invoice_id"])
    assert d["rows"] == [(D(0).isoformat(), 5000.0), (D(7).isoformat(), 5000.0)]
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE order_statements SET total_amount = total_amount + 100 WHERE statement_id = ?", (sid,))
    assert not cp.send_to_batch(p, sid)[0]                                    # the batch changed since the plan


def test_settings_round_trip(db):
    cp.save_settings(settings(max_days_late=10, daily_limit=250_000, daily_max_checks=6, priority={3, 5}, buffer=100_000))
    s = cp.load_settings()
    assert (s.max_days_late, s.daily_limit, s.daily_max_checks, s.priority, s.buffer) == (10, 250_000, 6, {3, 5}, 100_000)


def test_already_short_before_new_checks_is_explained(db):
    a, b = make_creditor(db, name="Alpha"), make_creditor(db, name="Beta")
    batch(db, a, "OS-A", [("A1", D(0), 100.0)])
    other = batch(db, b, "OS-B", [("B1", D(1), 5000.0)])
    with sqlite3.connect(db) as conn:                     # Beta's 5,000 check is already issued for Tuesday
        conn.execute("INSERT INTO checks_issued (creditor_id, bank_account_id, statement_id, check_date, amount, status) VALUES (?, 1, ?, ?, 5000, 'Active')",
                     (b, other, D(1).isoformat()))
    p = cp.plan(today=MON, start_cash=3_000, inflow_per_day=0, settings=settings())
    assert p.summary["already_short"] == (D(1), -2000.0)
    assert by_doc(p)["A1"].status == "Doesn't fit"         # even 100 does not fit: the balance is already under zero on Tuesday
