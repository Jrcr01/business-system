"""Beginning balance (from MS Access) (owner, 2026-09-30: "there should be a beginning balance instead, no archiving").
The 36 batches brought over from MS Access hold checks but NO invoices (the batch total was set equal to its checks), so every all-time report showed
invoices <> checks issued. Each such batch now gets ONE flagged line, "Beginning balance (from MS Access)", equal to its total and tied to its checks.
It counts in balances and histories; it is kept OUT of aging, days-to-pay, the overview's invoice count, the saved-report invoice list and the
duplicate / vendor-statement matchers. Nothing already recorded changes: no check, batch total or status is touched."""
import sqlite3
from datetime import date

import app
from conftest import fetchone, make_creditor, make_statement
from engines.beginning_balance_service import BEGINNING_REF, apply_beginning_balances, preview_beginning_balances
from engines.dashboard_kpi_service import get_average_dpo_suppliers
from engines.report_builder import DATASETS
from engines.reports_service import get_invoice_rundown, get_supplier_ap_aging_report, get_supplier_history
from engines.statement_rules import get_batch_balance


def _legacy_batch(db, n, checks, name="Express Tire", total=None, cancelled=()):
    """A LEGACY-MS-ACCESS batch the way the import left it: total set to its checks, no invoices."""
    creditor_id = make_creditor(db, name=name)
    statement_id = make_statement(db, creditor_id)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE order_statements SET statement_ref = ?, status = 'Issued', total_amount = ? WHERE statement_id = ?",
                     (f"LEGACY-MS-ACCESS-{n}", sum(checks) if total is None else total, statement_id))
        for i, amt in enumerate(checks):
            conn.execute("INSERT INTO checks_issued (bank_account_id, statement_id, creditor_id, check_number, amount, check_date, status) VALUES (1, ?, ?, ?, ?, ?, 'Active')",
                         (statement_id, creditor_id, f"39900{n:02d}{i:03d}", amt, f"2026-1{i % 3}-05"))
        for j, amt in enumerate(cancelled):
            conn.execute("INSERT INTO checks_issued (bank_account_id, statement_id, creditor_id, check_number, amount, check_date, status) VALUES (1, ?, ?, ?, ?, '2026-12-01', 'Cancelled')",
                         (statement_id, creditor_id, f"39901{n:02d}{j:03d}", amt))
    return creditor_id, statement_id


def test_the_preview_lists_each_legacy_batch_without_invoices_and_changes_nothing(db):
    cid, sid = _legacy_batch(db, 1, [1000.0, 2000.0, 3000.0], cancelled=(500.0,))

    df = preview_beginning_balances()

    assert len(df) == 1
    row = df.iloc[0]
    assert (row["batch"], row["supplier"], row["batch_total"], row["checks_total"], row["checks"], row["ok"]) == ("LEGACY-MS-ACCESS-1", "Express Tire", 6000.0, 6000.0, 3, True)
    assert fetchone(db, "SELECT COUNT(*) FROM invoices")[0] == 0                      # a preview writes nothing


def test_a_batch_whose_total_differs_from_its_checks_is_left_for_the_owner(db):
    _legacy_batch(db, 2, [1000.0, 2000.0], total=3500.0)

    row = preview_beginning_balances().iloc[0]
    assert not row["ok"] and "3,500.00" in row["note"]
    ok, msg = apply_beginning_balances()
    assert ok and fetchone(db, "SELECT COUNT(*) FROM invoices")[0] == 0                # nothing added for it


def test_applying_adds_one_flagged_line_equal_to_the_batch_and_ties_every_active_check(db):
    cid, sid = _legacy_batch(db, 3, [1000.0, 2000.0, 3000.0], cancelled=(500.0,))
    before_checks = fetchone(db, "SELECT COUNT(*), ROUND(SUM(amount), 2), GROUP_CONCAT(check_id || status) FROM checks_issued")

    ok, msg = apply_beginning_balances()

    assert ok, msg
    inv = fetchone(db, "SELECT doc_ref, amount, gross_amount, is_opening_balance, statement_id FROM invoices")
    assert inv == (BEGINNING_REF, 6000.0, 6000.0, 1, sid)
    assert fetchone(db, "SELECT total_amount FROM order_statements WHERE statement_id = ?", (sid,))[0] == 6000.0            # the batch total did not move
    assert fetchone(db, "SELECT COUNT(*), SUM(amount) FROM invoice_check_allocations") == (3, 6000.0)                    # the 3 active checks, not the cancelled one
    assert get_batch_balance(sid)[2] == 0.0
    assert fetchone(db, "SELECT COUNT(*), ROUND(SUM(amount), 2), GROUP_CONCAT(check_id || status) FROM checks_issued") == before_checks   # no check was touched
    assert "Beginning balance" in fetchone(db, "SELECT new_values FROM audit_logs WHERE user_action = 'BEGINNING_BALANCE_ADDED'")[0]


def test_applying_twice_adds_nothing_the_second_time(db):
    _legacy_batch(db, 4, [1000.0])
    apply_beginning_balances()

    ok, msg = apply_beginning_balances()

    assert ok and fetchone(db, "SELECT COUNT(*) FROM invoices")[0] == 1 and preview_beginning_balances().empty


def test_a_batch_that_already_has_invoices_is_not_touched(db):
    cid, sid = _legacy_batch(db, 5, [1000.0])
    app.add_invoice(cid, "REAL-1", date(2026, 9, 1), date(2026, 10, 1), 1000.0, [], sid)

    assert preview_beginning_balances().empty
    apply_beginning_balances()
    assert fetchone(db, "SELECT COUNT(*) FROM invoices")[0] == 1


def test_invoices_and_checks_now_balance_in_the_supplier_history(db):
    cid, sid = _legacy_batch(db, 6, [1000.0, 2000.0])
    apply_beginning_balances()

    statements, invoices, checks = get_supplier_history(cid)

    assert list(invoices["doc_ref"]) == [BEGINNING_REF]
    assert float(invoices["amount"].sum()) == float(checks[checks["status"] == "Active"]["amount"].sum()) == 3000.0


def test_the_line_stays_out_of_aging_days_to_pay_the_overview_and_the_saved_report_invoice_list(db):
    cid, sid = _legacy_batch(db, 7, [1000.0, 2000.0])
    apply_beginning_balances()

    assert get_supplier_ap_aging_report().empty                                       # not an overdue-by-days invoice
    assert get_average_dpo_suppliers() is None                                        # no due date to measure payment lag against
    from engines.overview_report_service import _new_business
    from datetime import timedelta
    with sqlite3.connect(db) as conn:
        today = date.today()
        assert _new_business(conn, today - timedelta(days=400), today + timedelta(days=400))["invoices"] == 0
    assert "Beginning balance" not in " ".join(str(v) for v in _run_dataset(db, "invoices"))


def _run_dataset(db, key):
    with sqlite3.connect(db) as conn:
        return [r[0] for r in conn.execute(DATASETS[key].sql.replace("ORDER BY i.invoice_date, i.invoice_id", ""))]


def test_the_invoice_rundown_still_shows_it_as_a_balance(db):
    cid, sid = _legacy_batch(db, 8, [1000.0])
    apply_beginning_balances()

    df = get_invoice_rundown(cid)

    assert BEGINNING_REF in " ".join(str(v) for v in df.astype(str).values.ravel())
