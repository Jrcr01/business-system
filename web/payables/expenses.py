"""Bank > Expenses (owner, 2026-10-11): the drawer's and the bank's money out that is no supplier payable. The rules are engines/expense_rules.py;
this view draws the page and passes on what was typed. A person who may only see gets no saves (page_needed)."""
from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect, render

from masters.views import page_needed


@page_needed("expenses")
def expenses(request):
    from engines import expense_rules as er
    from engines.clock import manila_today
    from engines.permissions import can
    say = lambda ok, msg: (messages.success if ok else messages.error)(request, msg)      # noqa: E731
    if request.method == "POST":
        p, what = request.POST, request.POST.get("action")
        if what == "add":
            ok, msg, _eid = er.add(p.get("expense_date", ""), p.get("category_id"), p.get("paid_from", ""), p.get("detail", ""), p.get("amount", ""))
        elif what == "void":
            ok, msg = er.void(int(p.get("expense_id") or 0), p.get("reason", ""))
        elif what == "category":
            ok, msg = er.save_category(int(p["category_id"]) if p.get("category_id", "").isdigit() else None, p.get("name", ""), p.get("off") != "1")
        else:
            ok, msg = False, "Unknown action."
        say(ok, msg)
        return redirect("/expenses/" + (f"?m={p.get('m')}" if p.get("m") else ""))
    month = request.GET.get("m") or manila_today().strftime("%Y-%m")
    rows = er.listing(month=month)
    return render(request, "payables/expenses.html", {
        "page_id": "expenses", "rows": rows, "m": month, "month_label": month, "today": manila_today().isoformat(),
        "total": round(sum(r["amount"] for r in rows if not r["void"]), 2), "by_cat": er.month_by_category(month),
        "categories": er.categories(), "all_categories": er.categories(all_rows=True), "sources": er.paid_from_choices(),
        "is_admin": can(request.slicks_user["role"], "settings.edit")})
