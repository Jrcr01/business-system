"""Other income (owner, 2026-10-13: "i want to add other income"; go 2026-10-15): money received that is not a sale -- rent received, a supplier's rebate
or incentive. The mirror of engines/expense_rules.py: one line each, a kind from the owner's own list, from whom and what for; taken back only with a
reason, nothing deleted. Adds to the month's Income statement; not to the drawer on Deposits (his answer). Recorded by whoever records Expenses."""
from __future__ import annotations

from engines.clock import manila_today
from engines.common import get_conn, write_tx
from engines.db_engine import next_doc_number
from engines.expense_rules import paid_from_choices as received_in_choices      # the drawer, each bank account, GCash
from engines.permissions import require as require_permission
from engines.sales_rules import _as_date, _text

__all__ = ["kinds", "save_kind", "received_in_choices", "add", "void", "listing", "month_by_kind"]


def kinds(all_rows: bool = False) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT kind_id, name, is_active, sort FROM income_kinds ORDER BY sort, name").fetchall()
    return [{"kind_id": int(a), "name": b, "active": bool(c), "sort": int(d)} for a, b, c, d in rows if all_rows or c]


def save_kind(kind_id, name: str, active: bool = True) -> tuple[bool, str]:
    """The owner's list (settings.edit): add or rename a kind; switching one off keeps its history."""
    allowed, why = require_permission("settings.edit")
    if not allowed:
        return False, why
    name = _text(name)
    if not name:
        return False, "Type the kind's name."
    with write_tx() as conn:
        if kind_id:
            conn.execute("UPDATE income_kinds SET name = ?, is_active = ? WHERE kind_id = ?", (name, int(active), int(kind_id)))
        else:
            if conn.execute("SELECT 1 FROM income_kinds WHERE lower(name) = ?", (name.lower(),)).fetchone():
                return False, f"{name} is already on the list."
            top = conn.execute("SELECT COALESCE(MAX(sort), 0) + 1 FROM income_kinds").fetchone()[0]
            conn.execute("INSERT INTO income_kinds (name, sort, created_by) VALUES (?, ?, acting_user())", (name, top))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'INCOME_KIND_SAVED', 'income_kinds', ?, ?)",
                     (kind_id, name + ("" if active else " (switched off)")))
    return True, f"{name} saved."


def add(income_date, kind_id, received_in: str, detail: str, amount) -> tuple[bool, str, int | None]:
    allowed, why = require_permission("expenses.use")
    if not allowed:
        return False, why, None
    day = _as_date(income_date)
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
    where = _text(received_in) or "Cash (drawer)"
    with write_tx() as conn:
        kind = conn.execute("SELECT name FROM income_kinds WHERE kind_id = ? AND is_active = 1", (int(kind_id or 0),)).fetchone()
        if not kind:
            return False, "Pick the kind.", None
        number = f"OI-{next_doc_number(conn, 'OI', 0):04d}"
        iid = conn.execute("""INSERT INTO other_income (inc_no, income_date, kind_id, received_in, detail, amount, created_by)
                              VALUES (?, ?, ?, ?, ?, ?, acting_user()) RETURNING income_id""",
                           (number, str(day), int(kind_id), where, _text(detail) or None, value)).fetchone()[0]
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'OTHER_INCOME_SAVED', 'other_income', ?, ?)",
                     (iid, f"{number} · {kind[0]} ₱{value:,.2f} into {where}" + (f" — {_text(detail)}" if _text(detail) else "")))
    return True, f"{number} saved: {kind[0]} ₱{value:,.2f}.", int(iid)


def void(income_id: int, reason: str) -> tuple[bool, str]:
    allowed, why = require_permission("expenses.use")
    if not allowed:
        return False, why
    if not _text(reason):
        return False, "Say why it is taken back."
    with write_tx() as conn:
        row = conn.execute("SELECT inc_no, voided_at FROM other_income WHERE income_id = ?", (int(income_id),)).fetchone()
        if not row:
            return False, "No such line."
        if row[1]:
            return False, "Already taken back."
        conn.execute("UPDATE other_income SET voided_at = CURRENT_TIMESTAMP, voided_by = acting_user(), void_reason = ? WHERE income_id = ?",
                     (_text(reason), int(income_id)))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'OTHER_INCOME_VOIDED', 'other_income', ?, ?)",
                     (int(income_id), f"{row[0]}: {_text(reason)}"))
    return True, f"{row[0]} taken back."


def listing(month: str | None = None) -> list[dict]:
    """The month's lines ('2026-10'), newest first."""
    where, params = ("WHERE substr(o.income_date, 1, 7) = ?", [month]) if month else ("", [])
    with get_conn() as conn:
        rows = conn.execute(f"""SELECT o.income_id, o.inc_no, o.income_date, k.name, o.received_in, o.detail, o.amount, o.voided_at, o.void_reason, o.created_by
                                FROM other_income o JOIN income_kinds k ON k.kind_id = o.kind_id {where}
                                ORDER BY o.income_date DESC, o.income_id DESC""", params).fetchall()
    return [{"income_id": int(a), "inc_no": b, "day": c, "kind": d, "received_in": e, "detail": f or "", "amount": float(g),
             "void": bool(h), "void_reason": i or "", "by": j or ""} for a, b, c, d, e, f, g, h, i, j in rows]


def month_by_kind(month: str, conn=None) -> list[tuple[str, float]]:
    sql = """SELECT k.name, COALESCE(SUM(o.amount), 0) FROM other_income o JOIN income_kinds k ON k.kind_id = o.kind_id
             WHERE substr(o.income_date, 1, 7) = ? AND o.voided_at IS NULL GROUP BY k.name ORDER BY 2 DESC"""
    try:
        if conn is not None:
            rows = conn.execute(sql, (month,)).fetchall()
        else:
            with get_conn() as c:
                rows = c.execute(sql, (month,)).fetchall()
    except Exception:                                   # noqa: BLE001 -- a database from before migration 104
        return []
    return [(a, round(float(b), 2)) for a, b in rows]
