"""The places the Sales / receivables and Stock modules will plug into (2026-10-03), with just enough rules to keep them honest. No screen uses them yet.
  * add_customer / list_customers           -- who we sell to, apart from suppliers and lenders
  * set_selling_price / selling_price_on    -- what we SELL an item for, dated; apart from the supplier cost lists
  * tag_deposit / deposits_by_kind          -- a bank deposit tagged as sales, so real deposits can replace the forecast's guess later"""
from __future__ import annotations

from datetime import date

import pandas as pd

from engines.clock import manila_today
from engines.common import get_conn, write_tx
from engines.permissions import require as require_permission

CUSTOMER_TYPES = ("RETAIL", "FLEET", "DEALER", "OTHER")


def add_customer(name: str, customer_type: str = "RETAIL", terms: str = "", contact_person: str = "", contact_number: str = "", email: str = "") -> tuple[bool, str, int | None]:
    ok, why = require_permission("sales.use")
    if not ok:
        return False, why, None
    name = " ".join((name or "").split())
    if not name:
        return False, "Enter the customer's name.", None
    if customer_type not in CUSTOMER_TYPES:
        return False, "Pick a customer type.", None
    with write_tx() as conn:
        if conn.execute("SELECT 1 FROM customers WHERE LOWER(customer_name) = LOWER(?)", (name,)).fetchone():
            return False, f"{name} is already a customer.", None
        cid = conn.execute("INSERT INTO customers (customer_name, customer_type, terms, contact_person, contact_number, email, created_by) VALUES (?, ?, ?, ?, ?, ?, acting_user()) RETURNING customer_id",
                           (name, customer_type, terms or None, contact_person or None, contact_number or None, email or None)).fetchone()[0]
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CUSTOMER_ADDED', 'customers', ?, ?)", (cid, name))
    return True, f"{name} added.", int(cid)


def list_customers(active_only: bool = True) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query("SELECT customer_id, customer_name, customer_type, terms, is_active FROM customers" + (" WHERE is_active = 1" if active_only else "") + " ORDER BY customer_name", conn)


def set_selling_price(item_id: int, price: float, effective_date: date | None = None) -> tuple[bool, str]:
    ok, why = require_permission("inventory.use")
    if not ok:
        return False, why
    if price is None or float(price) < 0:
        return False, "The selling price cannot be negative."
    day = effective_date or manila_today()
    with write_tx() as conn:
        if not conn.execute("SELECT 1 FROM items WHERE id = ?", (int(item_id),)).fetchone():
            return False, "Item not found."
        conn.execute("INSERT INTO item_prices (item_id, price, effective_date, created_by) VALUES (?, ?, ?, acting_user()) "
                     "ON CONFLICT (item_id, effective_date) DO UPDATE SET price = excluded.price, is_active = 1", (int(item_id), round(float(price), 2), str(day)))
    return True, f"Selling price {float(price):,.2f} from {day}."


def selling_price_on(item_id: int, on: date | None = None) -> float | None:
    """The selling price in force on a day (the latest one that started on or before it), or None when the item has none."""
    with get_conn() as conn:
        row = conn.execute("SELECT price FROM item_prices WHERE item_id = ? AND is_active = 1 AND effective_date <= ? ORDER BY effective_date DESC LIMIT 1",
                           (int(item_id), str(on or manila_today()))).fetchone()
    return float(row[0]) if row else None


def tag_deposit(txn_id: int, kind: str | None) -> tuple[bool, str]:
    """Tags a bank line that is money IN as 'sales' or 'other' (None clears it). Only a credit can be a deposit."""
    ok, why = require_permission("bank.use")
    if not ok:
        return False, why
    if kind not in (None, "sales", "other"):
        return False, "A deposit is tagged sales or other."
    with write_tx() as conn:
        row = conn.execute("SELECT credit FROM bank_transactions WHERE txn_id = ?", (int(txn_id),)).fetchone()
        if not row:
            return False, "Bank line not found."
        if float(row[0] or 0) <= 0:
            return False, "Only money coming in can be tagged as a deposit."
        conn.execute("UPDATE bank_transactions SET deposit_kind = ? WHERE txn_id = ?", (kind, int(txn_id)))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'DEPOSIT_TAGGED', 'bank_transactions', ?, ?)", (int(txn_id), str(kind)))
    return True, "Tagged." if kind else "Tag cleared."


def deposits_by_kind(since: date, until: date) -> dict:
    """{'sales': total, 'other': total, 'untagged': total} of the money in between two dates, from the bank statement (the truth)."""
    with get_conn() as conn:
        rows = conn.execute("SELECT COALESCE(deposit_kind, 'untagged'), SUM(credit) FROM bank_transactions WHERE credit > 0 AND posting_date BETWEEN ? AND ? GROUP BY 1", (str(since), str(until))).fetchall()
    return {"sales": 0.0, "other": 0.0, "untagged": 0.0} | {k: float(v) for k, v in rows}
