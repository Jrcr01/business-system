"""Stage B part 3 (owner, 2026-10-11, the mock-ups' choices): cash IN by day, beside the daily plan's out. Firm pieces in full colour -- the
customers' checks on their check dates and open charge invoices on their due dates -- and the usual cash takings for that weekday as a greyed
estimate (his yes), labelled like the card reserve, never passed off as fact. Deposits-light: each day's takings beside what the bank statement
shows; the difference is shown, never policed -- the statement is the truth."""
from __future__ import annotations

from datetime import date, timedelta

from engines.clock import manila_today
from engines.common import get_conn

USUAL_WINDOW_DAYS = 56          # the weekday average looks back 8 weeks


def _firm_checks(conn, start: date, end: date) -> dict[date, float]:
    """Customer checks not yet cleared and not bounced, on their check date (an old uncleared one counts on `start`: it can be deposited now)."""
    rows = conn.execute(
        """SELECT p.check_date, p.amount FROM sale_payments p JOIN sales s ON s.sale_id = p.sale_id
           LEFT JOIN customer_check_state m ON m.source = 'sale' AND m.payment_id = p.payment_id
           WHERE p.method = 'CHECK' AND s.status = 'SAVED' AND COALESCE(m.cleared_on, m.bounced_on) IS NULL
           UNION ALL
           SELECT p.check_date, p.amount FROM collection_payments p JOIN collections k ON k.collection_id = p.collection_id
           LEFT JOIN customer_check_state m ON m.source = 'collection' AND m.payment_id = p.payment_id
           WHERE p.method = 'CHECK' AND k.voided_at IS NULL AND COALESCE(m.cleared_on, m.bounced_on) IS NULL""").fetchall()
    out: dict[date, float] = {}
    for day, amount in rows:
        try:
            d = date.fromisoformat(str(day or "")[:10])
        except ValueError:
            d = start
        d = max(d, start)                                       # an old uncleared check can be deposited now
        if d > end:                                             # a post-dated one beyond the window is not in it
            continue
        out[d] = round(out.get(d, 0.0) + float(amount), 2)
    return out


def _due_invoices(conn, start: date, end: date) -> dict[date, float]:
    """What open charge invoices still owe, on their due dates (sale date + the customer's terms); already due counts on `start`."""
    rows = conn.execute(
        """SELECT s.sale_date, COALESCE(c.terms, ''),
                  (SELECT COALESCE(SUM(p.amount), 0) FROM sale_payments p WHERE p.sale_id = s.sale_id AND p.method = 'CHARGE')
                  + (SELECT COALESCE(SUM(p.amount), 0) FROM sale_payments p JOIN customer_check_state m ON m.source = 'sale' AND m.payment_id = p.payment_id
                     WHERE p.sale_id = s.sale_id AND p.method = 'CHECK' AND m.bounced_on IS NOT NULL)
                  - (SELECT COALESCE(SUM(l.amount), 0) FROM collection_lines l JOIN collections k ON k.collection_id = l.collection_id
                     WHERE l.sale_id = s.sale_id AND k.voided_at IS NULL)
           FROM sales s LEFT JOIN customers c ON c.customer_id = s.customer_id
           WHERE s.status = 'SAVED' AND s.customer_id IS NOT NULL AND s.written_off_at IS NULL""").fetchall()
    out: dict[date, float] = {}
    for day, terms, owed in rows:
        if not owed or owed <= 0.005:
            continue
        days = int(str(terms).strip() or 0) if str(terms).strip().isdigit() else 0
        try:
            due = date.fromisoformat(str(day)[:10]) + timedelta(days=days)
        except ValueError:
            continue
        d = max(due, start)                                     # already due: it counts now
        if d > end:
            continue
        out[d] = round(out.get(d, 0.0) + float(owed), 2)
    return out


