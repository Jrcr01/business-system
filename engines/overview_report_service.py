"""Overall report: highlights and insights for a Day / Week / Month / Quarter / Year (2026-09-21).

Everything is compared with the period just before it. Money definitions follow the rest of the app:
  paid       Supplier / Lender checks (Active or Cleared) whose check date has passed, Bank Loan
             installments whose due date has passed, Credit Card statement amounts paid.
  scheduled  the same items dated today or later (due today = still owed, owner 2026-09-28).
Nothing here writes to the database.
"""
import os
import sqlite3
from calendar import monthrange
from datetime import date, timedelta

import pandas as pd

from engines.cc_service import get_statement_coverage
from engines.checkbook_service import get_checkbook_alerts
from engines.dashboard_kpi_service import get_supplier_check_lag
from engines.duplicate_service import loan_check_mismatches
from engines.reports_service import get_combined_ap_summary, get_payment_schedule_all_tracks

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")
PERIOD_KINDS = ["Day", "Week", "Month", "Quarter", "Year"]
TRACKS = ["Supplier", "Private Lender", "Bank Loan", "Credit Card"]
STALE_DAYS = 180


def _php(v) -> str:
    return f"PHP {float(v):,.2f}"


def period_bounds(kind: str, anchor: date) -> tuple[date, date, str]:
    """(start, end, label) of the day / Mon-Sun week / month / quarter / year containing `anchor`."""
    if kind == "Day":
        return anchor, anchor, f"{anchor:%A, %B %d, %Y}"
    if kind == "Week":
        start = anchor - timedelta(days=anchor.weekday())
        end = start + timedelta(days=6)
        return start, end, f"Week of {start:%b %d} to {end:%b %d, %Y}"
    if kind == "Month":
        start = anchor.replace(day=1)
        return start, anchor.replace(day=monthrange(anchor.year, anchor.month)[1]), f"{anchor:%B %Y}"
    if kind == "Quarter":
        q = (anchor.month - 1) // 3
        start = date(anchor.year, q * 3 + 1, 1)
        end_month = q * 3 + 3
        return start, date(anchor.year, end_month, monthrange(anchor.year, end_month)[1]), f"Q{q + 1} {anchor.year}"
    if kind == "Year":
        return date(anchor.year, 1, 1), date(anchor.year, 12, 31), str(anchor.year)
    raise ValueError(f"Unknown period kind: {kind}")


def previous_bounds(kind: str, start: date) -> tuple[date, date, str]:
    return period_bounds(kind, start - timedelta(days=1))


def next_bounds(kind: str, end: date) -> tuple[date, date, str]:
    return period_bounds(kind, end + timedelta(days=1))


# ----------------------------------------------------------------------------------------------
def _cash_rows(conn, start: date, end: date) -> pd.DataFrame:
    """One row per payment in [start, end]: track, payee, date, paid, scheduled."""
    s, e, today = str(start), str(end), str(date.today())
    parts = []
    parts.append(pd.read_sql_query(
        """SELECT CASE WHEN c.loan_note_id IS NOT NULL THEN 'Private Lender' ELSE 'Supplier' END AS track,
                  cr.creditor_name AS payee, c.check_date AS date,
                  CASE WHEN c.check_date < ? THEN c.amount ELSE 0 END AS paid,
                  CASE WHEN c.check_date >= ? THEN c.amount ELSE 0 END AS scheduled
           FROM checks_issued c JOIN creditors cr ON cr.creditor_id = c.creditor_id
           WHERE c.status IN ('Active', 'Cleared') AND c.check_date BETWEEN ? AND ?""",
        conn, params=(today, today, s, e)))
    parts.append(pd.read_sql_query(
        """SELECT 'Bank Loan' AS track, cr.creditor_name AS payee, bli.due_date AS date,
                  CASE WHEN bli.due_date < ? THEN bli.amount ELSE 0 END AS paid,
                  CASE WHEN bli.due_date >= ? THEN bli.amount ELSE 0 END AS scheduled
           FROM bank_loan_installments bli JOIN bank_loans bl ON bl.bank_loan_id = bli.bank_loan_id
           JOIN creditors cr ON cr.creditor_id = bl.creditor_id
           WHERE bl.is_cancelled = 0 AND bli.due_date BETWEEN ? AND ?""",
        conn, params=(today, today, s, e)))
    # Card payments count on the date they were actually recorded; a statement with no recorded payment
    # falls back to its own amount_paid on its due date (the migrated statements, or a statement you confirmed).
    parts.append(pd.read_sql_query(
        """SELECT 'Credit Card' AS track, cr.creditor_name AS payee, p.payment_date AS date, p.amount AS paid, 0 AS scheduled
           FROM cc_payments p JOIN credit_cards cc ON cc.card_id = p.card_id JOIN creditors cr ON cr.creditor_id = cc.creditor_id
           WHERE p.payment_date BETWEEN ? AND ?""",
        conn, params=(s, e)))
    parts.append(pd.read_sql_query(
        """SELECT 'Credit Card' AS track, cr.creditor_name AS payee, cs.due_date AS date,
                  CASE WHEN EXISTS (SELECT 1 FROM cc_payments p WHERE p.cc_statement_id = cs.cc_statement_id) THEN 0 ELSE cs.amount_paid END AS paid,
                  CASE WHEN cs.due_date >= ? THEN MAX(cs.total_amount_due - cs.amount_paid, 0) ELSE 0 END AS scheduled
           FROM cc_statements cs JOIN credit_cards cc ON cc.card_id = cs.card_id
           JOIN creditors cr ON cr.creditor_id = cc.creditor_id
           WHERE cs.status != 'Cancelled' AND cs.due_date BETWEEN ? AND ?""",
        conn, params=(today, s, e)))
    return pd.concat(parts, ignore_index=True)


