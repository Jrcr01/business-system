"""Analytics -> Warnings (owner, 2026-10-15: "Next warning tab"; the BI mock-up docs/mockups/s_business_intelligence.html, section 4). One list of the
risks the system can already see, most urgent first, each with a why and where to act. Read only; plain arithmetic over the same figures the other
pages show. A warning is shown only to a person who may open the page it points to (hidden means hidden everywhere, owner 2026-10-11).
The limits are settings (group "Warnings")."""
from __future__ import annotations

from datetime import date, timedelta

from engines.clock import manila_today
from engines.common import get_conn

RED, AMBER, BLUE = "red", "amber", "blue"
_ORDER = {RED: 0, AMBER: 1, BLUE: 2}


def _p(v: float) -> str:
    return f"₱{v:,.0f}" if v >= 0 else f"−₱{-v:,.0f}"


def _limit(key: str) -> int:
    from engines.settings_registry import int_value
    return int_value(key)


def _w(level, page, title, why, link, act) -> dict:
    return {"level": level, "page": page, "title": title, "why": why, "link": link, "act": act}


def cash(today: date) -> list[dict]:
    from engines.bi_service import liquidity
    q = liquidity(today)
    if q.get("none"):
        return [_w(BLUE, "bank-cash", "No bank statement imported yet", "Cash ahead cannot be worked out without a starting balance.", "/bank-cash/", "Bank and cash")]
    out = []
    if q["stale"]:
        out.append(_w(BLUE, "bank-cash", f"The bank balance is {q['stale_days']} days old", f"Last statement line {q['as_of']:%m/%d/%Y}. Import a newer statement for "
                      "warnings worth trusting.", "/bank-cash/", "Import"))
    low, low_day, buf, first = q["lowest"], q["lowest_day"], q["buffer"], q["first_below"]
    if low < 0 or (buf and low < buf):
        day = first or low_day                                   # the first day it is short is the one to act on
        heavy = next((h for h in q["heavy"] if h["day"] == day), None)
        out.append(_w(RED, "bank-cash", f"Cash falls {'below the ' + _p(buf) + ' buffer' if buf else 'below zero'} on {day:%b} {day.day}",
                      (f"{_p(heavy['amount'])} goes out that day: {heavy['what']}. " if heavy else "")
                      + f"The lowest point is {_p(low)} on {low_day:%b} {low_day.day}. Move a payment or bring money in before then.",
                      "/analytics/?tab=cash", "Cash ahead"))
    return out


def old_checks(today: date) -> list[dict]:
    days = _limit("warn_check_days")
    with get_conn() as conn:
        n, total = conn.execute("""SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM checks_issued WHERE status = 'Active' AND bank_cleared_date IS NULL
                                   AND manual_cleared_at IS NULL AND settled_outside_at IS NULL AND check_date < ?""", (str(today - timedelta(days=days)),)).fetchone()
    if not n:
        return []
    return [_w(AMBER, "checks", f"{n} check{'s' if n != 1 else ''} dated over {days} days ago, not cleared ({_p(total)})",
               "Still counted as money going out today, so Cash ahead and the order room read lower than they are. Mark the ones the bank paid, or "
               "settled another way.", "/checks/?tab=waiting", "Checks")]


def running_out(today: date) -> list[dict]:
    from engines.morning_email import RUN_OUT_DAYS
    with get_conn() as conn:
        rows = conn.execute("""SELECT i.name, i.on_hand, m.sold FROM items i JOIN (SELECT item_id, -SUM(qty) AS sold FROM stock_ledger WHERE kind = 'OUT' AND moved_on >= ?
                               GROUP BY item_id) m ON m.item_id = i.id WHERE i.is_active = 1 AND i.on_hand > 0 AND m.sold > 0 AND i.on_hand * 30.0 / m.sold < ?
                               ORDER BY i.on_hand * 30.0 / m.sold""", (str(today - timedelta(days=30)), RUN_OUT_DAYS)).fetchall()
    if not rows:
        return []
    first = rows[0]
    left = max(1, round(first[1] * 30.0 / first[2]))
    return [_w(RED, "stock", f"{len(rows)} item{'s' if len(rows) != 1 else ''} will run out within {RUN_OUT_DAYS} days",
               f"{first[0]}: {first[1]} on hand, {first[2]:g} sold in 30 days (about {left} day{'s' if left != 1 else ''} left)"
               + (f"; and {len(rows) - 1} more." if len(rows) > 1 else "."), "/stock/?show=low", "Stock")]


