"""The cash count at closing (owner, 2026-10-19, review item 8: "controls for error"): the cash in the drawer, less the change fund kept there, against
the cash sales recorded for the day. A missed or doubled sale shows up the same night. A difference is a note on the Review list, never a block."""
from __future__ import annotations

from datetime import date

from engines.clock import manila_today
from engines.common import get_conn, write_tx
from engines.permissions import require as require_permission


def _num(v) -> float | None:
    try:
        return round(float(str(v).replace(",", "").replace("₱", "").strip()), 2)
    except (TypeError, ValueError):
        return None


def expected_cash(day: date) -> float:
    """The cash received on the day's saved sales."""
    with get_conn() as conn:
        return round(float(conn.execute("""SELECT COALESCE(SUM(p.amount), 0) FROM sale_payments p JOIN sales s ON s.sale_id = p.sale_id
                                           WHERE s.status = 'SAVED' AND s.sale_date = ? AND p.method = 'CASH'""", (str(day),)).fetchone()[0]), 2)


def get_count(day: date) -> dict | None:
    with get_conn() as conn:
        r = conn.execute("SELECT counted, change_fund, expected, note, counted_by, counted_at FROM cash_counts WHERE count_day = ?", (str(day),)).fetchone()
    if not r:
        return None
    now = expected_cash(day)
    return {"counted": r[0], "fund": r[1], "expected": now, "diff": round(r[0] - r[1] - now, 2), "note": r[3] or "", "by": r[4] or "", "at": r[5]}


def last_fund() -> float:
    with get_conn() as conn:
        r = conn.execute("SELECT change_fund FROM cash_counts ORDER BY count_day DESC LIMIT 1").fetchone()
    return float(r[0]) if r else 0.0


def save_count(day: date, counted, fund, note: str = "") -> tuple[bool, str]:
    """Records (or corrects) the day's count. A difference of a peso or more goes on the Review list."""
    allowed, why = require_permission("sales.use")
    if not allowed:
        return False, why
    c, f = _num(counted), _num(fund or 0)
    if c is None or c < 0:
        return False, "Type the cash counted in the drawer."
    if f is None or f < 0:
        return False, "The change fund is a peso amount (0 if none)."
    if day > manila_today():
        return False, "A count can't be for a day after today."
    exp = expected_cash(day)
    diff = round(c - f - exp, 2)
    note = " ".join(str(note or "").split())
    from engines.review_flags import flag
    with write_tx() as conn:
        conn.execute("""INSERT INTO cash_counts (count_day, counted, change_fund, expected, note, counted_by) VALUES (?, ?, ?, ?, ?, acting_user())
                        ON CONFLICT (count_day) DO UPDATE SET counted = excluded.counted, change_fund = excluded.change_fund, expected = excluded.expected,
                        note = excluded.note, counted_by = excluded.counted_by, counted_at = CURRENT_TIMESTAMP""", (str(day), c, f, exp, note or None))
        if abs(diff) >= 1:
            flag(conn, "CASH_COUNT_DIFF", "cash_counts", None,
                 f"Cash count {day:%m/%d/%Y}: drawer ₱{c:,.2f} less change fund ₱{f:,.2f} = ₱{c - f:,.2f}; cash sales recorded ₱{exp:,.2f}; "
                 + ("₱{:,.2f} more in the drawer".format(diff) if diff > 0 else "₱{:,.2f} short".format(-diff))
                 + (f". Note: {note}" if note else ". A sale not recorded yet, or recorded twice?"))
    if abs(diff) < 1:
        return True, f"Cash count saved: it matches the cash sales (₱{exp:,.2f})."
    return True, (f"Cash count saved: ₱{diff:,.2f} more in the drawer than the cash sales recorded." if diff > 0 else
                  f"Cash count saved: ₱{-diff:,.2f} short of the cash sales recorded.") + " Check for a sale not recorded or typed twice."
