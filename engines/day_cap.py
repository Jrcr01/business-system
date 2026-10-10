"""The daily payment rule (owner, 2026-10-09: "no AP above 350,000 to 400,000 per day"): what goes out on one day, against two lines --
a TARGET (₱350,000: above it the day is "near the limit") and a CEILING (₱400,000: above it, any step that adds to the day asks to confirm and is
flagged DAY_OVER_CAP for review). Survival-mode control (owner): both are Settings numbers; 0 switches the rule off.

What counts on a day (owner, 2026-10-10: "everything that goes out", whatever the bank): checks not cleared, SIs no check covers yet, open purchase
orders (delivered or not), bank-loan installments, card statements -- cards paid outside Metrobank included -- and, for card months whose SOA has not
come yet, a CARD RESERVE (an estimate: the average of the card's last 3 statements, on its usual due day; owner 2026-10-09: "this is not foreseen
because it is given only 1 month before")."""
from __future__ import annotations

import calendar as _cal
from datetime import date, timedelta

import pandas as pd

from engines.clock import manila_today
from engines.common import get_conn

TARGET_KEY = "po_daily_payables_target"
CEILING_KEY = "po_daily_payables_limit"
RESERVE_KEY = "card_reserve_on"
SCOPE_KEY = "po_daily_limit_scope"
COLS = ["day", "payee", "kind", "reference", "amount"]


def target() -> float:
    from engines.settings_registry import value
    return float(value(TARGET_KEY))


def ceiling() -> float:
    from engines.settings_registry import value
    return float(value(CEILING_KEY))


def soft_line() -> float:
    """The amount to plan under: the target, but never above the ceiling (a ceiling set lower than the target wins). 0 = no rule."""
    t, c = target(), ceiling()
    lines = [x for x in (t, c) if x > 0]
    return min(lines) if lines else 0.0


def level(total: float) -> str:
    """'ok' (at or under the target), 'near' (above the target, at or under the ceiling), 'over' (above the ceiling), or '' when the rule is off."""
    t, c = target(), ceiling()
    if c > 0 and total > c + 0.005:
        return "over"
    if t > 0 and total > t + 0.005:
        return "near"
    return "ok" if (t > 0 or c > 0) else ""


def _bank_day(d: date) -> date:
    from engines.reports_service import next_banking_day
    return next_banking_day(d)


def card_reserve_rows(start: date, end: date) -> pd.DataFrame:
    """For each active card with statements on file, a reserve for every statement month after its latest statement, up to `end` (shown as an
    estimate; the real SOA replaces it once entered). Owner, 2026-10-11:
      * the DAY = that month's statement date (the card's statement day) + the card's average gap from statement date to due date over its last 3
        statements, on a bank day. A card with no statement day keeps its most usual due day of the month.
      * the AMOUNT = the average of its last 3 statements' OWN charges -- "dont be the bloated amount (those unpaid)": new_charges when filled in,
        else the total less the unpaid balance carried in from the statement before."""
    from engines.cc_dates import statement_date_for
    from engines.settings_registry import int_value
    if not int_value(RESERVE_KEY):
        return pd.DataFrame(columns=COLS)
    with get_conn() as conn:
        rows = pd.read_sql_query(
            """SELECT cs.card_id, cs.due_date, cs.total_amount_due, cs.new_charges, cs.unpaid_balance_next, cc.statement_day,
                      cc.card_name || ' - ' || cc.card_number_last4 AS card, cr.creditor_name AS bank
               FROM cc_statements cs JOIN credit_cards cc ON cc.card_id = cs.card_id JOIN creditors cr ON cr.creditor_id = cc.creditor_id
               WHERE cs.status != 'Cancelled' AND COALESCE(cc.is_active, 1) = 1 ORDER BY cs.card_id, cs.due_date""", conn)
    out = []
    for card_id, g in rows.groupby("card_id"):
        g = g.assign(carried=g["unpaid_balance_next"].shift(1).fillna(0.0))                 # the unpaid balance the statement before pushed into this one
        own = g["new_charges"].where(g["new_charges"] > 0.004, (g["total_amount_due"] - g["carried"]).clip(lower=0.0))
        last3 = g.tail(3)
        amount = round(float(own.tail(3).mean()), 2)
        if amount <= 0.004:
            continue
        dues = [date.fromisoformat(str(x)[:10]) for x in last3["due_date"]]
        sd = last3["statement_day"].iloc[-1]
        sd = int(sd) if pd.notna(sd) and int(sd) > 0 else 0
        label = f"Card reserve (estimate) · {last3['card'].iloc[-1]} · avg of {len(last3)} statement{'s' if len(last3) != 1 else ''}"
        if sd:
            gap = round(sum((d - statement_date_for(d, sd)).days for d in dues) / len(dues))
            latest = statement_date_for(max(date.fromisoformat(str(x)[:10]) for x in g["due_date"]), sd)
            label += f", {gap} days after the statement day ({sd})"
        else:
            due_day = int(pd.to_datetime(last3["due_date"]).dt.day.mode().max())
            latest = max(date.fromisoformat(str(x)[:10]) for x in g["due_date"])
        y, m = latest.year, latest.month
        while True:
            m += 1
            if m > 12:
                y, m = y + 1, 1
            if sd:
                d = _bank_day(date(y, m, min(sd, _cal.monthrange(y, m)[1])) + timedelta(days=gap))
            else:
                d = _bank_day(date(y, m, min(due_day, _cal.monthrange(y, m)[1])))
            if d > end:
                break
            if d >= start:
                out.append((d, last3["bank"].iloc[-1], "Credit cards", label, amount))
    return pd.DataFrame(out, columns=COLS)