def customers(today: date) -> list[dict]:
    from engines.collect_rules import aging
    with get_conn() as conn:
        rows = conn.execute("""SELECT DISTINCT s.customer_id, c.customer_name FROM sales s JOIN sale_payments p ON p.sale_id = s.sale_id JOIN customers c ON c.customer_id = s.customer_id
                               WHERE p.method = 'CHARGE' AND s.status = 'SAVED'""").fetchall()
    late = []
    for cid, name in rows:
        a = aging(cid)
        v = a["d60"] + a["d90"] + a["over"]
        if v > 0.005:
            late.append((name, v))
    if not late:
        return []
    late.sort(key=lambda x: -x[1])
    return [_w(AMBER, "collect", f"{len(late)} customer{'s' if len(late) != 1 else ''} over 30 days past due: {_p(sum(v for _, v in late))}",
               " · ".join(f"{n} {_p(v)}" for n, v in late[:3]) + (f" · and {len(late) - 3} more" if len(late) > 3 else ""), "/collect/", "Collect")]


def price_rises(today: date) -> list[dict]:
    pct, since = _limit("warn_price_rise_pct"), today - timedelta(days=_limit("warn_price_days"))
    with get_conn() as conn:
        rows = conn.execute("""SELECT c.creditor_name, p.item_name, p.unit_price,
                                      (SELECT q.unit_price FROM supplier_price_list q WHERE q.creditor_id = p.creditor_id AND q.item_name = p.item_name
                                       AND q.effective_date < p.effective_date ORDER BY q.effective_date DESC LIMIT 1) AS before
                               FROM supplier_price_list p JOIN creditors c ON c.creditor_id = p.creditor_id
                               WHERE p.is_active = 1 AND p.effective_date BETWEEN ? AND ?""", (str(since), str(today))).fetchall()
    by: dict[str, list] = {}
    for sup, item, now, before in rows:
        if before and before > 0 and now > before * (1 + pct / 100.0):
            by.setdefault(sup, []).append((item, before, now))
    out = []
    for sup, items in sorted(by.items(), key=lambda kv: -len(kv[1])):
        items.sort(key=lambda x: -(x[2] / x[1]))
        avg = sum(n / b - 1 for _, b, n in items) / len(items) * 100
        item, b, n = items[0]
        out.append(_w(AMBER, "price-lists", f"{sup}: prices up {avg:.1f}% on {len(items)} item{'s' if len(items) != 1 else ''}",
                      f"{item} {_p(b)} → {_p(n)}. Check your selling prices still give the margin you want.", "/price-lists/", "Price lists"))
    return out


def slow_movers(today: date) -> list[dict]:
    days = _limit("warn_slow_days")
    with get_conn() as conn:
        rows = conn.execute("""SELECT i.name, i.on_hand, i.on_hand * i.avg_cost FROM items i WHERE i.is_active = 1 AND i.on_hand > 0 AND i.avg_cost > 0
                               AND NOT EXISTS (SELECT 1 FROM stock_ledger l WHERE l.item_id = i.id AND l.kind = 'OUT' AND l.moved_on >= ?)
                               AND EXISTS (SELECT 1 FROM stock_ledger l WHERE l.item_id = i.id AND l.moved_on < ?)
                               ORDER BY i.on_hand * i.avg_cost DESC""", (str(today - timedelta(days=days)), str(today - timedelta(days=days)))).fetchall()
    if not rows:
        return []
    total = sum(float(r[2]) for r in rows)
    if total < _limit("warn_slow_min"):
        return []
    name, qty, value = rows[0]
    return [_w(AMBER, "stock", f"{_p(total)} in {len(rows)} slow mover{'s' if len(rows) != 1 else ''}",
               f"No sale in {days} days. The biggest: {name}, {qty} piece{'s' if qty != 1 else ''}, {_p(float(value))}.", "/stock/", "Stock")]


