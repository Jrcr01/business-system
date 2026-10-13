"""Analytics (Phase 8, owner 2026-10-09; SAP Business One's Analytics tab): its own top-bar section with four dashboards -- Payables, Lenders, Bank and cash,
Purchasing. Read only. The figures come from the engines the reports use (reports_service, analytics_service, dashboard_kpi_service, clearing), so a check
counts as paid only once it has cleared (engines/clearing.py), never by its date. The bars are drawn by the page itself (no chart library)."""
from __future__ import annotations

from datetime import date, timedelta

from django.shortcuts import render

from masters.views import page_needed
from engines.common import us_date

TABS = [("sales", "Sales"), ("cash", "Cash ahead"), ("purchasing", "Purchasing"), ("payables", "Payables"), ("bank", "Bank and cash"), ("lenders", "Lenders")]     # owner, 2026-10-14: sales and liquidity first
PERIODS = [("month", "This month"), ("quarter", "This quarter"), ("year", "This year"), ("custom", "From … to …")]
TRACK_OF = {"payables": "Supplier", "lenders": "Lender"}


def _rows(sql: str, params: tuple = ()) -> list:
    from engines.common import get_conn
    with get_conn() as conn:
        return conn.execute(sql, params).fetchall()


def _period(key: str, today: date, frm: str = "", to: str = "") -> tuple[date, date]:
    if key == "custom":
        try:
            a, b = date.fromisoformat(frm), date.fromisoformat(to)
            return (a, b) if a <= b else (b, a)
        except ValueError:
            return today.replace(day=1), today
    if key == "quarter":
        first = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
    elif key == "year":
        first = date(today.year, 1, 1)
    else:
        first = today.replace(day=1)
    return first, today


def _bars(pairs: list[tuple[str, float]], link: str = "") -> list[dict]:
    """[(label, amount)] -> [{'label', 'amount', 'pct'}], pct of the biggest (for the bar's length)."""
    top = max([abs(v) for _, v in pairs] or [0]) or 1
    return [{"label": k, "amount": v, "pct": round(abs(v) / top * 100, 1), "link": link} for k, v in pairs]


def _weeks(track: str | None, today: date, n: int = 8) -> list[dict]:
    """What is due per week for the next n weeks (Monday-based), from the schedule of payments; track None = all four tracks."""
    from engines.reports_service import get_payment_schedule_all_tracks
    df = get_payment_schedule_all_tracks()
    monday = today - timedelta(days=today.weekday())
    out = []
    for i in range(n):
        a, b = monday + timedelta(weeks=i), monday + timedelta(weeks=i, days=6)
        part = df[(df["actual_payment_date"] >= a) & (df["actual_payment_date"] <= b)] if not df.empty else df
        if track and not part.empty:
            part = part[part["track"] == track]
        out.append((f"{a:%m/%d}", float(part["amount"].sum()) if not part.empty else 0.0))
    return _bars(out)


def _summary() -> dict:
    from engines.reports_service import get_combined_ap_summary
    df = get_combined_ap_summary()
    return {str(r["track"]): float(r["outstanding"]) for _, r in df.iterrows()}


@page_needed("analytics")
def analytics(request):
    from engines.clock import manila_today
    today = manila_today()
    from engines.permissions import analytics_tabs
    seen = analytics_tabs()                                          # a page hidden from this person hides its dashboard too (owner, 2026-10-11)
    tabs = [t for t in TABS if t[0] in seen]
    if not tabs:
        from core.menu import PAGES
        return render(request, "core/not_allowed.html", {"page": PAGES["analytics"]}, status=403)
    tab = request.GET.get("tab") if request.GET.get("tab") in dict(tabs) else ("payables" if "payables" in dict(tabs) else tabs[0][0])     # Payables opens first, as before
    per = request.GET.get("period") if request.GET.get("period") in dict(PERIODS) else "month"
    start, end = _period(per, today, request.GET.get("from", ""), request.GET.get("to", ""))
    ctx = {"page_id": "analytics", "tabs": tabs, "tab": tab, "periods": PERIODS, "period": per, "start": start, "end": end, "today": today}
    try:
        ctx.update(globals()[f"_{tab}"](today, start, end))
        ctx["charts"] = [ctx.get("chart_a"), ctx.get("chart_b"), ctx.get("chart_c")]
    except Exception as e:                                           # noqa: BLE001 -- a broken figure must not take the page down
        ctx["error"] = f"This dashboard could not be drawn: {e}"
    return render(request, "payables/analytics.html", ctx)


