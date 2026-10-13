"""Business intelligence on Analytics (owner, 2026-10-14: "i want a business intelligence for my analytics" ... "our focus are now sales and liquidity").
Two dashboards, read only, every figure straight from the documents:
  * Sales -- what happened and why: sales and margin against the period before and the same period last year, by day, by month, by brand,
    by size, by customer type and by how they paid. A sale counts when it was sold (accrual, as the Income statement does); returns reduce
    the month they came back; voids and the opening receivables never count.
  * Liquidity -- what will happen: the bank balance 60 days ahead (engines/cash_insights_service.forecast), its lowest point, the heavy days.
The line charts are plain SVG drawn by the page from the points made here: no chart library."""
from __future__ import annotations

from datetime import date, timedelta

from engines.common import get_conn

LIVE = "s.status = 'SAVED' AND s.is_opening = 0"
W, H, PAD = 520, 170, (40, 14, 10, 26)          # the drawing box and its left, top, right, bottom margins


def _rows(sql: str, params: tuple = ()) -> list:
    with get_conn() as conn:
        return conn.execute(sql, params).fetchall()


def _one(sql: str, params: tuple = ()) -> float:
    return round(float(_rows(sql, params)[0][0] or 0), 2)


def shift_months(d: date, months: int) -> date:
    """The same day of the month `months` back (clamped to that month's length)."""
    import calendar
    m = d.month - 1 - months
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def earlier(start: date, end: date, period: str) -> tuple[date, date]:
    """The period before: the same days a month, a quarter or a year back; a custom range = the same length just before it."""
    if period in ("month", "quarter", "year"):
        n = {"month": 1, "quarter": 3, "year": 12}[period]
        return shift_months(start, n), shift_months(end, n)
    span = (end - start).days + 1
    return start - timedelta(days=span), start - timedelta(days=1)


def _totals(start: date, end: date) -> dict:
    a, b = str(start), str(end)
    goods = _one(f"SELECT COALESCE(SUM(l.amount), 0) FROM sale_lines l JOIN sales s ON s.sale_id = l.sale_id WHERE {LIVE} AND l.item_id IS NOT NULL AND s.sale_date BETWEEN ? AND ?", (a, b))
    services = _one(f"SELECT COALESCE(SUM(l.amount), 0) FROM sale_lines l JOIN sales s ON s.sale_id = l.sale_id WHERE {LIVE} AND l.item_id IS NULL AND s.sale_date BETWEEN ? AND ?", (a, b))
    returns = _one("SELECT COALESCE(SUM(amount), 0) FROM sale_returns WHERE returned_on BETWEEN ? AND ?", (a, b))
    cogs = _one(f"SELECT COALESCE(SUM(s.cost_total), 0) FROM sales s WHERE {LIVE} AND s.sale_date BETWEEN ? AND ?", (a, b))
    returns_cost = _one("SELECT COALESCE(SUM(r.qty * l.unit_cost), 0) FROM sale_returns r JOIN sale_lines l ON l.line_id = r.line_id WHERE r.returned_on BETWEEN ? AND ?", (a, b))
    pieces = _one(f"SELECT COALESCE(SUM(l.qty), 0) FROM sale_lines l JOIN sales s ON s.sale_id = l.sale_id WHERE {LIVE} AND l.item_id IS NOT NULL AND s.sale_date BETWEEN ? AND ?", (a, b)) \
        - _one("SELECT COALESCE(SUM(qty), 0) FROM sale_returns WHERE returned_on BETWEEN ? AND ?", (a, b))
    invoices = int(_one(f"SELECT COUNT(*) FROM sales s WHERE {LIVE} AND s.sale_date BETWEEN ? AND ?", (a, b)))
    sales = round(goods - returns + services, 2)
    margin = round(sales - (cogs - returns_cost), 2)
    return {"sales": sales, "goods": round(goods - returns, 2), "services": services, "returns": returns, "cogs": round(cogs - returns_cost, 2),
            "margin": margin, "margin_pct": round(margin / sales * 100, 1) if sales else 0.0, "pieces": int(round(pieces)), "invoices": invoices,
            "average": round(sales / invoices, 2) if invoices else 0.0}


def pct_change(now: float, before: float) -> float | None:
    return None if not before else round((now - before) / abs(before) * 100, 1)