def thin_margins(today: date) -> list[dict]:
    pct = _limit("warn_margin_pct")
    with get_conn() as conn:
        rows = conn.execute("""SELECT i.name, l.unit_price, l.unit_cost FROM sale_lines l JOIN sales s ON s.sale_id = l.sale_id JOIN items i ON i.id = l.item_id
                               WHERE s.status = 'SAVED' AND s.is_opening = 0 AND s.sale_date BETWEEN ? AND ? AND l.unit_price > 0 AND l.unit_cost > 0
                               AND (l.unit_price - l.unit_cost) < l.unit_price * ? ORDER BY (l.unit_price - l.unit_cost) / l.unit_price""",
                            (str(today.replace(day=1)), str(today), pct / 100.0)).fetchall()
    if not rows:
        return []
    names = list(dict.fromkeys(r[0] for r in rows))
    name, price, cost = rows[0]
    m = (price - cost) / price * 100
    return [_w(AMBER, "sales", f"Margin under {pct}% on {len(names)} item{'s' if len(names) != 1 else ''} this month",
               f"{name} sold at {_p(price)} against a cost of {_p(cost)} ({m:.0f}%).", "/analytics/?tab=sales", "Sales")]


def card_statements(today: date) -> list[dict]:
    from engines.cc_service import get_statement_coverage
    cov = get_statement_coverage(today)
    out = []
    if not cov.empty and "status" in cov.columns:
        miss = cov[cov["status"] == "Missing this month"]
        if len(miss):
            names = [f"{r['card_name']} {r['card_number_last4']}" for _, r in miss.iterrows()]
            out.append(_w(BLUE, "credit-cards", f"{len(names)} card statement{'s' if len(names) != 1 else ''} not yet typed",
                          ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "") + ". Due dates cannot be planned until they are in.",
                          "/credit-cards/", "Credit cards"))
    from engines.cc_dates import statement_month_for
    month = today.strftime("%Y-%m")
    with get_conn() as conn:
        rows = conn.execute("""SELECT s.due_date, cc.statement_day, s.interest_charged FROM cc_statements s JOIN credit_cards cc ON cc.card_id = s.card_id
                               WHERE s.status != 'Cancelled' AND s.interest_charged > 0 AND s.due_date >= ?""", (str(today - timedelta(days=75)),)).fetchall()
    fin = sum(float(f) for d, sd, f in rows if statement_month_for(d, sd) == month)
    if fin > 0.005:
        out.insert(0, _w(AMBER, "credit-cards", f"{_p(fin)} in card finance charges this month",
                         "A finance charge comes when a statement is paid less than in full or late. See which cards on Analytics → Credit cards.",
                         "/analytics/?tab=cards", "Credit cards"))
    return out


CHECKS = (cash, old_checks, running_out, customers, card_statements, price_rises, slow_movers, thin_margins)


def all_for(email: str | None = None, today: date | None = None) -> list[dict]:
    """Every warning this person may see, most urgent first. A check that fails is skipped, never the page."""
    from engines.permissions import hidden_for
    today = today or manila_today()
    hidden = hidden_for(email) if email else frozenset()
    out = []
    for check in CHECKS:
        try:
            out.extend(w for w in check(today) if w["page"] not in hidden)
        except Exception:                                   # noqa: BLE001 -- one broken figure must not hide the others
            continue
    return sorted(out, key=lambda w: _ORDER[w["level"]])