def _interest(conn, start: date, end: date) -> pd.DataFrame:
    """Interest cost in the period by facility (same conventions as interest_service)."""
    s, e, today = str(start), str(end), str(date.today())
    rows = pd.concat([
        pd.read_sql_query(
            """SELECT 'Private loans' AS source, cr.creditor_name AS name, SUM(li.interest) AS interest
               FROM loan_installments li JOIN loan_notes ln ON ln.loan_note_id = li.loan_note_id
               JOIN creditors cr ON cr.creditor_id = ln.creditor_id JOIN checks_issued ck ON ck.check_id = li.check_id
               WHERE li.status = 'Active' AND ck.status = 'Active' AND ck.check_date BETWEEN ? AND ? AND ck.check_date < ?
               GROUP BY cr.creditor_id""", conn, params=(s, e, today)),
        pd.read_sql_query(
            """SELECT 'Bank loans' AS source, cr.creditor_name AS name, SUM(bli.interest_portion) AS interest
               FROM bank_loan_installments bli JOIN bank_loans bl ON bl.bank_loan_id = bli.bank_loan_id
               JOIN creditors cr ON cr.creditor_id = bl.creditor_id
               WHERE bl.is_cancelled = 0 AND bli.due_date BETWEEN ? AND ? AND bli.due_date < ?
               GROUP BY cr.creditor_id""", conn, params=(s, e, today)),
        pd.read_sql_query(
            """SELECT 'Credit cards' AS source, (cc.card_name || ' - ' || cc.card_number_last4) AS name, SUM(cs.interest_charged) AS interest
               FROM cc_statements cs JOIN credit_cards cc ON cc.card_id = cs.card_id
               WHERE cs.status != 'Cancelled' AND cs.due_date BETWEEN ? AND ? GROUP BY cc.card_id""", conn, params=(s, e)),
    ], ignore_index=True)
    rows["interest"] = rows["interest"].fillna(0.0)
    return rows[rows["interest"] > 0]


def _checks_summary(conn, start: date, end: date) -> dict:
    row = conn.execute(
        """SELECT COALESCE(SUM(CASE WHEN status IN ('Active', 'Cleared') THEN 1 END), 0),
                  COALESCE(SUM(CASE WHEN status IN ('Cancelled', 'Replaced', 'Spoiled') THEN 1 END), 0),
                  COALESCE(SUM(CASE WHEN status IN ('Cancelled', 'Replaced', 'Spoiled') THEN amount END), 0)
           FROM checks_issued WHERE check_date BETWEEN ? AND ?""", (str(start), str(end))).fetchone()
    return {"issued": int(row[0]), "voided": int(row[1]), "voided_amount": float(row[2])}