def _sales(today, start, end) -> dict:
    """Owner, 2026-10-14: what happened and why (engines/bi_service.sales); drawn by analytics_sales.html."""
    from engines import bi_service
    period = "custom" if (start, end) not in (_period(k, today) for k in ("month", "quarter", "year")) else next(k for k in ("month", "quarter", "year") if _period(k, today) == (start, end))
    return {"bi": bi_service.sales(today, start, end, period), "include": "payables/analytics_sales.html"}


def _cash(today, start, end) -> dict:
    """Owner, 2026-10-14: liquidity, the bank balance 60 days ahead (engines/bi_service.liquidity); drawn by analytics_cash.html."""
    from engines import bi_service
    return {"bi": bi_service.liquidity(today), "include": "payables/analytics_cash.html"}


def _payables(today, start, end) -> dict:
    from engines.clearing import waiting_for_bank
    from engines.dashboard_kpi_service import get_top_suppliers
    s = _summary()
    week = _weeks("Supplier", today, 1)[0]["amount"]
    wait = waiting_for_bank()
    wait = wait[wait["track"] == "Supplier"] if "track" in wait.columns else wait
    loose = _rows("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM invoices WHERE statement_id IS NULL AND status IN ('Unpaid', 'Partially Paid')")[0]
    top = get_top_suppliers("outstanding", 10)
    from engines.reports_service import get_supplier_ap_aging_report        # supplier aging, kept from the old reports (owner, 2026-10-09)
    aging = get_supplier_ap_aging_report()
    aging_rows = [] if aging.empty else aging.sort_values("total_unpaid", ascending=False).to_dict("records")
    return {"aging": aging_rows, "aging_total": {k: float(aging[k].sum()) for k in ("total_unpaid", "current_due", "days_1_30", "days_31_60", "days_over_60")} if aging_rows else None,
            "cards": [("Owed to suppliers", s.get("Supplier (Order Statements)", 0), "peso", "/calendar/"),
                      ("Due this week", week, "peso", "/calendar/"),
                      ("Checks waiting for the bank", len(wait), "count", "/checks/?tab=waiting"),
                      ("Invoices not in a batch", loose[0], "count", "/order-statements/")],
            "chart_a": ("Due to suppliers, next 8 weeks", _weeks("Supplier", today)),
            "chart_b": ("Owed by supplier (top 10)", _bars([(r["supplier_name"], float(r["value"])) for _, r in top.iterrows()]))}


def _lenders(today, start, end) -> dict:
    from engines.analytics_service import get_lender_pareto
    s = _summary()
    nxt = _rows("""SELECT COALESCE(SUM(i.principal + i.interest), 0) FROM loan_installments i JOIN loan_notes n ON n.loan_note_id = i.loan_note_id
                   WHERE n.status = 'Active' AND n.is_lease = 0 AND i.due_date BETWEEN ? AND ?""", (str(today), str(today + timedelta(days=30))))[0][0]
    interest = _rows("""SELECT substr(i.due_date, 1, 7), COALESCE(SUM(i.interest), 0) FROM loan_installments i JOIN loan_notes n ON n.loan_note_id = i.loan_note_id
                        WHERE n.status = 'Active' AND n.is_lease = 0 AND i.due_date >= ? GROUP BY 1 ORDER BY 1 LIMIT 6""", (str(today.replace(day=1)),))
    notes = _rows("SELECT COUNT(*) FROM loan_notes WHERE status = 'Active' AND is_lease = 0")[0][0]
    pareto = get_lender_pareto(10)
    return {"cards": [("Owed to lenders", s.get("Private Lender (Loan Notes)", 0), "peso", "/lenders/"),
                      ("Payments next 30 days", float(nxt), "peso", "/lenders/"),
                      ("Interest, next 6 months", sum(float(v) for _, v in interest), "peso", "/lenders/"),
                      ("Active loan notes", notes, "count", "/lenders/")],
            "chart_a": ("Lender payments, next 8 weeks", _weeks("Lender", today)),
            "chart_b": ("Owed per lender", _bars([(r["name"], float(r["value"])) for _, r in pareto.iterrows()])),
            "chart_c": ("Interest due by month", _bars([(date.fromisoformat(m + "-01").strftime("%b %Y"), float(v)) for m, v in interest]))}


