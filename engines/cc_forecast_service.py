"""Credit card due forecast (owner, 2026-09-29): "estimated dues by week for the next 4-5 months, from each card's history, replaced by
the real amount once the statement is entered" -- "meant for issuing checks to avoid large amount of payables especially when there is
credit card that only month away to schedule".

A card's amount is known only when its statement arrives (about 3-4 weeks before the due date), so without this the Check-date planner
and the Cash forecast see card dues only about a month ahead. Owner's choices: the estimates COUNT AS CASH GOING OUT (they move the
planner's suggested check dates and lower the Cash forecast); amount = median of the card's last 3 statements, with the low-high of
those 3 shown; 5 months ahead.

How: for every active card with at least one statement (cancelled ones ignored), the next due dates are projected after its LATEST
recorded due date -- monthly on the same day (month-end clamped), or by the card's usual gap when it is shorter than 27 days -- so an
estimate disappears by itself the moment the real statement is entered. A projected due whose statement month (engines/cc_dates.py)
the owner marked "No due" is skipped. Estimated rows carry the track 'Credit Card (estimated)' and are never counted as checks.
"""
from __future__ import annotations

import calendar
import os
import sqlite3
import statistics
from datetime import date, timedelta

import pandas as pd

from engines.cc_dates import statement_month_for

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")
TRACK = "Credit Card (estimated)"
HISTORY = 3                      # statements the estimate is based on
SHORT_CYCLE_DAYS = 27            # a card whose usual gap between dues is shorter steps by that gap instead of monthly
MONTHS_AHEAD = 5
COLUMNS = ["due", "track", "payee", "reference", "amount", "low", "high", "based_on", "card_id", "cardholder", "statement_month"]


def _add_month(d: date, months: int = 1) -> date:
    y, m = d.year + (d.month - 1 + months) // 12, (d.month - 1 + months) % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _history() -> tuple[pd.DataFrame, pd.DataFrame]:
    with sqlite3.connect(DB_PATH) as conn:
        cards = pd.read_sql_query(
            """SELECT cc.card_id, cc.card_name, cc.card_number_last4, cc.statement_day, COALESCE(ch.name, '') AS cardholder
               FROM credit_cards cc LEFT JOIN cardholders ch ON ch.cardholder_id = cc.cardholder_id WHERE cc.is_active = 1""", conn)
        stmts = pd.read_sql_query(
            """SELECT card_id, due_date, total_amount_due, status FROM cc_statements
               WHERE status != 'Cancelled' AND due_date IS NOT NULL ORDER BY due_date""", conn)
    return cards, stmts


def estimated_card_dues(start: date, end: date, today: date | None = None) -> pd.DataFrame:
    """Estimated card dues with a due date between start and end (inclusive), one row per card per projected due."""
    from engines.cc_service import no_due_marks

    today = today or date.today()
    cards, stmts = _history()
    no_due = no_due_marks()
    rows = []
    for _, c in cards.iterrows():
        mine = stmts[stmts["card_id"] == c["card_id"]]
        if mine.empty:
            continue
        dues = [date.fromisoformat(str(d)[:10]) for d in mine["due_date"]]
        amounts = [float(a or 0) for a in mine["total_amount_due"]][-HISTORY:]
        estimate, low, high = float(statistics.median(amounts)), min(amounts), max(amounts)
        gaps = [(b - a).days for a, b in zip(dues, dues[1:]) if (b - a).days > 5]
        step = statistics.median(gaps) if gaps else 30
        day = None if pd.isna(c["statement_day"]) else int(c["statement_day"])
        label = f"{c['card_name']} ({c['card_number_last4']})" if c["card_number_last4"] else str(c["card_name"])
        nxt, n = max(dues), 0
        while n < 60:
            n += 1
            nxt = _add_month(nxt) if step >= SHORT_CYCLE_DAYS else nxt + timedelta(days=int(step))
            if nxt > end:
                break
            if nxt < start or nxt < today:
                continue
            month = statement_month_for(nxt, day)
            if (int(c["card_id"]), month) in no_due:
                continue
            rows.append({"due": nxt, "track": TRACK, "payee": label, "reference": "estimated from last "
                         + (f"{len(amounts)} statements" if len(amounts) > 1 else "statement"), "amount": estimate, "low": low,
                         "high": high, "based_on": len(amounts), "card_id": int(c["card_id"]), "cardholder": c["cardholder"],
                         "statement_month": month})
    return pd.DataFrame(rows, columns=COLUMNS).sort_values(["due", "payee"]).reset_index(drop=True) if rows else pd.DataFrame(columns=COLUMNS)


def forecast_by_week(today: date | None = None, months: int = MONTHS_AHEAD) -> pd.DataFrame:
    """Week by week (weeks start on Monday) for `months` ahead: card dues already recorded (unpaid statements), estimated, the estimate's
    low-high, and how many cards. Columns: week_start, recorded, estimated, low, high, cards."""
    today = today or date.today()
    end = _add_month(today, months)
    with sqlite3.connect(DB_PATH) as conn:
        rec = pd.read_sql_query(
            """SELECT s.card_id, s.due_date, s.total_amount_due FROM cc_statements s JOIN credit_cards cc ON cc.card_id = s.card_id
               WHERE cc.is_active = 1 AND s.status IN ('Unpaid', 'Partially Paid') AND s.due_date BETWEEN ? AND ?""",
            conn, params=(today.isoformat(), end.isoformat()))
    est = estimated_card_dues(today, end, today=today)
    weeks: dict = {}

    def bucket(d: date) -> dict:
        wk = d - timedelta(days=d.weekday())
        return weeks.setdefault(wk, {"week_start": wk, "recorded": 0.0, "estimated": 0.0, "low": 0.0, "high": 0.0, "cards": set()})

    for _, r in rec.iterrows():
        b = bucket(date.fromisoformat(str(r["due_date"])[:10]))
        b["recorded"] += float(r["total_amount_due"] or 0)
        b["low"] += float(r["total_amount_due"] or 0)
        b["high"] += float(r["total_amount_due"] or 0)
        b["cards"].add(int(r["card_id"]))
    for _, r in est.iterrows():
        b = bucket(r["due"])
        b["estimated"] += r["amount"]
        b["low"] += r["low"]
        b["high"] += r["high"]
        b["cards"].add(int(r["card_id"]))
    out = [dict(v, cards=len(v["cards"])) for _, v in sorted(weeks.items())]
    return pd.DataFrame(out, columns=["week_start", "recorded", "estimated", "low", "high", "cards"])