def _new_business(conn, start: date, end: date) -> dict:
    """New batches / loans opened in the period (migrated LEGACY batches are left out -- they were all loaded on one day)."""
    s, e = str(start), str(end)
    b = conn.execute(
        """SELECT COUNT(*), COALESCE(SUM(total_amount), 0) FROM order_statements
           WHERE status = 'Issued' AND statement_ref NOT LIKE 'LEGACY-%' AND DATE(created_at, 'localtime') BETWEEN ? AND ?""", (s, e)).fetchone()
    inv = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM invoices WHERE status != 'Cancelled' AND invoice_date BETWEEN ? AND ?", (s, e)).fetchone()
    ln = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(principal_amount), 0) FROM loan_notes WHERE status != 'Cancelled' AND issue_date BETWEEN ? AND ?", (s, e)).fetchone()
    return {"batches": int(b[0]), "batches_total": float(b[1]), "invoices": int(inv[0]), "invoices_total": float(inv[1]),
            "loan_notes": int(ln[0]), "loan_notes_total": float(ln[1])}


def _behavior(start: date, end: date) -> dict:
    """How late supplier checks are versus the batch's invoice-weighted due date (needs itemised invoices)."""
    df = get_supplier_check_lag()
    if df.empty:
        return {"count": 0, "avg_days": None, "on_time_pct": None}
    today = str(date.today())
    d = df[(df["status"] == "Active") & (df["check_date"] >= str(start)) & (df["check_date"] <= str(end)) & (df["check_date"] < today)]
    if d.empty:
        return {"count": 0, "avg_days": None, "on_time_pct": None}
    return {"count": int(len(d)), "avg_days": float(d["lag_days"].mean()), "on_time_pct": float((d["lag_days"] <= 0).mean() * 100)}


def _summarise(conn, start: date, end: date) -> dict:
    cash = _cash_rows(conn, start, end)
    by_track = cash.groupby("track", as_index=False)[["paid", "scheduled"]].sum() if not cash.empty else pd.DataFrame(columns=["track", "paid", "scheduled"])
    by_track = by_track.set_index("track").reindex(TRACKS).fillna(0.0).reset_index()
    payees = (cash.groupby(["payee", "track"], as_index=False)[["paid", "scheduled"]].sum() if not cash.empty
              else pd.DataFrame(columns=["payee", "track", "paid", "scheduled"]))
    payees["total"] = payees["paid"] + payees["scheduled"]
    payees = payees.sort_values("total", ascending=False).reset_index(drop=True)
    interest = _interest(conn, start, end)
    return {
        "paid": float(by_track["paid"].sum()), "scheduled": float(by_track["scheduled"].sum()),
        "by_track": by_track, "payees": payees,
        "interest_total": float(interest["interest"].sum()) if not interest.empty else 0.0,
        "interest": interest.sort_values("interest", ascending=False).reset_index(drop=True),
        "checks": _checks_summary(conn, start, end), "new": _new_business(conn, start, end),
        "behavior": _behavior(start, end),
    }


def _pct_change(now: float, before: float) -> float | None:
    return None if before == 0 else (now - before) / before * 100.0


def _watch_list() -> list[dict]:
    """Things needing attention today, from the system's existing safeguards."""
    items = []
    try:
        cov = get_statement_coverage()
        missing = cov[cov["status"].isin(["Missing this month", "Missing both months"])] if not cov.empty else cov
        if len(missing):
            items.append({"item": "Credit card statements not yet received", "count": int(len(missing)),
                          "detail": ", ".join(f"{r.card_name}" for r in missing.head(4).itertuples()) if "card_name" in missing.columns else ""})
    except Exception:
        pass
    try:
        alerts = get_checkbook_alerts()
        if len(alerts):
            items.append({"item": "Checkbooks with a gap or duplicate number", "count": int(len(alerts)), "detail": ", ".join(alerts["booklet_code"].astype(str).head(4))})
    except Exception:
        pass
    with sqlite3.connect(DB_PATH) as conn:
        note_ids = [r[0] for r in conn.execute("SELECT DISTINCT loan_note_id FROM checks_issued WHERE loan_note_id IS NOT NULL AND status = 'Active'")]
        bad = sum(len(loan_check_mismatches(n)) for n in note_ids)
        if bad:
            items.append({"item": "Loan-note checks not matching their schedule", "count": bad, "detail": ""})
        stale = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM checks_issued WHERE status = 'Active' AND check_date < DATE('now', 'localtime', ?)",
            (f"-{STALE_DAYS} days",)).fetchone()
        if stale[0]:
            items.append({"item": f"Checks still uncleared after {STALE_DAYS} days", "count": int(stale[0]), "detail": _php(stale[1])})
    return items


