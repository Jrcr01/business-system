"""Pay suppliers -> Calendar (owner, 2026-10-09: "drop payables due and make the calendar a tile"; mock-up approved): a month of payables, every kind --
each day its total and count, green when paid (cleared or ticked), amber when still to pay, grey for weekends, holidays and no-check days; a day's list
below; a day can be marked a holiday / no-check day / note. Unpaid items from earlier days count on today, as in the Schedule of payments.
The figures: engines/reports_service.get_payment_calendar_items / attach_calendar_totals; the marks: engines/calendar_marks.py."""
from __future__ import annotations

import calendar as _cal
from datetime import date, timedelta

from django.contrib import messages
from django.shortcuts import redirect, render

from buying.views import _date
from masters.views import page_needed


@page_needed("calendar")
def payables_calendar(request):
    from engines import calendar_marks as cm
    from engines.permissions import can
    today = date.today()
    if request.method == "POST":
        day = _date(request.POST.get("day"))
        if day:
            ok, msg = (cm.unmark_day(day) if request.POST.get("action") == "unmark"
                       else cm.mark_day(day, request.POST.get("kind", "holiday"), request.POST.get("label", "")))
            (messages.success if ok else messages.error)(request, msg)
        return redirect(f"/calendar/?m={request.POST.get('m', '')}&day={day or ''}")
    m = request.GET.get("m", "")
    try:
        first = date(int(m[:4]), int(m[5:7]), 1)
    except (ValueError, IndexError):
        first = today.replace(day=1)
    pick = _date(request.GET.get("day")) or (today if first.month == today.month and first.year == today.year else first)
    import pandas as pd
    from engines.reports_service import attach_calendar_totals, get_payment_calendar_items
    weeks = _cal.Calendar(firstweekday=0).monthdatescalendar(first.year, first.month)
    grid = pd.DataFrame({"date": [d for w in weeks for d in w]})
    figs = {r["date"]: r for r in attach_calendar_totals(grid).to_dict("records")}
    marks = cm.marks_for_month(first.year, first.month)
    for d in {d for w in weeks for d in w} - {d for d in marks}:
        if d.month != first.month and (d.year, d.month) != (first.year, first.month):
            marks.update({k: v for k, v in cm.marks_for_month(d.year, d.month).items() if k == d})
    from engines import day_cap
    grid_first, grid_last = weeks[0][0], weeks[-1][-1]
    plan_rows = day_cap.loads(max(grid_first, today), grid_last) if grid_last >= today else None     # the daily rule (owner, 2026-10-09): everything going out
    plan = {} if plan_rows is None or plan_rows.empty else {d: float(v) for d, v in plan_rows.groupby("day")["amount"].sum().items()}
    cells = []
    for w in weeks:
        row = []
        for d in w:
            f, mk = figs.get(d, {}), marks.get(d)
            n = int(f.get("num_items") or 0)
            row.append({"d": d, "out": d.month != first.month, "today": d == today, "pick": d == pick, "n": n, "total": float(f.get("total_amount") or 0),
                        "state": f.get("status") or "", "off": d.weekday() >= 5 or (mk and mk["kind"] in ("holiday", "custom")), "mark": mk,
                        "paid_n": int(f.get("paid_items") or 0), "due_n": int(f.get("due_items") or 0),
                        "plan": plan.get(d, 0.0), "lvl": day_cap.level(plan.get(d, 0.0)) if plan.get(d) else ""})
        cells.append(row)
    items = get_payment_calendar_items()
    day_rows = []
    if not items.empty:
        part = items[pd.to_datetime(items["actual_payment_date"]).dt.date == pick]
        for r in part.itertuples():
            due = date.fromisoformat(str(r.due_date)[:10])
            day_rows.append({"payee": r.payee_name, "kind": {"Bank Loan": "Bank loan", "Credit Card": "Card"}.get(r.track, r.track),
                             "ref": r.reference if isinstance(r.reference, str) else "", "amount": float(r.amount), "paid": r.state == "Paid",
                             "late_from": due if (r.state != "Paid" and due < pick) else None})
    extra = []                                                  # on the picked day: what counts against the limit beyond what is owed -- open orders, card reserves
    if plan_rows is not None and not plan_rows.empty:
        for r in plan_rows[(plan_rows["day"] == pick) & ((plan_rows["kind"] == "Purchase orders") | plan_rows["reference"].astype(str).str.startswith("Card reserve"))].itertuples():
            extra.append({"payee": r.payee, "kind": "Order (committed)" if r.kind == "Purchase orders" else "Card reserve",
                          "ref": r.reference, "amount": float(r.amount)})
    prev_m = (first - timedelta(days=1)).replace(day=1)
    next_m = (first + timedelta(days=32)).replace(day=1)
    return render(request, "payables/calendar.html", {
        "page_id": "calendar", "first": first, "cells": cells, "pick": pick, "pick_mark": marks.get(pick), "day_rows": day_rows,
        "day_total": sum(r["amount"] for r in day_rows), "prev": prev_m.strftime("%Y-%m"), "next": next_m.strftime("%Y-%m"), "m": first.strftime("%Y-%m"),
        "this_m": today.strftime("%Y-%m"), "kinds": list(cm.KINDS.items()), "may_mark": can(request.slicks_user["role"], "calendar.mark"),
        "month_total": sum(c["total"] for w in cells for c in w if not c["out"]),
        "extra": extra, "pick_plan": plan.get(pick, 0.0), "pick_lvl": day_cap.level(plan.get(pick, 0.0)) if plan.get(pick) else "",
        "cap_target": day_cap.target(), "cap_ceiling": day_cap.ceiling()})
