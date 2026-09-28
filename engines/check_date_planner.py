"""Check-date planner (2026-09-28, the owner's long-term goal: "pick a date for my checks considering our cash constraint, and other
criteria"). A transparent rules engine, not AI guessing: every suggested date comes with the reason, and nothing is issued by itself --
the owner sends a batch's suggested dates to its Issue-checks grid (as a remembered, not-yet-parked draft), reviews them and parks.

Owner's decisions (2026-09-28):
  * scope    invoices with NO check yet (the part of each batch not covered by active checks); issued checks are never touched
  * rules    never before the due date; keep the forecast bank balance at or above the cash buffer (no buffer yet -> never below zero);
             a daily limit (amount and / or number of checks going out per day; 0 = no limit); priority suppliers are paid on time first
  * short    move checks later, lowest priority first, up to `max_days_late` days after the due date; what still does not fit is
             flagged "Doesn't fit" with the shortfall, never silently dropped
  * house rule (2026-09-17): never two checks to the same supplier on the same day -- a supplier's invoices in one batch that land on the
             same day become ONE check; another batch of that supplier, or an existing check, pushes to the next free day.
Check dates are bank days: no Saturdays, Sundays or days marked on the calendar (holidays, custom). Cash comes from the latest bank
balance when it is at most 14 days old (same forecast as Bank and cash), or from the cash the owner types in; money in is the average
deposit per weekday from the bank statements, or a typed amount per bank day.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from engines.settings_store import get_number, get_setting, set_setting
from engines.common import get_conn

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")
FRESH_DAYS = 14                 # a bank balance older than this is not used to plan from
TAIL_DAYS = 31                  # the cash check also looks this far past the last suggested date


@dataclass
class PlanSettings:
    max_days_late: int = 7
    daily_limit: float = 0.0            # 0 = no limit
    daily_max_checks: int = 0           # 0 = no limit
    priority: set = field(default_factory=set)      # creditor ids
    buffer: float = 0.0


def load_settings() -> PlanSettings:
    from engines.cash_insights_service import get_buffer

    pri = {int(x) for x in str(get_setting("planner_priority_suppliers", "") or "").split(",") if x.strip().isdigit()}
    return PlanSettings(int(get_number("planner_max_days_late", 7)), float(get_number("planner_daily_limit", 0)),
                        int(get_number("planner_daily_max_checks", 0)), pri, get_buffer())


def save_settings(s: PlanSettings) -> None:
    from engines.cash_insights_service import set_buffer

    set_setting("planner_max_days_late", int(s.max_days_late), "planner", "Check-date planner: most days a check may be moved past its due date")
    set_setting("planner_daily_limit", float(s.daily_limit), "planner", "Check-date planner: most money going out on one day (0 = no limit)")
    set_setting("planner_daily_max_checks", int(s.daily_max_checks), "planner", "Check-date planner: most checks on one day (0 = no limit)")
    set_setting("planner_priority_suppliers", ",".join(str(i) for i in sorted(s.priority)), "planner", "Check-date planner: priority supplier ids")
    set_buffer(float(s.buffer))


def _read(sql: str, params: tuple = ()) -> pd.DataFrame:
    with get_conn(DB_PATH) as conn:
        return pd.read_sql_query(sql, conn, params=params)


# ------------------------------------------------------------------ what needs a check
def open_items(today: date | None = None) -> pd.DataFrame:
    """One row per invoice still needing a check: supplier batches (not cancelled, not locked) whose net total is not fully covered by
    active checks. The uncovered amount is spread over the batch's open invoices; when the batch's own balance is smaller (credit memos,
    or older checks not tied to invoices) the difference comes off the oldest invoices first, so the rows always add up to exactly what
    the batch still needs."""
    batches = _read("""SELECT b.statement_id, b.statement_ref AS batch, b.creditor_id, cr.creditor_name AS supplier, b.net_total,
                              COALESCE((SELECT SUM(c.amount) FROM checks_issued c WHERE c.statement_id = b.statement_id AND c.status = 'Active'), 0) AS checks
                       FROM v_batch_net_totals b JOIN order_statements os ON os.statement_id = b.statement_id
                       JOIN creditors cr ON cr.creditor_id = b.creditor_id
                       WHERE os.status != 'Cancelled' AND COALESCE(os.is_locked, 0) = 0""")
    cols = ["statement_id", "batch", "creditor_id", "supplier", "invoice_id", "doc_ref", "due_date", "amount"]
    if batches.empty:
        return pd.DataFrame(columns=cols)
    batches["remaining"] = (batches["net_total"] - batches["checks"]).round(2)
    rows = []
    for b in batches[batches["remaining"] >= 0.01].itertuples():
        inv = _read("""SELECT i.invoice_id, i.doc_ref, i.due_date,
                              i.amount - COALESCE((SELECT SUM(a.amount) FROM invoice_check_allocations a WHERE a.invoice_id = i.invoice_id), 0) AS remaining
                       FROM invoices i WHERE i.statement_id = ? AND i.status != 'Cancelled' ORDER BY i.due_date, i.invoice_id""", (int(b.statement_id),))
        inv = inv[inv["remaining"] > 0.005].reset_index(drop=True)
        if inv.empty:
            rows.append((b.statement_id, b.batch, b.creditor_id, b.supplier, None, "(no open invoice)", (today or date.today()).isoformat(), b.remaining))
            continue
        amounts = [round(float(x), 2) for x in inv["remaining"]]
        extra = round(sum(amounts) - float(b.remaining), 2)
        i = 0
        while extra > 0.005 and i < len(amounts):              # take the difference off the oldest invoices
            cut = min(amounts[i], extra)
            amounts[i] = round(amounts[i] - cut, 2)
            extra = round(extra - cut, 2)
            i += 1
        if extra < -0.005:                                     # the batch needs more than its invoices show: add it to the last one
            amounts[-1] = round(amounts[-1] - extra, 2)
        for r, amt in zip(inv.itertuples(), amounts):
            if amt >= 0.01:
                rows.append((b.statement_id, b.batch, b.creditor_id, b.supplier, int(r.invoice_id), r.doc_ref, str(r.due_date)[:10], amt))
    return pd.DataFrame(rows, columns=cols)


# ------------------------------------------------------------------ calendar and cash
def _blocked_days() -> set:
    try:
        return {str(d)[:10] for d in _read("SELECT blocked_date FROM calendar_blocked_dates")["blocked_date"]}
    except Exception:
        return set()


def is_bank_day(d: date, blocked: set) -> bool:
    return d.weekday() < 5 and d.isoformat() not in blocked


def next_bank_day(d: date, blocked: set) -> date:
    while not is_bank_day(d, blocked):
        d += timedelta(days=1)
    return d


def cash_source(today: date | None = None) -> dict:
    """{'fresh': bool, 'balance', 'as_of'} -- whether the latest bank balance is recent enough to plan from."""
    from engines.cash_insights_service import cash_position

    today = today or date.today()
    pos = cash_position()
    if pos is None:
        return {"fresh": False, "balance": None, "as_of": None}
    return {"fresh": (today - pos["as_of"]).days <= FRESH_DAYS, "balance": pos["balance"], "as_of": pos["as_of"]}


def _baseline(today: date, end: date, start_cash: float | None, inflow_per_day: float | None, blocked: set) -> dict:
    """The days from today to `end` with money in, money already going out (checks already issued, bank-loan and card dues) and the
    count of checks already dated each day, plus which supplier already has a check on which day."""
    from engines.cash_insights_service import expected_daily_inflow, scheduled_outflows
    from engines.reports_service import get_payment_schedule_all_tracks

    src = cash_source(today)
    if start_cash is None:
        if not src["fresh"]:
            raise ValueError("No recent bank balance to plan from — type the cash in the bank today, or import a recent statement.")
        start, source = float(src["balance"]), f"bank balance of {src['as_of']:%b %d, %Y}"
        out = scheduled_outflows(today, end)[["day", "track", "amount"]]
    else:
        start, source = float(start_cash), "the cash you typed in"
        sched = get_payment_schedule_all_tracks()
        if sched.empty:
            out = pd.DataFrame(columns=["day", "track", "amount"])
        else:
            sched = sched.assign(day=pd.to_datetime(sched["actual_payment_date"]).dt.date)
            out = sched[(sched["day"] >= today) & (sched["day"] <= end)][["day", "track", "amount"]]
    weekday_in = expected_daily_inflow() if inflow_per_day is None else {wd: (float(inflow_per_day) if wd < 5 else 0.0) for wd in range(7)}
    days = [today + timedelta(days=i) for i in range((end - today).days + 1)]
    out_by_day = out.groupby("day")["amount"].sum().to_dict() if not out.empty else {}
    checks_by_day = out[out["track"].isin(["Supplier", "Lender"])].groupby("day").size().to_dict() if not out.empty else {}
    money_in = [0.0 if i == 0 else float(weekday_in.get(d.weekday(), 0.0)) for i, d in enumerate(days)]   # today's deposits are not known yet
    existing = _read("SELECT creditor_id, check_date FROM checks_issued WHERE status = 'Active' AND check_date >= ?", (today.isoformat(),))
    taken = {(int(r.creditor_id), str(r.check_date)[:10]) for r in existing.itertuples()}
    return {"start": start, "source": source, "days": days, "money_in": money_in, "out": [float(out_by_day.get(d, 0.0)) for d in days],
            "checks": [int(checks_by_day.get(d, 0)) for d in days], "taken": taken,
            "inflow_note": "average deposits per weekday from the bank statements" if inflow_per_day is None else f"PHP {float(inflow_per_day):,.2f} per bank day (typed)"}


# ------------------------------------------------------------------ the plan
@dataclass
class Plan:
    items: pd.DataFrame          # one row per invoice: suggested date, days late, status, reason
    checks: pd.DataFrame         # one row per suggested check: batch, date, amount, invoices
    daily: pd.DataFrame          # day, money in, already going out, new checks, balance after, under buffer
    summary: dict


def plan(today: date | None = None, start_cash: float | None = None, inflow_per_day: float | None = None,
         settings: PlanSettings | None = None, items: pd.DataFrame | None = None) -> Plan:
    today = today or date.today()
    s = settings or load_settings()
    blocked = _blocked_days()
    items = open_items(today) if items is None else items.copy()
    if items.empty:
        base = _baseline(today, today + timedelta(days=TAIL_DAYS), start_cash, inflow_per_day, blocked)
        return Plan(items.assign(suggested_date=None, days_late=0, status="", reason=""), pd.DataFrame(), pd.DataFrame(),
                    {"items": 0, "source": base["source"], "start": base["start"], "buffer": s.buffer})
    items["due"] = pd.to_datetime(items["due_date"]).dt.date
    items["priority"] = items["creditor_id"].astype(int).isin(s.priority)
    last = max(max(d, today) for d in items["due"]) + timedelta(days=int(s.max_days_late) + 14)
    base = _baseline(today, last + timedelta(days=TAIL_DAYS), start_cash, inflow_per_day, blocked)
    days = base["days"]
    idx = {d: i for i, d in enumerate(days)}
    n = len(days)
    new_out = [0.0] * n
    new_checks = [0] * n
    # balance before any new check, end of each day
    bal0, run = [], base["start"]
    for i in range(n):
        run += base["money_in"][i] - base["out"][i]
        bal0.append(run)
    taken = set(base["taken"])                       # (creditor_id, day) with an existing check
    planned = {}                                     # (creditor_id, day) -> statement_id of the planned check that day

    def balance_after(i: int) -> list:
        acc, out = 0.0, []
        for k in range(n):
            acc += new_out[k]
            out.append(bal0[k] - acc)
        return out

    # priority suppliers first, then by due date; on the same due date the older batch first (keeps a batch's invoices together)
    order = items.sort_values(["priority", "due", "statement_id", "amount"], ascending=[False, True, True, False], kind="stable").index
    res = {}
    for ix in order:
        it = items.loc[ix]
        cid, sid, amt = int(it["creditor_id"]), int(it["statement_id"]), float(it["amount"])
        overdue = it["due"] < today
        earliest = next_bank_day(max(it["due"], today), blocked)
        latest = max(it["due"], today) + timedelta(days=int(s.max_days_late))
        cands = [d for d in (earliest + timedelta(days=k) for k in range((latest - earliest).days + 1)) if is_bank_day(d, blocked)] or [earliest]
        bals = balance_after(0)
        suffix_min = [0.0] * n
        m = float("inf")
        for k in range(n - 1, -1, -1):
            m = min(m, bals[k])
            suffix_min[k] = m
        why_not, chosen = [], None
        for d in cands:
            i = idx[d]
            key = (cid, d.isoformat())
            if key in taken or (key in planned and planned[key] != sid):
                why_not.append((d, "the supplier already has a check that day"))
                continue
            merging = planned.get(key) == sid
            if s.daily_limit and base["out"][i] + new_out[i] + amt > s.daily_limit + 0.005:
                why_not.append((d, f"the daily limit (PHP {s.daily_limit:,.2f}) would be passed"))
                continue
            if s.daily_max_checks and not merging and base["checks"][i] + new_checks[i] + 1 > s.daily_max_checks:
                why_not.append((d, f"already {s.daily_max_checks} check(s) that day"))
                continue
            if suffix_min[i] - amt < s.buffer - 0.005:
                why_not.append((d, f"cash would go under PHP {s.buffer:,.2f} (lowest PHP {suffix_min[i] - amt:,.2f})"))
                continue
            chosen = d
            break
        if chosen is not None:
            status = "Overdue" if overdue else ("On time" if chosen == earliest else "Moved")
            if chosen == earliest:
                reason = ("Due date has passed — first bank day" if overdue else
                          "On its due date" if chosen == it["due"] else "Due on a weekend / marked day — next bank day")
            else:
                first = why_not[0]
                reason = f"Moved {(chosen - earliest).days} day(s): on {first[0]:%b %d} {first[1]}"
        else:
            # does not fit: the latest allowed day where the supplier is free, flagged with the shortfall
            free = [d for d in cands if (cid, d.isoformat()) not in taken and planned.get((cid, d.isoformat()), sid) == sid] or cands
            chosen = free[-1]
            i = idx[chosen]
            short = s.buffer - (suffix_min[i] - amt)
            status = "Doesn't fit"
            reason = (f"No day within {s.max_days_late} day(s) of the due date passes every rule — "
                      + (f"cash would be PHP {short:,.2f} under the buffer. " if short > 0.005 else "")
                      + f"Placed on the last allowed day ({why_not[-1][1] if why_not else 'check it'}).")
        i = idx[chosen]
        key = (cid, chosen.isoformat())
        if planned.get(key) != sid:
            new_checks[i] += 1
        planned[key] = sid
        new_out[i] += amt
        res[ix] = (chosen, status, reason)

    items["suggested_date"] = [res[ix][0] for ix in items.index]
    items["status"] = [res[ix][1] for ix in items.index]
    items["reason"] = [res[ix][2] for ix in items.index]
    items["days_late"] = [max(0, (res[ix][0] - it_due).days) for ix, it_due in zip(items.index, items["due"])]
    items = items.sort_values(["suggested_date", "supplier", "batch"], kind="stable").reset_index(drop=True)

    checks = (items.groupby(["statement_id", "batch", "creditor_id", "supplier", "suggested_date"], as_index=False)
              .agg(amount=("amount", "sum"), invoices=("doc_ref", lambda x: ", ".join(map(str, x))), n_invoices=("doc_ref", "size"),
                   status=("status", lambda x: "Doesn't fit" if "Doesn't fit" in set(x) else ("Moved" if "Moved" in set(x) else x.iloc[0]))))
    checks["amount"] = checks["amount"].round(2)
    checks = checks.sort_values(["suggested_date", "supplier"], kind="stable").reset_index(drop=True)

    bals = balance_after(0)
    shown_end = min(n - 1, idx[max(items["suggested_date"])] + 7)
    daily = pd.DataFrame({"day": days, "money_in": base["money_in"], "already_out": base["out"], "new_checks": new_out,
                          "balance": bals})[: shown_end + 1]
    daily["under_buffer"] = daily["balance"] < s.buffer - 0.005
    low_i = int(daily["balance"].idxmin())
    # already short before any new check? (checks already issued + loan / card dues alone take the balance under the buffer)
    short0 = next(((days[k], bal0[k]) for k in range(len(daily)) if bal0[k] < s.buffer - 0.005), None)
    summary = {"items": len(items), "checks": len(checks), "total": float(items["amount"].sum()), "source": base["source"],
               "start": base["start"], "buffer": s.buffer, "inflow": base["inflow_note"],
               "moved": int((items["status"] == "Moved").sum()), "overdue": int((items["status"] == "Overdue").sum()),
               "doesnt_fit": int((items["status"] == "Doesn't fit").sum()),
               "lowest": (daily.loc[low_i, "day"], float(daily.loc[low_i, "balance"])), "already_short": short0, "settings": s}
    return Plan(items, checks, daily, summary)


# ------------------------------------------------------------------ hand a batch's dates to its Issue-checks grid
def send_to_batch(p: Plan, statement_id: int) -> tuple[bool, str]:
    """Put the suggested checks of one batch into its Issue-checks grid as a remembered draft (engines/park_draft_service.py) -- nothing
    is parked: the owner opens the batch, reviews the dates and amounts, and clicks Park Metrobank checks as usual."""
    from engines.park_draft_service import save_park_draft
    from engines.statement_rules import get_batch_balance, get_invoices_with_remaining

    rows = p.checks[p.checks["statement_id"] == int(statement_id)].sort_values("suggested_date")
    if rows.empty:
        return False, "Nothing planned for this batch."
    if len(rows) > 50:
        return False, "More than 50 checks for one batch — too many for the grid."
    _, _, remaining = get_batch_balance(int(statement_id))
    if abs(round(float(rows["amount"].sum()), 2) - round(remaining, 2)) > 0.01:
        return False, "The batch changed since the plan was made — make the plan again."
    inv = get_invoices_with_remaining(int(statement_id))
    save_park_draft(int(statement_id), "By number of checks", len(rows), remaining, [int(i) for i in inv["invoice_id"]] if not inv.empty else [],
                    [(d.isoformat(), float(a)) for d, a in zip(rows["suggested_date"], rows["amount"])])
    with get_conn(DB_PATH) as conn:
        conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('PLANNER_DATES_SENT', 'order_statements', ?, ?)",
                     (int(statement_id), "; ".join(f"{d} {a:,.2f}" for d, a in zip(rows["suggested_date"], rows["amount"]))))
    return True, f"{len(rows)} check(s) with the suggested dates are waiting in the batch's Issue checks — review them and park."
