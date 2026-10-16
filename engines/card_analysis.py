"""Analytics -> Credit cards (owner, 2026-10-15: "Add credit card analysis", "include finance charges incurred"; mock-up docs/mockups/y_card_analysis.html).
How much is owed on the cards, whether card debt grows or shrinks, the finance charges paid, and each card's latest statement. Read only; every figure
comes from the statements already typed (cc_statements). A statement belongs to the month of its STATEMENT DATE (engines/cc_dates, owner 2026-09-29).
No limit warning (owner: "not need"): credit used is information only. The family owners see this tab too (page credit-cards)."""
from __future__ import annotations

from datetime import date, timedelta

from engines.cc_dates import statement_month_for
from engines.clock import manila_today
from engines.common import get_conn

MONTHS = 6


def _statements() -> list[dict]:
    with get_conn() as conn:
        cols = ("sid", "card_id", "due_date", "total", "prev", "new", "finance", "paid", "carried", "status", "card_name", "last4", "limit", "holder",
                "statement_day", "fee_month", "credits", "unbilled")
        rows = conn.execute("""SELECT s.cc_statement_id, s.card_id, s.due_date, s.total_amount_due, s.previous_balance, s.new_charges, s.interest_charged,
                                      s.amount_paid, s.unpaid_balance_next, s.status, cc.card_name, cc.card_number_last4, cc.credit_limit,
                                      COALESCE(ch.name, '(no holder)'), cc.statement_day, cc.annual_fee_due_month, s.credits, s.unbilled_installments
                               FROM cc_statements s JOIN credit_cards cc ON cc.card_id = s.card_id LEFT JOIN cardholders ch ON ch.cardholder_id = cc.cardholder_id
                               WHERE s.status != 'Cancelled' AND cc.is_active = 1 ORDER BY s.card_id, s.due_date, s.cc_statement_id""").fetchall()
    out = []
    for r in rows:
        s = dict(zip(cols, r))
        for k in ("total", "prev", "new", "finance", "paid", "carried", "credits", "unbilled"):
            s[k] = float(s[k] or 0)
        s["month"] = statement_month_for(s["due_date"], s["statement_day"])
        s["charged"] = s["new"] if s["new"] > 0 else max(s["total"] - s["prev"] - s["finance"], 0.0)       # a statement typed as one lump: all of it is new
        s["paid_amt"] = s["paid"] if s["paid"] > 0 else (s["total"] if s["status"] == "Paid" else 0.0)
        s["open"] = 0.0 if s["status"] == "Paid" else max(s["total"] - s["paid"], 0.0)     # owed until marked paid (Daily payables / the next SOA)
        out.append(s)
    for prev, nxt in zip(out, out[1:]):                       # a balance carried into the card's next statement is owed there, not twice
        if prev["card_id"] == nxt["card_id"] and prev["carried"] > 0 and prev["status"] != "Paid":
            prev["open"] = 0.0
    return out