def _usual_by_weekday(conn, today: date) -> dict[int, float]:
    """The average cash-like takings (cash, transfer, card -- on sales and collections) per weekday over the last 8 weeks. An estimate, greyed."""
    since = str(today - timedelta(days=USUAL_WINDOW_DAYS))
    rows = conn.execute(
        """SELECT day, SUM(amount) FROM (
             SELECT s.sale_date AS day, p.amount FROM sale_payments p JOIN sales s ON s.sale_id = p.sale_id
             WHERE p.method IN ('CASH', 'TRANSFER', 'CARD') AND s.status = 'SAVED' AND s.sale_date >= ? AND s.sale_date < ?
             UNION ALL
             SELECT k.collect_date AS day, p.amount FROM collection_payments p JOIN collections k ON k.collection_id = p.collection_id
             WHERE p.method IN ('CASH', 'TRANSFER', 'CARD') AND k.voided_at IS NULL AND k.collect_date >= ? AND k.collect_date < ?)
           GROUP BY day""", (since, str(today), since, str(today))).fetchall()
    sums: dict[int, float] = {}
    counts: dict[int, int] = {}
    for day, total in rows:
        try:
            wd = date.fromisoformat(str(day)[:10]).weekday()
        except ValueError:
            continue
        sums[wd] = sums.get(wd, 0.0) + float(total)
        counts[wd] = counts.get(wd, 0) + 1
    return {wd: round(sums[wd] / counts[wd], 2) for wd in sums if counts.get(wd)}


def in_by_day(start: date, end: date) -> dict[date, dict]:
    """{day: {'checks', 'due', 'usual', 'firm', 'total'}} between start and end. checks + due are firm; usual is the weekday estimate
    (future days only, so today's real takings are not double-guessed)."""
    today = manila_today()
    with get_conn() as conn:
        checks = _firm_checks(conn, start, end)
        due = _due_invoices(conn, start, end)
        usual = _usual_by_weekday(conn, today)
    out: dict[date, dict] = {}
    d = start
    while d <= end:
        c, v = checks.get(d, 0.0), due.get(d, 0.0)
        u = usual.get(d.weekday(), 0.0) if d > today else 0.0
        if c or v or u:
            out[d] = {"checks": c, "due": v, "usual": u, "firm": round(c + v, 2), "total": round(c + v + u, 2)}
        d += timedelta(days=1)
    return out


def deposit_days(days: int = 14) -> list[dict]:
    """Deposits, light: each recent day's takings (cash-like, and customer checks marked deposited that day) beside the statement's credits.
    The difference shows; nothing is policed."""
    today = manila_today()
    start = today - timedelta(days=days - 1)
    with get_conn() as conn:
        cash = {str(d): float(v) for d, v in conn.execute(
            """SELECT day, SUM(amount) FROM (
                 SELECT s.sale_date AS day, p.amount FROM sale_payments p JOIN sales s ON s.sale_id = p.sale_id
                 WHERE p.method = 'CASH' AND s.status = 'SAVED' AND s.sale_date >= ?
                 UNION ALL
                 SELECT k.collect_date AS day, p.amount FROM collection_payments p JOIN collections k ON k.collection_id = p.collection_id
                 WHERE p.method = 'CASH' AND k.voided_at IS NULL AND k.collect_date >= ?)
               GROUP BY day""", (str(start), str(start))).fetchall()}
        deposited = {}
        for d, v in conn.execute(
            """SELECT m.deposited_on, SUM(x.amount) FROM customer_check_state m
               JOIN (SELECT 'sale' AS source, payment_id, amount FROM sale_payments WHERE method = 'CHECK'
                     UNION ALL SELECT 'collection', payment_id, amount FROM collection_payments WHERE method = 'CHECK') x
                 ON x.source = m.source AND x.payment_id = m.payment_id
               WHERE m.deposited_on IS NOT NULL AND m.deposited_on >= ? GROUP BY m.deposited_on""", (str(start),)).fetchall():
            deposited[str(d)] = float(v)
        stmt = {str(d): float(v) for d, v in conn.execute(
            "SELECT txn_date, SUM(credit) FROM bank_transactions WHERE credit > 0 AND txn_date >= ? GROUP BY txn_date", (str(start),)).fetchall()}
    out = []
    d = today
    while d >= start:
        key = str(d)
        ours = round(cash.get(key, 0.0) + deposited.get(key, 0.0), 2)
        bank = round(stmt.get(key, 0.0), 2)
        if ours or bank:
            out.append({"day": d, "cash": round(cash.get(key, 0.0), 2), "deposited": round(deposited.get(key, 0.0), 2),
                        "statement": bank, "diff": round(bank - ours, 2)})
        d -= timedelta(days=1)
    return out
