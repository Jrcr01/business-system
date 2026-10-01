"""One combined "needs attention" list for the Dashboard (owner-approved plan H, 2026-09-30: "Needs attention counts only card alerts and booklet gaps; it ignores
bank lines, checks not yet cleared, stale checks, Data health, missing statements ..."). Every source that used to be its own banner or buried on another page is
gathered here as plain items: {level: error | warning | info, text, page, group}. `page` is the menu page that fixes it (views.shared.go_to key). The Dashboard shows
the list and counts error + warning as "Needs attention"; info items are shown but not counted. A source whose tables are missing (an older database) is skipped."""
from __future__ import annotations

import sqlite3
from datetime import date

from engines.bank_names import card_display_name
from engines.common import php

LEVELS = {"error": 0, "warning": 1, "info": 2}


def _item(level: str, text: str, page: str, group: str) -> dict:
    return {"level": level, "text": text, "page": page, "group": group}


def _safe(fn):
    """Run one source; an older database without its tables just contributes nothing."""
    try:
        return fn()
    except sqlite3.OperationalError:
        return []


def _cards() -> list[dict]:
    from engines.cc_service import get_statement_gap_alerts

    out = []
    df = get_statement_gap_alerts()
    if not df.empty:
        missing = df[df["alert"] == "Possibly missing a statement"]
        if not missing.empty:
            names = "; ".join(card_display_name(r["issuer"], r["card_name"]) for _, r in missing.iterrows())
            out.append(_item("error", f"{len(missing)} credit card statement(s) may have been overlooked — {names}.", "credit-cards", "Credit cards"))
    import pandas as pd
    from engines.settings_store import DB_PATH

    with sqlite3.connect(DB_PATH) as conn:
        due = pd.read_sql_query("SELECT bank_issuer, card_name, due_date, total_amount_due FROM v_cc_due_alerts WHERE urgency_status LIKE 'CRITICAL%'", conn)
    for _, r in due.iterrows():
        when = f"was due {r['due_date']} (still unpaid)" if str(r["due_date"])[:10] < date.today().isoformat() else f"is due {r['due_date']}"
        out.append(_item("warning", f"{card_display_name(r['bank_issuer'], r['card_name'])} {when} — {php(r['total_amount_due'])}.", "credit-cards", "Credit cards"))
    return out


def _booklets() -> list[dict]:
    from engines.checkbook_service import get_checkbook_alerts, unrecorded_alerts

    out = []
    gaps = get_checkbook_alerts()
    if not gaps.empty:
        text = "; ".join(f"{r['booklet_code']} ({r['missing_gaps_count']} skipped, {r['duplicate_numbers_count']} duplicate)" for _, r in gaps.iterrows())
        out.append(_item("error", f"{len(gaps)} checkbook(s) have unaccounted checks — {text}.", "admin", "Checks"))
    unrecorded = unrecorded_alerts()
    if not unrecorded.empty:
        text = "; ".join(f"{r.booklet}: {int(r.count)}" for r in unrecorded.itertuples())
        out.append(_item("error", f"{int(unrecorded['count'].sum())} check number(s) look used but are not recorded — {text}.", "payment-run", "Checks"))
    return out


def _guarantees() -> list[dict]:
    from engines.guarantee_check_service import exposure, presented_count

    out = []
    paid = presented_count()
    if paid:
        out.append(_item("error", f"The bank paid or received {paid} guarantee check(s). They were only given as security — look into it.", "bank-cash", "Checks"))
    ex = exposure()
    if ex["held"]:
        extra = f" (+ {ex['no_amount']} with no amount stated)" if ex["no_amount"] else ""
        out.append(_item("info", f"Guarantee checks held by lenders: {ex['held']} · {php(ex['total'])}{extra}. Not payments; return them when the loans are settled.", "checks", "Checks"))
    return out


def _bank() -> list[dict]:
    from engines.bank_alerts import all_alerts
    from engines.cash_insights_service import dashboard_bank_summary

    out = [_item(a["level"], a["message"], "bank-cash", "Bank") for a in all_alerts()]
    summary = dashboard_bank_summary()
    if summary:
        if summary["open_items"]:
            out.append(_item("warning", f"{summary['open_items']} bank line(s) need a look — resolve them on Bank and cash → Reconciliation.", "bank-cash", "Bank"))
        if summary["float_count"]:
            out.append(_item("info", f"{summary['float_count']} check(s) past their date are not yet cleared by the bank, {php(summary['float_amount'])}.", "bank-cash", "Bank"))
    return out


def _stale() -> list[dict]:
    from engines.dashboard_kpi_service import get_stale_checks
    from engines.settings_registry import int_value

    days = int_value("stale_check_days")
    stale = get_stale_checks(days)
    if stale["n"]:
        return [_item("warning", f"{stale['n']} check(s) dated more than {days} days ago are still not cleared, {php(stale['total'])} — probably lost or never deposited.", "bank-cash", "Checks")]
    return []


def _health() -> list[dict]:
    from engines.data_health_service import findings

    n = len(findings())
    return [_item("warning", f"Data health found {n} thing(s) to look at.", "data-health", "Data")] if n else []


def _concentration() -> list[dict]:
    from engines.dashboard_kpi_service import get_top_supplier_dependency
    from engines.settings_registry import value

    top = get_top_supplier_dependency()
    limit = value("supplier_concentration_pct")
    if top and top["pct"] >= limit:
        return [_item("warning", f"Supplier concentration — {top['name']} alone is {top['pct']:.0f}% of what is owed to suppliers "
                                 f"({php(top['amount'])} of {php(top['total_outstanding'])}); the alert level is {limit:g}% (Settings).", "analytics", "Suppliers")]
    return []


def _to_print() -> list[dict]:
    import pandas as pd
    from datetime import timedelta
    from engines.check_run_service import get_pending_check_run

    df = get_pending_check_run()
    if df.empty:
        return []
    soon = df[pd.to_datetime(df["check_date"]).dt.date <= date.today() + timedelta(days=7)]
    if soon.empty:
        return []
    return [_item("info", f"{len(soon)} check(s), {php(soon['amount'].sum())}, are dated within 7 days and not printed yet.", "checks", "Checks")]


SOURCES = (_cards, _booklets, _guarantees, _bank, _stale, _health, _concentration, _to_print)


def attention_items() -> list[dict]:
    """Every item, most urgent first (errors, then warnings, then info; stable inside a level)."""
    out: list[dict] = []
    for source in SOURCES:
        out.extend(_safe(source))
    return sorted(out, key=lambda i: LEVELS[i["level"]])


def attention_count(items: list[dict] | None = None) -> int:
    """What the Dashboard's "Needs attention" figure counts: errors and warnings, not info."""
    return sum(1 for i in (attention_items() if items is None else items) if i["level"] in ("error", "warning"))