def _bank(today, start, end) -> dict:
    from engines.clearing import waiting_for_bank
    last = _rows("SELECT COALESCE(posting_date, txn_date), balance FROM bank_transactions WHERE balance IS NOT NULL ORDER BY COALESCE(posting_date, txn_date) DESC, txn_id DESC LIMIT 1")
    trend = _rows("""SELECT m, balance FROM (SELECT substr(COALESCE(posting_date, txn_date), 1, 7) AS m, balance,
                                ROW_NUMBER() OVER (PARTITION BY substr(COALESCE(posting_date, txn_date), 1, 7) ORDER BY COALESCE(posting_date, txn_date) DESC, txn_id DESC) AS k
                         FROM bank_transactions WHERE balance IS NOT NULL) WHERE k = 1 ORDER BY m DESC LIMIT 6""")
    cleared = _rows("""SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM checks_issued WHERE status = 'Active'
                       AND COALESCE(bank_cleared_date, manual_cleared_at, settled_outside_at) BETWEEN ? AND ?""", (str(start), str(end) + " 23:59"))[0]
    wait = waiting_for_bank()
    out_week = _weeks(None, today, 1)[0]["amount"]
    return {"cards": [("Bank balance, last statement" + (f" ({us_date(last[0][0])})" if last else ""), float(last[0][1]) if last else 0, "peso", "/bank-cash/?tab=recon"),
                      ("Checks waiting for the bank", len(wait), "count", "/checks/?tab=waiting"),
                      ("Cleared in the period", float(cleared[1]), "peso", "/bank-cash/?tab=look"),
                      ("Cash going out this week", out_week, "peso", "/reports/")],
            "chart_a": ("Cash going out, next 8 weeks (suppliers, lenders, bank loans, cards)", _weeks(None, today)),
            "chart_b": ("Bank balance at each month's last statement line", _bars([(date.fromisoformat(m + "-01").strftime("%b %Y"), float(b)) for m, b in reversed(trend)]))}


def _purchasing(today, start, end) -> dict:
    a, b = str(start), str(end)
    open_po = _rows("SELECT COUNT(*), COALESCE(SUM((SELECT SUM(est_amount) FROM purchase_order_lines l WHERE l.po_id = p.po_id)), 0) FROM purchase_orders p WHERE status IN ('ORDERED', 'PARTIAL')")[0]
    spend = _rows("SELECT COALESCE(SUM(amount), 0) FROM invoices WHERE status != 'Cancelled' AND invoice_date BETWEEN ? AND ?", (a, b))[0][0]
    loose = _rows("SELECT COUNT(*), COALESCE(SUM(COALESCE(actual_amount, est_amount)), 0) FROM po_receipts WHERE invoice_id IS NULL")[0]
    by_sup = _rows("""SELECT c.creditor_name, SUM(i.amount) FROM invoices i JOIN creditors c ON c.creditor_id = i.creditor_id
                      WHERE i.status != 'Cancelled' AND i.invoice_date BETWEEN ? AND ? GROUP BY 1 ORDER BY 2 DESC LIMIT 10""", (a, b))
    by_brand = _rows("""SELECT COALESCE(NULLIF(it.brand, ''), '(no brand)'), SUM(COALESCE(l.actual_amount, l.est_amount)) FROM purchase_order_lines l
                        JOIN purchase_orders p ON p.po_id = l.po_id LEFT JOIN items it ON it.id = l.item_id
                        WHERE p.status != 'CANCELLED' AND p.status != 'DRAFT' AND p.order_date BETWEEN ? AND ? GROUP BY 1 ORDER BY 2 DESC LIMIT 10""", (a, b))
    return {"cards": [("Spend in the period (invoices)", float(spend), "peso", "/order-statements/"),
                      ("Open purchase orders", open_po[0], "count", "/purchase-orders/"),
                      ("Open orders' value", float(open_po[1]), "peso", "/purchase-orders/"),
                      ("Received, not invoiced", loose[0], "count", "/purchase-orders/?mode=match")],
            "chart_a": ("Spend by supplier in the period", _bars([(n, float(v)) for n, v in by_sup])),
            "chart_b": ("Ordered by brand in the period", _bars([(n, float(v or 0)) for n, v in by_brand]))}