def _upcoming(kind: str, end: date) -> dict:
    nstart, nend, label = next_bounds(kind, end)
    df = get_payment_schedule_all_tracks()
    if not df.empty:
        col = "actual_payment_date" if "actual_payment_date" in df.columns else "due_date"
        df = df.assign(date=df[col].astype(str).str[:10])
        df = df[(df["date"] >= str(nstart)) & (df["date"] <= str(nend))].copy()
    if df is None or df.empty:
        return {"label": label, "total": 0.0, "by_track": {}, "peak_days": [], "by_week": []}
    df["week"] = pd.to_datetime(df["date"]).dt.to_period("W-SUN").apply(lambda p: p.start_time.date())
    peak = df.groupby("date")["amount"].sum().sort_values(ascending=False).head(3)
    weeks = df.groupby("week")["amount"].sum().sort_index()
    return {"label": label, "total": float(df["amount"].sum()),
            "by_track": df.groupby("track")["amount"].sum().to_dict(),
            "peak_days": [(d, float(a)) for d, a in peak.items()],
            "by_week": [(str(w), float(a)) for w, a in weeks.items()]}


def _highlights(kind: str, label: str, cur: dict, prev: dict, upcoming: dict, watch: list) -> list[str]:
    out = []
    ch = _pct_change(cur["paid"], prev["paid"])
    trend = "" if ch is None else (f" -- {abs(ch):.0f}% {'more' if ch >= 0 else 'less'} than the previous {kind.lower()} ({_php(prev['paid'])})")
    out.append(f"Paid out {_php(cur['paid'])} in {label}{trend}.")
    if cur["scheduled"] > 0.005:
        out.append(f"A further {_php(cur['scheduled'])} in this period is scheduled but not yet due.")
    paid_total = cur["paid"] + cur["scheduled"]
    if paid_total > 0:
        top_track = cur["by_track"].assign(t=cur["by_track"]["paid"] + cur["by_track"]["scheduled"]).sort_values("t", ascending=False).iloc[0]
        out.append(f"{top_track['track']} was the biggest area: {_php(top_track['t'])} ({top_track['t'] / paid_total * 100:.0f}% of the period's payments).")
        if not cur["payees"].empty:
            p = cur["payees"].iloc[0]
            out.append(f"Largest payee: {p['payee']} at {_php(p['total'])} ({p['total'] / paid_total * 100:.0f}%).")
    else:
        out.append("No payments fall in this period.")
    ich = _pct_change(cur["interest_total"], prev["interest_total"])
    if cur["interest_total"] > 0:
        itrend = "" if ich is None else f", {'up' if ich >= 0 else 'down'} {abs(ich):.0f}% on the previous {kind.lower()}"
        out.append(f"Interest cost was {_php(cur['interest_total'])}{itrend}.")
    if upcoming["total"] > 0:
        peak = f"; the heaviest day is {upcoming['peak_days'][0][0]} ({_php(upcoming['peak_days'][0][1])})" if upcoming["peak_days"] else ""
        out.append(f"Coming up in {upcoming['label']}: {_php(upcoming['total'])} falls due{peak}.")
    b = cur["behavior"]
    if b["count"]:
        out.append(f"Supplier checks cleared on average {abs(b['avg_days']):.0f} day(s) {'after' if b['avg_days'] > 0 else 'before'} the invoices' due dates ({b['on_time_pct']:.0f}% on time).")
    if watch:
        out.append(f"{len(watch)} item(s) need attention -- see the watch list.")
    return out


def _short_label(kind: str, start: date) -> str:
    return {"Day": f"{start:%b %d}", "Week": f"{start:%b %d}", "Month": f"{start:%b %y}", "Quarter": f"Q{(start.month - 1) // 3 + 1} {start:%y}", "Year": f"{start:%Y}"}[kind]


def _trend(kind: str, start: date, periods: int = 6) -> list[dict]:
    """Paid and scheduled totals for this period and the (periods - 1) before it, oldest first."""
    bounds = [(start, period_bounds(kind, start)[1])]
    for _ in range(periods - 1):
        s, e, _label = previous_bounds(kind, bounds[0][0])
        bounds.insert(0, (s, e))
    out = []
    with sqlite3.connect(DB_PATH) as conn:
        for s, e in bounds:
            cash = _cash_rows(conn, s, e)
            out.append({"label": _short_label(kind, s), "paid": float(cash["paid"].sum()) if not cash.empty else 0.0,
                        "scheduled": float(cash["scheduled"].sum()) if not cash.empty else 0.0})
    return out