def loads(start: date, end: date, exclude_po_id: int | None = None, requests: bool = False) -> pd.DataFrame:
    """Everything that goes out per day between start and end: the order day check's list (checks, SIs with no check, open POs, bank loans, cards
    paid from Metrobank) plus cards paid outside Metrobank and the card reserve. Columns: day, payee, kind, reference, amount.
    `requests` (the order screens only; owner, 2026-10-11): also the requests waiting for a supplier's answer, as tentative rows."""
    from engines.cash_insights_service import split_card_rows
    from engines.po_rules import _committed_rows
    from engines.reports_service import get_payment_schedule_all_tracks
    from engines.settings_registry import int_value
    base = _committed_rows(end, exclude_po_id, requests=requests)
    if not int_value(SCOPE_KEY):                              # suppliers only (Settings): supplier checks, SIs with no check, open orders
        base = base[base["kind"].isin(["Supplier checks", "Purchase orders", "Order requests"])] if not base.empty else base
        if base.empty:
            return pd.DataFrame(columns=COLS)
        base = base.assign(day=pd.to_datetime(base["day"]).dt.date)
        return base[(base["day"] >= start) & (base["day"] <= end)][COLS].reset_index(drop=True)
    parts = [base]
    sched = get_payment_schedule_all_tracks()
    if not sched.empty:
        _, outside = split_card_rows(sched)
        if not outside.empty:
            today = manila_today()
            days = [max(_bank_day(date.fromisoformat(str(x)[:10])), _bank_day(today)) for x in outside["due_date"]]
            parts.append(pd.DataFrame({"day": days, "payee": outside["payee_name"], "kind": "Credit cards", "reference": outside["reference"],
                                       "amount": outside["amount"].astype(float)}))
    parts.append(card_reserve_rows(start, end))
    df = pd.concat([p[COLS] for p in parts if not p.empty], ignore_index=True) if any(not p.empty for p in parts) else pd.DataFrame(columns=COLS)
    if df.empty:
        return df
    df["day"] = pd.to_datetime(df["day"]).dt.date
    return df[(df["day"] >= start) & (df["day"] <= end)].reset_index(drop=True)


def day_totals(start: date, end: date, exclude_po_id: int | None = None, requests: bool = False) -> dict:
    df = loads(start, end, exclude_po_id, requests)
    return {} if df.empty else {d: float(v) for d, v in df.groupby("day")["amount"].sum().items()}


