"""Purchasing tables (migration 061): they exist in a fresh database, enforce their rules, and hang off the existing creditors / invoices."""
import sqlite3

import pytest

from conftest import make_creditor


def _con(db):
    c = sqlite3.connect(db)
    c.execute("PRAGMA foreign_keys = ON")
    return c


def test_the_purchasing_tables_exist(db):
    names = {r[0] for r in _con(db).execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"supplier_price_list", "purchase_orders", "purchase_order_lines", "po_receipts"} <= names


def test_a_price_is_one_row_per_item_per_effective_date(db):
    cid = make_creditor(db)
    c = _con(db)
    c.execute("INSERT INTO supplier_price_list (creditor_id, item_name, unit, unit_price, effective_date) VALUES (?, 'Tire 175/70R13', 'pc', 1850, '2026-10-01')", (cid,))
    c.execute("INSERT INTO supplier_price_list (creditor_id, item_name, unit, unit_price, effective_date) VALUES (?, 'Tire 175/70R13', 'pc', 1900, '2026-11-01')", (cid,))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO supplier_price_list (creditor_id, item_name, unit, unit_price, effective_date) VALUES (?, 'Tire 175/70R13', 'pc', 1999, '2026-10-01')", (cid,))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO supplier_price_list (creditor_id, item_name, unit_price, effective_date) VALUES (?, 'Bad', -1, '2026-10-01')", (cid,))


def test_a_purchase_order_needs_a_real_supplier_a_known_status_and_a_unique_number(db):
    cid = make_creditor(db)
    c = _con(db)
    c.execute("INSERT INTO purchase_orders (po_no, creditor_id, order_date) VALUES ('DRAFT-PO-1', ?, '2026-10-01')", (cid,))
    assert c.execute("SELECT status FROM purchase_orders").fetchone()[0] == "DRAFT"
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO purchase_orders (po_no, creditor_id, order_date) VALUES ('DRAFT-PO-1', ?, '2026-10-02')", (cid,))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO purchase_orders (po_no, creditor_id, order_date) VALUES ('X', 99999, '2026-10-02')")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO purchase_orders (po_no, creditor_id, order_date, status) VALUES ('Y', ?, '2026-10-02', 'SHIPPED')", (cid,))


def test_po_lines_and_receipts_hang_off_their_order_and_the_ap_invoice(db):
    cid = make_creditor(db)
    c = _con(db)
    po = c.execute("INSERT INTO purchase_orders (po_no, creditor_id, order_date) VALUES ('P1', ?, '2026-10-01')", (cid,)).lastrowid
    c.execute("INSERT INTO purchase_order_lines (po_id, item_name, qty, est_unit_price, est_amount) VALUES (?, 'Tire', 10, 1850, 18500)", (po,))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO purchase_order_lines (po_id, item_name, qty) VALUES (?, 'Tire', 0)", (po,))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO purchase_order_lines (po_id, item_name, qty) VALUES (99999, 'Tire', 1)")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO po_receipts (po_id, received_date, actual_amount, invoice_id) VALUES (?, '2026-10-05', 100, 99999)", (po,))
    c.execute("INSERT INTO po_receipts (po_id, received_date, supplier_invoice_no, actual_amount) VALUES (?, '2026-10-05', 'INV-77', 19000)", (po,))
    assert c.execute("SELECT actual_amount FROM po_receipts").fetchone()[0] == 19000