def _bank(start: date, end: date, pstart: date, pend: date) -> dict:
    """Bank and cash for the period (engines/cash_insights_service.period_bank_summary); 'none' on a database without the bank tables."""
    try:
        from engines.cash_insights_service import period_bank_summary

        return period_bank_summary(start, end, pstart, pend)
    except sqlite3.OperationalError:
        return {"covered": "none", "first": None, "last": None}


def _bank_highlight(kind: str, b: dict) -> str | None:
    if b["covered"] == "none":
        return None
    ch = None if b["money_in_prev"] is None else _pct_change(b["money_in"], b["money_in_prev"])
    trend = "" if ch is None else f" ({abs(ch):.0f}% {'more' if ch >= 0 else 'less'} than the previous {kind.lower()})"
    text = (f"Bank: {_php(b['money_in'])} came in{trend}; the bank cleared {b['cleared_n']} of your checks ({_php(b['cleared_amount'])}); "
            f"{b['float_n']} check(s) ({_php(b['float_amount'])}) were still not cleared on {b['as_of']:%b %d}.")
    if b["bounced_n"]:
        text += f" {b['bounced_n']} customer check(s) bounced ({_php(b['bounced_amount'])})."
    if b["covered"] == "part":
        text += f" (Bank statements cover {b['first']:%b %d} to {b['last']:%b %d, %Y} only.)"
    return text


def bank_rows(b: dict) -> list[list[str]]:
    """The Bank and cash section as [measure, figure] rows -- the same on screen and in the PDF. Empty when no statement covers the period."""
    if b["covered"] == "none":
        return []
    prev = "" if b["money_in_prev"] is None else f" (previous: {_php(b['money_in_prev'])})"
    rows = [["Bank balance" + (f" on {b['balance_date']:%b %d, %Y}" if b["balance_date"] else ""), _php(b["balance"]) if b["balance"] is not None else "-"],
            ["Money in (deposits and transfers)", _php(b["money_in"]) + prev],
            ["Checks cleared by the bank", f"{b['cleared_n']} · {_php(b['cleared_amount'])}"],
            ["Checks dated in the period", f"{b['issued_n']} · {_php(b['issued_amount'])}"],
            [f"Checks not yet cleared on {b['as_of']:%b %d}", f"{b['float_n']} · {_php(b['float_amount'])}"],
            ["Customer checks bounced", f"{b['bounced_n']} · {_php(b['bounced_amount'])}" if b["bounced_n"] else "None"],
            ["Bank lines still needing a look", str(b["open_items"])]]
    return rows


def bank_note(b: dict) -> str:
    """One line under the Bank and cash section: what the statements cover."""
    if b["covered"] == "none":
        return ("No imported bank statement covers this period" + (f" (statements on file: {b['first']:%b %d, %Y} to {b['last']:%b %d, %Y})" if b["first"] else "")
                + ". Import the Metrobank statement on Bank and cash.")
    part = " -- only part of this period" if b["covered"] == "part" else ""
    return f"From the imported Metrobank statements ({b['first']:%b %d, %Y} to {b['last']:%b %d, %Y}{part})."


def get_overview(kind: str, anchor: date) -> dict:
    start, end, label = period_bounds(kind, anchor)
    pstart, pend, plabel = previous_bounds(kind, start)
    with sqlite3.connect(DB_PATH) as conn:
        cur, prev = _summarise(conn, start, end), _summarise(conn, pstart, pend)
    upcoming = _upcoming(kind, end)
    watch = _watch_list()
    outstanding = get_combined_ap_summary()
    bank = _bank(start, end, pstart, pend)
    highlights = _highlights(kind, label, cur, prev, upcoming, watch)
    bank_line = _bank_highlight(kind, bank)
    if bank_line:
        highlights.insert(len(highlights) - (1 if watch else 0), bank_line)      # before the watch-list line, which stays last
    return {
        "kind": kind, "label": label, "start": start, "end": end, "prev_label": plabel,
        "current": cur, "previous": prev, "upcoming": upcoming, "watch": watch, "trend": _trend(kind, start),
        "outstanding_today": float(outstanding.loc[outstanding["track"] == "TOTAL", "outstanding"].iloc[0]),
        "highlights": highlights, "bank": bank, "my_reports": _my_reports(start, end),
    }


def _my_reports(start: date, end: date) -> list:
    """Saved "My reports" marked "Include in the overall report", run for this period (engines/report_builder.py)."""
    try:
        from engines.report_builder import overview_results

        return overview_results(start, end)
    except sqlite3.OperationalError:
        return []
