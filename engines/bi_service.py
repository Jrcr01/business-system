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


def _hbars(pairs: list[tuple[str, float]], note: dict | None = None) -> list[dict]:
    top = max([abs(v) for _, v in pairs] or [0]) or 1
    return [{"label": k, "amount": v, "pct": round(abs(v) / top * 100, 1), "note": (note or {}).get(k, "")} for k, v in pairs]


def stock(today: date) -> dict:
    """Analytics -> Stock (owner, 2026-10-15: "Go for 3"; the BI mock-up, section 2): what is tied up and what moves. Stock at average cost (the moving
    average the stock ledger keeps); "moving" = stock out in the last 30 days; a slow mover = on hand with no stock out in the slow-mover days (Settings
    -> Warnings). Tire age is by each item's oldest DOT on the rack (one per item, typed at receiving): a guide, not lot accounting."""
    from engines.settings_registry import int_value
    d30, slow_days = str(today - timedelta(days=30)), int_value("warn_slow_days")
    d_slow = str(today - timedelta(days=slow_days))
    with get_conn() as conn:
        items = conn.execute("""SELECT id, name, brand, category, on_hand, avg_cost, reorder_level, oldest_dot FROM items WHERE is_active = 1""").fetchall()
        out30 = dict(conn.execute("SELECT item_id, -SUM(qty) FROM stock_ledger WHERE kind = 'OUT' AND moved_on >= ? GROUP BY item_id", (d30,)).fetchall())
        cogs30 = float(conn.execute("SELECT COALESCE(SUM(-qty * unit_cost), 0) FROM stock_ledger WHERE kind = 'OUT' AND moved_on >= ?", (d30,)).fetchone()[0])
        moved_recently = {r[0] for r in conn.execute("SELECT DISTINCT item_id FROM stock_ledger WHERE kind = 'OUT' AND moved_on >= ?", (d_slow,))}
        older = {r[0] for r in conn.execute("SELECT DISTINCT item_id FROM stock_ledger WHERE moved_on < ?", (d_slow,))}
        stocked = {r[0] for r in conn.execute("SELECT DISTINCT item_id FROM stock_ledger")}
        hist = conn.execute("SELECT item_id, moved_on, on_hand_after, avg_after FROM stock_ledger ORDER BY moved_on, move_id").fetchall()
    held = [i for i in items if i[4] > 0]
    value = sum(i[4] * float(i[5]) for i in held)
    pieces = sum(i[4] for i in held)
    below = [i for i in items if i[0] in stocked and i[4] <= i[6]]
    zero = [i for i in below if i[4] <= 0]
    slow = sorted(((i[1], i[4], i[4] * float(i[5])) for i in held if i[0] not in moved_recently and i[0] in older and float(i[5]) > 0), key=lambda x: -x[2])
    by_brand: dict[str, float] = {}
    for i in held:
        key = i[2] if i[3] == "tire" or i[3] == "battery" else "Mags and rims"
        by_brand[key] = by_brand.get(key, 0.0) + i[4] * float(i[5])
    brands = sorted(by_brand.items(), key=lambda kv: -kv[1])
    if len(brands) > 8:
        brands = brands[:7] + [("Others", sum(v for _, v in brands[7:]))]
    fast = sorted(((i[1], float(out30[i[0]]), i[4]) for i in items if out30.get(i[0], 0) > 0), key=lambda x: -x[1])[:10]
    fast_note = {n: (f"{q:g} out · {round(h * 30 / q)} d left" if h > 0 else f"{q:g} out · none left") for n, q, h in fast}
    ages: dict[str, int] = {}
    for i in held:
        dot = str(i[7] or "")
        y = ("20" + dot[-2:]) if len(dot) == 4 and dot.isdigit() else "No DOT typed"
        ages[y] = ages.get(y, 0) + i[4]
    old_cut = str(today.year - 2)
    age_rows = sorted(((y if y == "No DOT typed" or y > old_cut else f"{old_cut} and older", n) for y, n in ages.items()), key=lambda x: x[0], reverse=True)
    merged: dict[str, int] = {}
    for y, n in age_rows:
        merged[y] = merged.get(y, 0) + n
    age = [(y, float(n)) for y, n in merged.items() if y != "No DOT typed"] + ([("No DOT typed", float(merged["No DOT typed"]))] if "No DOT typed" in merged else [])
    # stock value at each of the last 12 month ends, from the ledger's running figures
    ends = []
    m = date(today.year, today.month, 1)
    for _ in range(12):
        ends.append(m - timedelta(days=1))
        m = shift_months(m, 1)
    ends = sorted(ends) + [today]
    last: dict[int, tuple[float, float]] = {}
    points, k = [], 0
    for end in ends:
        while k < len(hist) and str(hist[k][1])[:10] <= str(end):
            last[hist[k][0]] = (float(hist[k][2]), float(hist[k][3]))
            k += 1
        points.append((("Now" if end == today else end.strftime("%b")), sum(q * c for q, c in last.values() if q > 0)))
    first_with = next((n for n, (_, v) in enumerate(points) if v > 0), len(points))
    points = points[first_with:] if len(points) - first_with >= 2 else points[-2:]
    return {"value": value, "pieces": pieces, "items": len(held), "days": round(value / (cogs30 / 30)) if cogs30 > 0 else None, "cogs30": cogs30,
            "below": len(below), "zero": len(zero), "slow_value": sum(x[2] for x in slow), "slow_n": len(slow), "slow_days": slow_days,
            "brands": _hbars(brands), "fast": _hbars([(n, q) for n, q, _ in fast], fast_note),
            "slow": _hbars([(n, v) for n, _, v in slow[:10]], {n: f"{q} pcs" for n, q, _ in slow[:10]}),
            "age": _hbars(age), "old_label": f"{old_cut} and older", "line": line(points), "line_from": points[0][0] if points else ""}
