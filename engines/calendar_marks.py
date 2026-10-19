"""Marking days on the payment calendar (2026-10-03). Three kinds:
  holiday   a bank day off (calendar_blocked_dates, kind 'holiday')
  custom    a no-check day, treated like a holiday (calendar_blocked_dates, kind 'custom')
  note      a coloured reminder (a supplier visit, payday); it moves no date (calendar_notes)
A holiday or a no-check day is treated like a weekend (owner, 2026-10-03): a payment due on it is PAID on the next bank day, in Payables due, the payment calendar, Payment run and the
reports (reports_service.next_banking_day), and a new purchase order and the check-date planner skip it. An invoice's stored due date and a check already written keep their dates.
Owner and Payables mark (the 'calendar.mark' action); everyone who sees the calendar sees the marks. Every change is audited with the acting email."""
from __future__ import annotations

from datetime import date

from engines.common import get_conn, write_tx
from engines.permissions import require as require_permission

KINDS = {"holiday": "Holiday (bank day off)", "custom": "No-check day", "note": "Note only"}

_BLOCKED: dict = {}          # db path -> (file stamp, frozenset of ISO dates); read once until the database changes (next_banking_day asks for it once per payment)


def blocked_days() -> frozenset:
    """Every holiday and no-check day, as ISO dates. Notes are not here: they move nothing. Checked once per page view (a page asks thousands of times)."""
    from engines.request_memo import memo
    return memo("blocked_days", _blocked_days)


def _blocked_days() -> frozenset:
    from engines import common
    from engines.settings_store import _stamp
    path = common.DB_PATH                                   # the same file get_conn() opens by default
    stamp = _stamp(path)
    hit = _BLOCKED.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    with get_conn() as conn:
        days = frozenset(str(r[0])[:10] for r in conn.execute("SELECT blocked_date FROM calendar_blocked_dates"))
    _BLOCKED[path] = (stamp, days)
    return days


def notes_for_month(year: int, month: int) -> dict:
    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1)
    with get_conn() as conn:
        rows = conn.execute("SELECT note_date, label FROM calendar_notes WHERE note_date >= ? AND note_date < ?", (str(start), str(end))).fetchall()
    return {date.fromisoformat(str(r[0])[:10]): r[1] for r in rows}


def marks_for_month(year: int, month: int) -> dict:
    """{date: {'kind': 'holiday'|'custom'|'note', 'label': text}} for the month."""
    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1)
    with get_conn() as conn:
        rows = conn.execute("SELECT blocked_date, kind, label FROM calendar_blocked_dates WHERE blocked_date >= ? AND blocked_date < ?", (str(start), str(end))).fetchall()
    out = {date.fromisoformat(str(r[0])[:10]): {"kind": r[1], "label": r[2] or ""} for r in rows}
    for d, label in notes_for_month(year, month).items():
        out.setdefault(d, {"kind": "note", "label": label})
    return out


def mark_day(day: date, kind: str, label: str = "") -> tuple[bool, str]:
    ok, why = require_permission("calendar.mark")
    if not ok:
        return False, why
    if kind not in KINDS:
        return False, "Pick holiday, no-check day or note."
    label = " ".join((label or "").split())
    if kind == "note" and not label:
        return False, "Type what the note says."
    with write_tx() as conn:
        conn.execute("DELETE FROM calendar_notes WHERE note_date = ?", (str(day),))
        if kind == "note":
            conn.execute("DELETE FROM calendar_blocked_dates WHERE blocked_date = ?", (str(day),))
            conn.execute("INSERT INTO calendar_notes (note_date, label, created_by) VALUES (?, ?, acting_user())", (str(day), label))
        else:
            conn.execute("INSERT INTO calendar_blocked_dates (blocked_date, label, kind) VALUES (?, ?, ?) "
                         "ON CONFLICT(blocked_date) DO UPDATE SET label = excluded.label, kind = excluded.kind", (str(day), label or None, kind))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, new_values) VALUES (acting_user(), 'CALENDAR_MARKED', 'calendar', ?)",
                     (f"{day}: {KINDS[kind]}" + (f" ({label})" if label else ""),))
    return True, f"{day:%m/%d/%Y} marked: {KINDS[kind].lower()}."


def unmark_day(day: date) -> tuple[bool, str]:
    ok, why = require_permission("calendar.mark")
    if not ok:
        return False, why
    with write_tx() as conn:
        n = conn.execute("DELETE FROM calendar_notes WHERE note_date = ?", (str(day),)).rowcount
        n += conn.execute("DELETE FROM calendar_blocked_dates WHERE blocked_date = ?", (str(day),)).rowcount
        if n:
            conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, new_values) VALUES (acting_user(), 'CALENDAR_UNMARKED', 'calendar', ?)", (str(day),))
    return True, f"{day:%m/%d/%Y} is no longer marked." if n else "That day was not marked."


def payments_on(day: date) -> int:
    """How many payments (checks, dues) are shown on this day: the ones that will be paid the next bank day if the day is marked as a holiday or a no-check day."""
    import pandas as pd
    from engines.reports_service import get_payment_calendar_items
    items = get_payment_calendar_items()
    if items is None or items.empty:
        return 0
    return int(((pd.to_datetime(items["actual_payment_date"]).dt.date == day) & (items["state"] == "Due")).sum())


def orders_planned_on(day: date) -> int:
    """How many open purchase orders are planned to be paid on this day. They keep their dates if the day is marked; only new orders skip it."""
    with get_conn() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE expected_pay_date = ? AND status IN ('DRAFT', 'ORDERED', 'PARTIAL')", (str(day),)).fetchone()[0])
