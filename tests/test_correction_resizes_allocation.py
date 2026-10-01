"""Found 2026-10-01: correcting a printed check's amount (169,282 -> 169,292) left its invoice allocation at 169,282, so the invoice stayed 10.00 short. The correction
now carries the allocation with it."""
import sqlite3
from datetime import date, timedelta

from conftest import fetchone, make_creditor
from engines.check_rules import apply_check_correction
from engines.handwritten_check_service import record_handwritten_checks
from engines.statement_rules import add_invoice, create_order_statement


def _paid(db, invoice_amount, checks):
    sup = make_creditor(db, name="Express")
    sid = create_order_statement(sup)
    add_invoice(sup, "INV1", date.today(), date.today() + timedelta(days=30), invoice_amount, [], sid)
    d = date.today() + timedelta(days=30)
    ok, msg, ids = record_handwritten_checks(sup, [{"number": n, "date": d, "amount": a} for n, a in checks], statement_id=sid)
    assert ok, msg
    return sid, ids


def _alloc(db, check_id):
    return fetchone(db, "SELECT SUM(amount) FROM invoice_check_allocations WHERE check_id = ?", (check_id,))[0]


def test_raising_a_checks_amount_raises_its_allocation(db):
    sid, (cid,) = _paid(db, 169292.0, [("3992554299", 169282.0)])

    ok, msg = apply_check_correction(cid, "Amount", "169292", "typo")

    assert ok, msg
    assert fetchone(db, "SELECT amount FROM checks_issued WHERE check_id = ?", (cid,))[0] == 169292.0
    assert _alloc(db, cid) == 169292.0


def test_lowering_a_checks_amount_lowers_its_allocation(db):
    sid, (cid,) = _paid(db, 1000.0, [("3992554301", 1000.0)])

    ok, msg = apply_check_correction(cid, "Amount", "900", "typo")

    assert ok, msg
    assert _alloc(db, cid) == 900.0


def test_a_check_with_no_allocation_is_corrected_without_error(db):
    sid, (cid,) = _paid(db, 1000.0, [("3992554302", 600.0)])
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM invoice_check_allocations WHERE check_id = ?", (cid,))

    ok, msg = apply_check_correction(cid, "Amount", "700", "typo")

    assert ok, msg
    assert _alloc(db, cid) is None
