"""Bank > Other income (owner, 2026-10-13; go 2026-10-15): money received that is not a sale -- rent received, supplier rebates. The rules are
engines/income_rules.py; this view draws the page and passes on what was typed. Same people and same switch as Expenses."""
from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect, render

from masters.views import page_needed


@page_needed("other-income")
def other_income(request):
    from engines import income_rules as ir
    from engines.clock import manila_today
    from engines.permissions import can
    say = lambda ok, msg: (messages.success if ok else messages.error)(request, msg)      # noqa: E731
    if request.method == "POST":
        p, what = request.POST, request.POST.get("action")
        if what == "add":
            ok, msg, _iid = ir.add(p.get("income_date", ""), p.get("kind_id"), p.get("received_in", ""), p.get("detail", ""), p.get("amount", ""))
        elif what == "void":
            ok, msg = ir.void(int(p.get("income_id") or 0), p.get("reason", ""))
        elif what == "kind":
            ok, msg = ir.save_kind(int(p["kind_id"]) if p.get("kind_id", "").isdigit() else None, p.get("name", ""), p.get("off") != "1")
        else:
            ok, msg = False, "Unknown action."
        say(ok, msg)
        return redirect("/other-income/" + (f"?m={p.get('m')}" if p.get("m") else ""))
    month = request.GET.get("m") or manila_today().strftime("%Y-%m")
    rows = ir.listing(month=month)
    return render(request, "payables/other_income.html", {
        "page_id": "other-income", "rows": rows, "m": month, "today": manila_today().isoformat(),
        "total": round(sum(r["amount"] for r in rows if not r["void"]), 2), "by_kind": ir.month_by_kind(month),
        "kinds": ir.kinds(), "all_kinds": ir.kinds(all_rows=True), "places": ir.received_in_choices(),
        "is_admin": can(request.slicks_user["role"], "settings.edit")})
