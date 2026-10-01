"""Standard lists of records (plan F): each list runs on an empty and a filled database, shows plain columns, filters on Active, and the workbook has one sheet per list."""
import sqlite3
from datetime import date, timedelta

import app
from conftest import make_creditor
from engines.handwritten_check_service import record_handwritten_checks
from engines.lists_service import LISTS, all_lists, export_workbook, friendly_list, get_list, list_keys
from engines.statement_rules import add_invoice, create_order_statement


def test_every_list_runs_on_an_empty_database_and_has_plain_labels_for_every_column(db):
    for key in list_keys():
        df = get_list(key)
        assert list(df.columns) == [c for c in df.columns]
        missing = [c for c in df.columns if c not in LISTS[key]["labels"]]
        assert missing == [], (key, missing)
    assert len(all_lists()) == len(LISTS)


def test_suppliers_list_shows_balance_last_invoice_and_filters_inactive(db):
    keep, gone = make_creditor(db, name="Keep Supplier"), make_creditor(db, name="Old Supplier")
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE creditors SET is_active = 0, tin = '123-456' WHERE creditor_id = ?", (gone,))
    sid = create_order_statement(keep)
    add_invoice(keep, "I-1", date.today(), date.today() + timedelta(days=30), 1000.0, [], sid)
    ok, msg, _ = record_handwritten_checks(keep, [{"number": "3992000001", "date": date.today() + timedelta(days=20), "amount": 1000.0}], statement_id=sid)
    assert ok, msg

    everyone = friendly_list("suppliers")
    active = friendly_list("suppliers", active_only=True)

    assert set(everyone["Supplier"]) == {"Keep Supplier", "Old Supplier"} and set(active["Supplier"]) == {"Keep Supplier"}
    row = active.iloc[0]
    assert row["Still to pay"] == 1000.0 and row["Invoices"] == 1 and row["Batches"] == 1 and row["Last invoice"] == str(date.today())


def test_banks_list_shows_a_bank_used_for_loans_and_cards_once(db):
    a = make_creditor(db, name="BPI", creditor_type="BANK_LOAN")
    b = make_creditor(db, name="BPI", creditor_type="CREDIT_CARD")
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO bank_loans (creditor_id, loan_ref, status) VALUES (?, 'L1', 'Active')", (a,))
        conn.execute("INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit) VALUES (?, 'Gold', '1111', 1000)", (b,))
    out = get_list("banks")
    assert len(out) == 1 and out.iloc[0]["bank_loans"] == 1 and out.iloc[0]["cards"] == 1


def test_booklet_list_counts_used_spoiled_and_left(db):
    from engines.check_rules import log_spoiled_check
    sup = make_creditor(db, name="Sup")
    sid = create_order_statement(sup)
    add_invoice(sup, "I-1", date.today(), date.today() + timedelta(days=30), 500.0, [], sid)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO check_booklets (bank_account_id, booklet_code, start_number, end_number, next_available_number, date_received) VALUES (1, 'BK1', 3992000001, 3992000010, 3992000001, '2026-01-01')")
    record_handwritten_checks(sup, [{"number": "3992000003", "date": date.today() + timedelta(days=5), "amount": 500.0}], statement_id=sid)
    log_spoiled_check("3992000004", "torn")

    row = get_list("booklets").iloc[0]

    assert (row["leaves"], row["used"], row["spoiled"], row["guarantee"], row["left"]) == (10, 1, 1, 0, 8)


def test_the_workbook_has_one_sheet_per_list_with_plain_headers_and_formats(db):
    sheets, fmt = export_workbook()
    assert list(sheets) == [LISTS[k]["title"] for k in LISTS]
    assert "Supplier" in sheets["Suppliers"].columns and fmt["Still to pay"] == "money" and fmt["Last invoice"] == "date"
    from engines.export_service import excel_bytes
    assert excel_bytes(sheets, kinds=fmt)[:2] == b"PK"
