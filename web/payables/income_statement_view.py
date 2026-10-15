"""Records > Income statement (owner, 2026-10-11: "just focus on income statement"): the month's profit straight from the documents, months side
by side, Excel out. Read-only; accrual by nature (a charge sale counts when sold)."""
from __future__ import annotations

from django.http import HttpResponse
from django.shortcuts import render

from masters.views import page_needed

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _month(request) -> str:
    from engines.clock import manila_today
    m = request.GET.get("m", "")
    return m if len(m) == 7 and m[:4].isdigit() and m[5:7].isdigit() else manila_today().strftime("%Y-%m")


def _prev(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def _next(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


def _label(month: str) -> str:
    from datetime import date
    return date(int(month[:4]), int(month[5:7]), 1).strftime("%B %Y")


@page_needed("income-statement")
def income_statement(request):
    from engines.clock import manila_today
    from engines.income_statement import month_report, xlsx
    month = _month(request)
    now = month_report(month)
    before = month_report(_prev(month))
    if request.GET.get("dl") == "xlsx":
        resp = HttpResponse(xlsx([now, before]), content_type=XLSX)
        resp["Content-Disposition"] = f'attachment; filename="income_statement_{month}.xlsx"'
        return resp
    cats = []
    b = dict(before["expenses"])
    for name, v in now["expenses"]:
        cats.append({"name": name, "now": v, "before": b.pop(name, None)})
    for name, v in b.items():
        cats.append({"name": name, "now": None, "before": v})
    kinds, ob = [], dict(before.get("other_income", []))
    for name, v in now.get("other_income", []):
        kinds.append({"name": name, "now": v, "before": ob.pop(name, None)})
    for name, v in ob.items():
        kinds.append({"name": name, "now": None, "before": v})
    return render(request, "payables/income_statement.html", {
        "page_id": "income-statement", "now": now, "before": before, "cats": cats, "kinds": kinds,
        "label": _label(month), "before_label": _label(_prev(month)), "m": month, "prev": _prev(month),
        "nxt": _next(month) if month < manila_today().strftime("%Y-%m") else ""})
