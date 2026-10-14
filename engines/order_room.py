"""Room for new orders (owner, 2026-10-15: "the logic of this PO is to monitor orders because this is the variable payable"). Checks a supplier has
collected, bank-loan dues and card statements are fixed; only orders can be steered. So, for each week within the reach of a new order (the longest
supplier terms plus a month, six months), the order page shows:

    room = expected cash in  -  fixed payables  -  orders already placed (and requests waiting)

Expected cash in = customer checks and invoices falling due that week (firm) + the usual cash-like takings per weekday, averaged over the last 8
weeks (engines/cash_in.in_by_day, as the Calendar shows it). A new order is placed into a week with room. Beyond the window nothing you decide today
can land, so those days are not judged (grey, no confirmation) and card reserves are not placed there either."""
from __future__ import annotations

from datetime import date, timedelta

from engines.clock import manila_today

WINDOW_KEY = "po_window_months"
FIXED = ("Supplier checks", "Lender checks", "Bank loans", "Credit cards")


def window_months() -> int:
    from engines.settings_registry import int_value
    return int_value(WINDOW_KEY)


def window_end(today: date | None = None) -> date | None:
    """The last day a new order can fall due: today + the window; None when the window is 0 (every day is judged, as before)."""
    months = window_months()
    if months <= 0:
        return None
    today = today or manila_today()
    m = today.month - 1 + months
    y, m = today.year + m // 12, m % 12 + 1
    import calendar
    return date(y, m, min(today.day, calendar.monthrange(y, m)[1]))


def in_window(day: date, today: date | None = None) -> bool:
    end = window_end(today)
    return end is None or day <= end


def _week_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


HISTORY_DAYS = 56


def typed_week() -> float:
    from engines.settings_registry import value
    return float(value("po_cash_in_week") or 0)


def history_days(today: date | None = None) -> int:
    """How many days of recorded sales there are (the weekday average needs 8 weeks to mean anything)."""
    from engines.common import get_conn
    today = today or manila_today()
    with get_conn() as conn:
        first = conn.execute("SELECT MIN(sale_date) FROM sales WHERE status = 'SAVED' AND is_opening = 0").fetchone()[0]
    return (today - date.fromisoformat(str(first)[:10])).days + 1 if first else 0


def cash_in_known(today: date | None = None) -> str:
    """'history' (8+ weeks of sales: the weekday average), 'typed' (less than that, but a weekly figure is typed in Settings), or '' (not known:
    room is not shown, a false green being worse than none)."""
    days = history_days(today)
    if days >= HISTORY_DAYS:
        return "history"
    return "typed" if typed_week() > 0 else ""


def weeks(today: date | None = None, exclude_po_id: int | None = None, order_pay_day: date | None = None, order_amount: float = 0.0) -> dict:
    """The week strip: {'known', 'window_end', 'weeks': [{week_start, week_end, fixed, orders, cash_in, room, level, selected, this_order}]}.
    level: 'ok' (the order fits the room), 'tight' (it uses more than the room), 'over' (the week is short before it), '' (cash in not known)."""
    from engines.cash_in import in_by_day
    from engines.day_cap import loads
    from engines.po_rules import REQUEST_KIND
    today = today or manila_today()
    end = window_end(today) or (today + timedelta(days=183))
    first = _week_of(today)
    starts = []
    w = first
    while w <= end:
        starts.append(w)
        w += timedelta(weeks=1)
    rows = loads(today, end, exclude_po_id, requests=True)
    known = cash_in_known(today)
    cin = in_by_day(today, end) if known == "history" else {}
    typed = typed_week() if known == "typed" else 0.0
    out = []
    pay_week = _week_of(order_pay_day) if order_pay_day else None
    for ws in starts:
        we = ws + timedelta(days=6)
        mine = rows[(rows["day"] >= ws) & (rows["day"] <= we)] if not rows.empty else rows
        fixed = float(mine[mine["kind"].isin(FIXED)]["amount"].sum()) if not mine.empty else 0.0
        orders = float(mine[mine["kind"].isin(["Purchase orders", REQUEST_KIND])]["amount"].sum()) if not mine.empty else 0.0
        cash = sum(v["total"] for d, v in cin.items() if ws <= d <= we) if known == "history" else (typed if known == "typed" else 0.0)
        room = round(cash - fixed - orders, 2)
        this = float(order_amount or 0) if pay_week == ws else 0.0
        if not known:
            lvl = ""
        elif room < -0.005:
            lvl = "over"
        elif this > room + 0.005:
            lvl = "tight"
        else:
            lvl = "ok"
        out.append({"week_start": ws, "week_end": we, "fixed": round(fixed, 2), "orders": round(orders, 2), "cash_in": round(cash, 2), "room": room,
                    "after": round(room - this, 2), "level": lvl, "selected": pay_week == ws, "this_order": this})
    return {"known": known, "window_end": window_end(today), "weeks": out, "history_days": history_days(today)}