def _month_keys(today: date, n: int) -> list[str]:
    y, m, keys = today.year, today.month, []
    for _ in range(n):
        keys.append(f"{y:04d}-{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return list(reversed(keys))


def _label(key: str) -> str:
    return date.fromisoformat(key + "-01").strftime("%b %Y")


def _bars(pairs) -> list[dict]:
    top = max([v for _, v in pairs] or [0]) or 1
    return [{"label": k, "amount": v, "pct": round(v / top * 100, 1)} for k, v in pairs]


def _weeks(df, today: date, n: int = 8) -> list[dict]:
    """Card statements due per week (Monday-based), from the schedule of payments (an unpaid past-due statement sits on today)."""
    monday = today - timedelta(days=today.weekday())
    out = []
    for i in range(n):
        a, b = monday + timedelta(weeks=i), monday + timedelta(weeks=i, days=6)
        part = df[(df["track"] == "Credit Card") & (df["actual_payment_date"] >= a) & (df["actual_payment_date"] <= b)] if not df.empty else df
        out.append((f"{a:%m/%d} – {b:%m/%d}", float(part["amount"].sum()) if not part.empty else 0.0))
    return _bars(out)


def analysis(today: date | None = None) -> dict:
    today = today or manila_today()
    stmts = _statements()
    by_card: dict[int, list[dict]] = {}
    for s in stmts:
        by_card.setdefault(s["card_id"], []).append(s)
    year = f"{today.year:04d}-"
    keys = _month_keys(today, MONTHS)
    flow = {k: [0.0, 0.0] for k in keys}
    fin_month = {k: 0.0 for k in _month_keys(today, today.month)}         # January .. this month
    fin_card: dict[str, float] = {}
    for s in stmts:
        if s["month"] in flow:
            flow[s["month"]][0] += s["charged"]
            flow[s["month"]][1] += s["paid_amt"]
        if s["month"].startswith(year) and s["finance"] > 0:
            fin_month[s["month"]] = fin_month.get(s["month"], 0.0) + s["finance"]
            name = f"{s['card_name']} ·{s['last4']}"
            fin_card[name] = fin_card.get(name, 0.0) + s["finance"]
    from engines.cc_service import get_annual_fee_alerts, get_statement_coverage
    cov = get_statement_coverage(today)
    missing = set(cov[cov["status"] == "Missing this month"]["card_id"]) if not cov.empty and "status" in cov.columns else set()
    fees = get_annual_fee_alerts(2)
    fee_soon = {} if fees.empty else dict(zip(fees["card_id"], fees["annual_fee_due_month"]))
    cards, holders, used_sum, limit_sum = [], {}, 0.0, 0.0
    for cid, mine in by_card.items():
        last = mine[-1]
        owed = round(sum(x["open"] for x in mine) + last["unbilled"], 2)           # + installments still to be billed (owner, 2026-10-17)
        if owed > 0:
            holders[last["holder"]] = holders.get(last["holder"], 0.0) + owed
        months_carried = 0
        for s in reversed(mine):
            if s["carried"] > 0:
                months_carried += 1
            else:
                break
        limit = float(last["limit"]) if last["limit"] else None
        used = round((last["total"] + last["unbilled"]) / limit * 100) if limit else None      # installments count against the limit too (2026-10-17)
        if limit:
            used_sum += last["total"] + last["unbilled"]                            # the installments count against the limit too
            limit_sum += limit
        watch = []
        if last["credits"] > 0 and last["unbilled"] > 0:
            watch.append(("warn", "Balance converted to installments this month"))
        elif last["credits"] > 0:
            watch.append(("off", "Credit on the statement"))
        if cid in missing:
            watch.append(("warn", "This month's statement not typed"))
        if months_carried >= 2:
            watch.append(("warn", f"Carried over {months_carried} months"))
        elif last["carried"] > 0:
            watch.append(("warn", "Carried over"))
        if last["finance"] > 0:
            watch.append(("warn", "Finance charge"))
        if last["status"] == "Paid" and last["carried"] == 0 and last["finance"] == 0:
            watch.append(("ok", "Paid in full"))
        if cid in fee_soon:
            watch.append(("off", "Annual fee " + date(2000, int(fee_soon[cid]), 1).strftime("%b")))
        cards.append({"card_id": cid, "name": f"{last['card_name']} ·{last['last4']}", "holder": last["holder"], "limit": limit, "used": used,
                      "month": _label(last["month"]), "charged": last["charged"], "paid": last["paid_amt"], "carried": last["carried"],
                      "finance": last["finance"], "owed": owed, "unbilled": last["unbilled"], "watch": watch, "rank": (-sum(1 for k, _ in watch if k == "warn"), -owed)})
    cards.sort(key=lambda c: c["rank"])
    from engines.reports_service import get_payment_schedule_all_tracks
    df = get_payment_schedule_all_tracks()
    soon = df[(df["track"] == "Credit Card") & (df["actual_payment_date"] >= today) & (df["actual_payment_date"] <= today + timedelta(days=30))] if not df.empty else df
    unbilled = round(sum(c["unbilled"] for c in cards), 2)
    return {"owed": round(sum(c["owed"] for c in cards), 2), "owed_cards": sum(1 for c in cards if c["owed"] > 0),
            "owed_unbilled": unbilled, "owed_statements": round(sum(c["owed"] for c in cards) - unbilled, 2),
            "due30": float(soon["amount"].sum()) if not soon.empty else 0.0, "due30_n": len(soon),
            "finance_year": round(sum(fin_month.values()), 2), "finance_this_month": fin_month.get(keys[-1], 0.0),
            "finance_cards": len(fin_card), "used_pct": round(used_sum / limit_sum * 100) if limit_sum else None, "used_sum": used_sum, "limit_sum": limit_sum,
            "missing": len(missing), "year": today.year,
            "flow": [{"label": _label(k), "charged": v[0], "paid": v[1]} for k, v in flow.items()],
            "flow_top": max([max(v) for v in flow.values()] or [0]) or 1, "finance_top": max(list(fin_month.values()) or [0]) or 1,
            "weeks": _weeks(df, today), "holders": _bars(sorted(holders.items(), key=lambda kv: -kv[1])),
            "fin_cards": _bars(sorted(fin_card.items(), key=lambda kv: -kv[1])),
            "finance_by_month": [(_label(k), v) for k, v in fin_month.items()],
            "cards": cards}
