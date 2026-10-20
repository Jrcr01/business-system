"""Expenses / petty cash (owner, 2026-10-11): anything paid from the drawer or the bank that is not a supplier payable. One line each, a category
from the owner's own list (the list is the owner's to edit), who took it and why. Taken back only with a reason; nothing deleted. Feeds the
Deposits page (cash taken − drawer expenses = what should reach the bank) and the month's Income statement."""
from __future__ import annotations

from engines.clock import manila_today
from engines.common import get_conn, write_tx
from engines.db_engine import next_doc_number
from engines.permissions import require as require_permission
from engines.sales_rules import _as_date, _text


def categories(all_rows: bool = False) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT category_id, name, is_active, sort FROM expense_categories ORDER BY sort, name").fetchall()
    return [{"category_id": int(a), "name": b, "active": bool(c), "sort": int(d)} for a, b, c, d in rows if all_rows or c]


def save_category(category_id, name: str, active: bool = True) -> tuple[bool, str]:
    """The owner's list (settings.edit): add or rename a category; switching one off keeps its history."""
    allowed, why = require_permission("settings.edit")
    if not allowed:
        return False, why
    name = _text(name)
    if not name:
        return False, "Type the category's name."
    with write_tx() as conn:
        if category_id:
            conn.execute("UPDATE expense_categories SET name = ?, is_active = ? WHERE category_id = ?", (name, int(active), int(category_id)))
        else:
            if conn.execute("SELECT 1 FROM expense_categories WHERE lower(name) = ?", (name.lower(),)).fetchone():
                return False, f"{name} is already on the list."
            top = conn.execute("SELECT COALESCE(MAX(sort), 0) + 1 FROM expense_categories").fetchone()[0]
            conn.execute("INSERT INTO expense_categories (name, sort, created_by) VALUES (?, ?, acting_user())", (name, top))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'EXPENSE_CATEGORY_SAVED', 'expense_categories', ?, ?)",
                     (category_id, name + ("" if active else " (switched off)")))
    return True, f"{name} saved."


def paid_from_choices() -> list[str]:
    """The drawer, each bank account by name, GCash."""
    with get_conn() as conn:
        banks = [r[0] for r in conn.execute("SELECT account_name FROM bank_accounts ORDER BY bank_account_id")]
    return ["Cash (drawer)"] + banks + ["GCash"]


def add(expense_date, category_id, paid_from: str, detail: str, amount) -> tuple[bool, str, int | None]:
    allowed, why = require_permission("expenses.use")
    if not allowed:
        return False, why, None
    day = _as_date(expense_date)
    if day is None:
        return False, "Pick the date.", None
    if day > manila_today():
        return False, "The date cannot be after today.", None
    raw = str(amount or "").replace(",", "").strip()
    try:
        value = round(float(raw), 2)
    except ValueError:
        return False, f"'{raw}' is not an amount.", None
    if value <= 0:
        return False, "The amount must be above zero.", None
    source = _text(paid_from) or "Cash (drawer)"
    with write_tx() as conn:
        cat = conn.execute("SELECT name FROM expense_categories WHERE category_id = ? AND is_active = 1", (int(category_id or 0),)).fetchone()
        if not cat:
            return False, "Pick the category.", None
        from engines.company import number as _co_number
        number = _co_number("num_exp", next_doc_number(conn, 'EXP', 0))
        eid = conn.execute("""INSERT INTO expenses (exp_no, expense_date, category_id, paid_from, detail, amount, created_by)
                              VALUES (?, ?, ?, ?, ?, ?, acting_user()) RETURNING expense_id""",
                           (number, str(day), int(category_id), source, _text(detail) or None, value)).fetchone()[0]
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'EXPENSE_SAVED', 'expenses', ?, ?)",
                     (eid, f"{number} · {cat[0]} ₱{value:,.2f} from {source}" + (f" — {_text(detail)}" if _text(detail) else "")))
    return True, f"{number} saved: {cat[0]} ₱{value:,.2f}.", int(eid)


def void(expense_id: int, reason: str) -> tuple[bool, str]:
    allowed, why = require_permission("expenses.use")
    if not allowed:
        return False, why
    if not _text(reason):
        return False, "Say why it is taken back."
    with write_tx() as conn:
        row = conn.execute("SELECT exp_no, voided_at FROM expenses WHERE expense_id = ?", (int(expense_id),)).fetchone()
        if not row:
            return False, "No such expense."
        if row[1]:
            return False, "Already taken back."
        conn.execute("UPDATE expenses SET voided_at = CURRENT_TIMESTAMP, voided_by = acting_user(), void_reason = ? WHERE expense_id = ?",
                     (_text(reason), int(expense_id)))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'EXPENSE_VOIDED', 'expenses', ?, ?)",
                     (int(expense_id), f"{row[0]}: {_text(reason)}"))
    return True, f"{row[0]} taken back."


def listing(month: str | None = None, day=None) -> list[dict]:
    """The lines, newest first: a month ('2026-10') or one day."""
    where, params = "", []
    if day:
        where, params = "WHERE e.expense_date = ?", [str(_as_date(day))]
    elif month:
        where, params = "WHERE substr(e.expense_date, 1, 7) = ?", [month]
    with get_conn() as conn:
        rows = conn.execute(f"""SELECT e.expense_id, e.exp_no, e.expense_date, c.name, e.paid_from, e.detail, e.amount, e.voided_at, e.void_reason, e.created_by
                                FROM expenses e JOIN expense_categories c ON c.category_id = e.category_id {where}
                                ORDER BY e.expense_date DESC, e.expense_id DESC""", params).fetchall()
    return [{"expense_id": int(a), "exp_no": b, "day": c, "category": d, "paid_from": e, "detail": f or "", "amount": float(g),
             "void": bool(h), "void_reason": i or "", "by": j or ""} for a, b, c, d, e, f, g, h, i, j in rows]


def month_by_category(month: str) -> list[tuple[str, float]]:
    with get_conn() as conn:
        rows = conn.execute("""SELECT c.name, COALESCE(SUM(e.amount), 0) FROM expenses e JOIN expense_categories c ON c.category_id = e.category_id
                               WHERE substr(e.expense_date, 1, 7) = ? AND e.voided_at IS NULL GROUP BY c.name ORDER BY 2 DESC""", (month,)).fetchall()
    return [(a, round(float(b), 2)) for a, b in rows]


def drawer_by_day(since) -> dict[str, float]:
    """Cash spent from the drawer per day (for the Deposits page: cash taken − this = what should reach the bank)."""
    with get_conn() as conn:
        rows = conn.execute("""SELECT expense_date, SUM(amount) FROM expenses
                               WHERE paid_from = 'Cash (drawer)' AND voided_at IS NULL AND expense_date >= ? GROUP BY expense_date""", (str(since),)).fetchall()
    return {str(a): round(float(b), 2) for a, b in rows}