def over_days(adds: list[tuple[date, float]], minus: list[tuple[date, float]] | None = None) -> list[dict]:
    """The days that `adds` (date, amount) would push above the ceiling. `minus` takes amounts off first (a parked check being moved).
    [{'day', 'already', 'adding', 'total'}]; empty when the rule is off or every day stays at or under the ceiling."""
    c = ceiling()
    if c <= 0 or not adds:
        return []
    today = manila_today()
    days = [max(_bank_day(d), _bank_day(today)) for d, _ in adds]
    totals = day_totals(min(days), max(days))
    for d, a in (minus or []):
        dd = max(_bank_day(d), _bank_day(today))
        totals[dd] = totals.get(dd, 0.0) - float(a)
    adding: dict = {}
    for d, (_, a) in zip(days, adds):
        adding[d] = adding.get(d, 0.0) + float(a)
    return [{"day": d, "already": round(totals.get(d, 0.0), 2), "adding": round(a, 2), "total": round(totals.get(d, 0.0) + a, 2)}
            for d, a in sorted(adding.items()) if totals.get(d, 0.0) + a > c + 0.005]


def flag_over(conn, days: list[dict], what: str) -> None:
    """A review line per day pushed above the ceiling and confirmed anyway."""
    from engines.review_flags import flag
    for d in days:
        flag(conn, "DAY_OVER_CAP", "day", None, f"{d['day']:%m/%d/%Y}: {what} ₱{d['adding']:,.2f} on a day already at ₱{d['already']:,.2f} → ₱{d['total']:,.2f}, "
                                               f"above the daily limit ₱{ceiling():,.2f} (confirmed anyway).")


def suggest_batch_dates(statement_id: int, max_days_late: int | None = None) -> list[dict]:
    """Check dates for what a batch still needs (its SIs no check covers yet), spread so each day stays at or under the target where possible:
    never before an SI's due date, bank days only, one check per day for this batch (SIs landing on the same day become one check), and no day where
    the supplier already has a check from another batch; up to `max_days_late` days after the due date. What fits nowhere goes on the least loaded
    allowed day, marked "Doesn't fit". [{'date', 'amount', 'invoices', 'status'}] in date order -- nothing is parked here."""
    from engines.payables_waterfall import uncovered_by_batch
    from engines.settings_registry import int_value
    late = int_value("planner_max_days_late") if max_days_late is None else int(max_days_late)
    items = uncovered_by_batch(statement_id)
    if not items:
        return []
    today = manila_today()
    first = max(_bank_day(today), min(_bank_day(max(date.fromisoformat(i["due"]), today)) for i in items))
    last = max(date.fromisoformat(i["due"]) for i in items) + timedelta(days=late + 7)
    df = loads(first, max(last, first))
    own = {f"SI {i['doc_ref']} · no check yet" for i in items}
    if not df.empty:
        df = df[~df["reference"].isin(own)]                       # this batch's own SIs are what we are placing
    totals = {} if df.empty else {d: float(v) for d, v in df.groupby("day")["amount"].sum().items()}
    with get_conn() as conn:
        cid = conn.execute("SELECT creditor_id FROM order_statements WHERE statement_id = ?", (statement_id,)).fetchone()[0]
        taken = {str(r[0])[:10] for r in conn.execute("""SELECT check_date FROM checks_issued WHERE creditor_id = ? AND status = 'Active'
                                                         AND COALESCE(statement_id, -1) != ?""", (cid, statement_id))}
    line = soft_line()
    plan: dict = {}
    for it in sorted(items, key=lambda x: (x["due"], -x["amount"])):
        start = _bank_day(max(date.fromisoformat(it["due"]), today))
        cands = []
        d = start
        while d <= start + timedelta(days=late):
            if d.isoformat() not in taken:
                cands.append(d)
            d = _bank_day(d + timedelta(days=1))
        cands = cands or [start]
        load = lambda d: totals.get(d, 0.0) + plan.get(d, {}).get("amount", 0.0)     # noqa: E731
        fit = next((d for d in cands if not line or load(d) + it["amount"] <= line + 0.005), None)
        chosen, status = (fit, "On time" if fit == start else "Moved") if fit else (min(cands, key=load), "Doesn't fit")
        p = plan.setdefault(chosen, {"amount": 0.0, "invoices": [], "status": "On time"})
        p["amount"] = round(p["amount"] + it["amount"], 2)
        p["invoices"].append(it["doc_ref"])
        if status == "Doesn't fit" or (status == "Moved" and p["status"] == "On time"):
            p["status"] = status
    return [{"date": d, "amount": v["amount"], "invoices": ", ".join(v["invoices"]), "status": v["status"],
             "day_total": round(totals.get(d, 0.0) + v["amount"], 2)} for d, v in sorted(plan.items())]
