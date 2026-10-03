"""The four hard stops (2026-10-03): controls guide and record, they rarely block. These four are refused, always, whoever asks: a check above the batch balance, the same
invoice number twice for one supplier, a check number used twice, releasing a check that was never printed."""
import sqlite3
from datetime import date

import app
import pytest

from conftest import fetchone, make_creditor
from engines.acting_user import acting_as
from engines.check_rules import assign_check_numbers_manual, issue_check
from engines.pdc_release_service import mark_released
from engines.statement_rules import add_invoice


def _batch(db, gross=5000.0):
    cid = make_creditor(db, name="Hard Stop Supplier")
    sid = app.create_order_statement(cid)
    add_invoice(cid, "SI-1", date(2026, 10, 1), date(2026, 11, 1), gross, [], sid)
    return cid, sid


@pytest.mark.parametrize("role", ["admin", "owner", "payables"])
def test_stop_1_a_check_above_the_batch_balance_is_refused_for_every_role(db, role):
    cid, sid = _batch(db)
    with acting_as(f"{role}@example.com", role):
        assert issue_check(cid, 3000.0, date(2026, 11, 8), statement_id=sid)[0]
        ok, msg, _ = issue_check(cid, 2500.0, date(2026, 11, 9), statement_id=sid)
    assert not ok and "exceeds" in msg and fetchone(db, "SELECT COUNT(*) FROM checks_issued")[0] == 1


def test_stop_2_the_same_invoice_number_twice_for_one_supplier_is_refused_but_another_supplier_may_use_it(db):
    cid, sid = _batch(db)
    with pytest.raises(sqlite3.IntegrityError):
        add_invoice(cid, "SI-1", date(2026, 10, 2), date(2026, 11, 2), 100.0, [], sid)
    other = make_creditor(db, name="Another Supplier")
    add_invoice(other, "SI-1", date(2026, 10, 2), date(2026, 11, 2), 100.0, [], app.create_order_statement(other))


def test_stop_3_a_check_number_cannot_be_used_twice(db):
    cid, sid = _batch(db)
    with acting_as("owner@example.com", "owner"):
        ids = [issue_check(cid, 1000.0, date(2026, 11, 8 + i), statement_id=sid)[2] for i in range(2)]
        assert assign_check_numbers_manual({ids[0]: "5001"}, None)[0]
        ok, msg, _ = assign_check_numbers_manual({ids[1]: "5001"}, None)
        assert not ok and "already" in msg
        assert not assign_check_numbers_manual({ids[0]: "5002", ids[1]: "5002"}, None)[0]               # nor twice in the same pass


def test_stop_4_a_check_that_was_never_printed_cannot_be_released_by_any_role(db):
    cid, sid = _batch(db)
    with acting_as("owner@example.com", "owner"):
        chk = issue_check(cid, 1000.0, date(2026, 11, 8), statement_id=sid)[2]
    for role in ("admin", "owner", "payables", "releaser"):
        with acting_as(f"{role}@example.com", role):
            ok, msg = mark_released([chk], date(2026, 10, 12), "Collector")
            assert not ok, role
    assert fetchone(db, "SELECT released_date FROM checks_issued WHERE check_id = ?", (chk,))[0] is None


def test_a_missing_number_stays_missing_and_a_missing_text_is_empty_in_every_screen_query(db):
    """The query helper mixed empty text into number columns, which broke int() and the Arrow conversion (found by the every-workflow tests, 2026-10-03)."""
    from engines.db_engine import df_query
    a, b = make_creditor(db, name="No Days Supplier"), make_creditor(db, name="Five Days Supplier")
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE creditors SET lead_days = 5 WHERE creditor_id = ?", (b,))
    df = df_query("SELECT creditor_name, lead_days, tin FROM creditors WHERE creditor_id IN (?, ?) ORDER BY creditor_id", (a, b))
    assert str(df["lead_days"].dtype).startswith("float") and df["lead_days"].isna().tolist() == [True, False]       # one column, numbers only (NaN = not set)
    assert df["tin"].tolist() == ["", ""] or all(v in ("", None) for v in df["tin"])                                 # text: empty, as the screens expect