def line(points: list[tuple[str, float]], points2: list[tuple[str, float]] | None = None, floor: float | None = None) -> dict:
    """One or two series on the same scale, as SVG polyline points plus axis ticks. `floor` draws a dashed line (the cash buffer)."""
    left, top, right, bottom = PAD
    vals = [v for _, v in points] + [v for _, v in (points2 or [])] + ([floor] if floor is not None else [])
    lo, hi = min(vals + [0.0]), max(vals + [0.0])
    if hi == lo:
        hi = lo + 1
    n = max(len(points), len(points2 or []), 2)
    x = lambda i: left + i * (W - left - right) / (n - 1)                                       # noqa: E731
    y = lambda v: top + (hi - v) / (hi - lo) * (H - top - bottom)                             # noqa: E731
    pts = lambda series: " ".join(f"{x(i):.1f},{y(v):.1f}" for i, (_, v) in enumerate(series))   # noqa: E731
    ticks = [{"y": round(y(v), 1), "label": short(v)} for v in (hi, (hi + lo) / 2, lo)]
    labels = [{"x": round(x(i), 1), "label": k} for i, (k, _) in enumerate(points) if i in (0, n // 2, n - 1)]
    last = points[-1] if points else None
    return {"w": W, "h": H, "a": pts(points), "b": pts(points2) if points2 else "", "ticks": ticks, "labels": labels,
            "zero": round(y(0), 1) if lo < 0 < hi else None, "floor": round(y(floor), 1) if floor is not None else None,
            "last": {"x": round(x(len(points) - 1), 1), "y": round(y(last[1]), 1), "label": last[0], "value": last[1]} if last else None}


def short(v: float) -> str:
    v = float(v or 0)
    s = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1_000_000:
        return f"{s}{v / 1_000_000:.1f}M".replace(".0M", "M")
    if v >= 1_000:
        return f"{s}{v / 1_000:.0f}k"
    return f"{s}{v:.0f}"


def sales(today: date, start: date, end: date, period: str = "month") -> dict:
    """The Sales dashboard's figures for the period (start..end), against the period before and the same period a year back."""
    a, b = str(start), str(end)
    now = _totals(start, end)
    p_start, p_end = earlier(start, end, period)
    before = _totals(p_start, p_end)
    y_start, y_end = shift_months(start, 12), shift_months(end, 12)
    year_ago = _totals(y_start, y_end)
    by_day = {d: float(v) for d, v in _rows(f"SELECT s.sale_date, SUM(s.total) FROM sales s WHERE {LIVE} AND s.sale_date BETWEEN ? AND ? GROUP BY 1", (a, b))}
    by_day_before = {d: float(v) for d, v in _rows(f"SELECT s.sale_date, SUM(s.total) FROM sales s WHERE {LIVE} AND s.sale_date BETWEEN ? AND ? GROUP BY 1", (str(p_start), str(p_end)))}
    days = [(start + timedelta(days=i)) for i in range((end - start).days + 1)]
    days_before = [(p_start + timedelta(days=i)) for i in range((p_end - p_start).days + 1)]
    series = [(d.strftime("%m/%d"), by_day.get(str(d), 0.0)) for d in days]
    series_before = [(d.strftime("%m/%d"), by_day_before.get(str(d), 0.0)) for d in days_before][:len(series)] if days_before else None
    first_month = shift_months(today.replace(day=1), 11)
    months = _rows(f"""SELECT substr(s.sale_date, 1, 7) AS m, SUM(s.total), SUM(s.total - s.cost_total) FROM sales s
                       WHERE {LIVE} AND s.sale_date >= ? GROUP BY 1 ORDER BY 1""", (str(first_month),))
    month_map = {m: (float(t), float(g)) for m, t, g in months}
    by_month = []
    for i in range(12):
        m = shift_months(first_month, -i)
        t, g = month_map.get(m.strftime("%Y-%m"), (0.0, 0.0))
        by_month.append({"label": m.strftime("%b %y"), "sales": t, "margin": g})
    top = max([r["sales"] for r in by_month] + [1.0])
    for r in by_month:
        r["pct"], r["mpct"] = round(r["sales"] / top * 100, 1), round(max(r["margin"], 0) / top * 100, 1)
    by_brand = _rows(f"""SELECT COALESCE(NULLIF(it.brand, ''), '(no brand)'), SUM(l.amount), SUM(l.amount - l.qty * l.unit_cost) FROM sale_lines l
                         JOIN sales s ON s.sale_id = l.sale_id JOIN items it ON it.id = l.item_id WHERE {LIVE} AND s.sale_date BETWEEN ? AND ?
                         GROUP BY 1 ORDER BY 2 DESC LIMIT 10""", (a, b))
    by_size = _rows(f"""SELECT it.size_model, SUM(l.qty) FROM sale_lines l JOIN sales s ON s.sale_id = l.sale_id JOIN items it ON it.id = l.item_id
                        WHERE {LIVE} AND s.sale_date BETWEEN ? AND ? GROUP BY 1 ORDER BY 2 DESC LIMIT 10""", (a, b))
    by_type = _rows(f"""SELECT CASE WHEN s.customer_id IS NULL THEN 'Walk-in' ELSE COALESCE(c.customer_type, 'OTHER') END, SUM(s.total) FROM sales s
                        LEFT JOIN customers c ON c.customer_id = s.customer_id WHERE {LIVE} AND s.sale_date BETWEEN ? AND ? GROUP BY 1 ORDER BY 2 DESC""", (a, b))
    by_method = _rows(f"""SELECT p.method, SUM(p.amount) FROM sale_payments p JOIN sales s ON s.sale_id = p.sale_id
                          WHERE {LIVE} AND s.sale_date BETWEEN ? AND ? GROUP BY 1 ORDER BY 2 DESC""", (a, b))
    types = {"Walk-in": "Walk-in", "RETAIL": "Retail (named)", "FLEET": "Fleet", "DEALER": "Dealer", "OTHER": "Other"}
    methods = {"CASH": "Cash", "CHARGE": "On credit (charge)", "TRANSFER": "GCash / transfer", "CARD": "Card", "CHECK": "Check"}
    return {"now": now, "before": before, "year_ago": year_ago, "before_range": (p_start, p_end), "year_range": (y_start, y_end),
            "delta": {k: pct_change(now[k], before[k]) for k in ("sales", "margin", "pieces", "average")},
            "delta_year": {k: pct_change(now[k], year_ago[k]) for k in ("sales", "margin", "pieces", "average")},
            "by_day": line(series, series_before), "by_month": by_month,
            "by_brand": [{"label": n, "amount": float(v), "margin_pct": round(float(g) / float(v) * 100, 1) if v else 0.0} for n, v, g in by_brand],
            "by_size": [(n, int(round(float(q)))) for n, q in by_size],
            "by_type": [(types.get(t, t), float(v)) for t, v in by_type], "by_method": [(methods.get(m, m), float(v)) for m, v in by_method]}


def liquidity(today: date, days: int = 60) -> dict:
    """The bank balance ahead: the forecast's line, its lowest point against the buffer, what goes out and is expected in, the heavy days."""
    from engines.cash_insights_service import forecast, forecast_fresh_days, inflow_weeks
    fc = forecast(days, today)
    if not fc:
        return {"none": True}
    daily, out = fc["daily"], fc["outflows"]
    series = [(r["day"].strftime("%m/%d"), float(r["balance"])) for _, r in daily.iterrows()]
    heavy = []
    if not out.empty:
        for day, part in sorted(out.groupby("day"), key=lambda kv: -float(kv[1]["amount"].sum()))[:5]:
            lines = part.sort_values("amount", ascending=False)
            what = " · ".join(f"{r['payee']} ({short(r['amount'])})" for _, r in lines.head(3).iterrows()) + (f" · {len(lines) - 3} more" if len(lines) > 3 else "")
            heavy.append({"day": day, "amount": float(part["amount"].sum()), "what": what})
        heavy.sort(key=lambda h: h["day"])
    by_track = out.groupby("track")["amount"].sum().to_dict() if not out.empty else {}
    low_day, low = fc["lowest"]
    return {"none": False, "opening": fc["opening"], "as_of": fc["as_of"], "stale_days": fc["stale_days"], "stale": fc["stale_days"] > forecast_fresh_days(),
            "buffer": fc["buffer"], "lowest": low, "lowest_day": low_day, "first_below": fc["first_below"], "days": days,
            "money_out": float(daily["money_out"].sum()), "money_in": float(daily["money_in"].sum()), "by_track": {k: float(v) for k, v in by_track.items()},
            "end_balance": float(daily["balance"].iloc[-1]), "weeks": inflow_weeks(), "heavy": heavy,
            "line": line(series, floor=fc["buffer"] if fc["buffer"] else None)}
