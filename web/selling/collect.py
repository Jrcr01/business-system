"""Sell > Collect payments (stage B part 1; owner, 2026-10-11: "the important to us is receivables can be track, any payments thereafter, BIR CR
is optional"). Whoever is signed in at the counter takes the payment. The rules are engines/collect_rules.py; this view draws the page and passes
on what was typed. A person who may only see gets no saves (page_needed)."""
from __future__ import annotations

from django.shortcuts import redirect, render

from masters.views import page_needed
from selling.sales import _payments, _say


@page_needed("collect")
def collect(request):
    from engines import collect_rules as cr
    from engines import sales_rules as sr
    from engines.clock import manila_today
    from engines.permissions import can

    if request.method == "POST":
        p, what = request.POST, request.POST.get("action")
        if what == "collect":
            cust = int(p.get("customer_id") or 0)
            lines = [{"sale_id": k.split("_", 1)[1], "amount": p.get(k, "")} for k in p if k.startswith("amt_")]
            ok, msg, _kid = cr.collect(cust, p.get("cr_no", ""), p.get("collect_date", ""), lines, _payments(p), p.get("note", ""),
                                       white_copy=p.get("white_copy") == "1")
            _say(request, ok, msg)
            return redirect(f"/collect/?c={cust}")
        if what == "void":
            ok, msg = cr.void_collection(int(p.get("collection_id") or 0), p.get("reason", ""))
            _say(request, ok, msg)
            return redirect(f"/collect/?c={p.get('c', '')}")
        if what == "write_off":
            ok, msg = cr.write_off(int(p.get("sale_id") or 0), p.get("reason", ""))
            _say(request, ok, msg)
            return redirect(f"/collect/?c={p.get('c', '')}")
        if what == "mark_check":
            from engines import customer_checks as cc
            ok, msg = cc.mark(p.get("source", ""), int(p.get("payment_id") or 0), p.get("what", ""), p.get("on", ""), p.get("note", ""))
            _say(request, ok, msg)
            return redirect("/collect/?view=checks" + ("&show=" + p.get("show") if p.get("show") else ""))
        if what == "opening":
            rows = [{"customer": p.get(f"op_cust_{k}", ""), "si_no": p.get(f"op_si_{k}", ""), "sale_date": p.get(f"op_date_{k}", ""),
                     "amount": p.get(f"op_amt_{k}", "")} for k in range(12)]
            ok, msg = cr.opening(rows)
            _say(request, ok, msg)
            return redirect("/collect/" + ("?tab=opening" if not ok else ""))
        _say(request, False, "Unknown action.")
        return redirect("/collect/")

    if request.GET.get("view") == "checks":                 # the customers' checks, by check date (part 2)
        from engines import customer_checks as cc
        show = request.GET.get("show") if request.GET.get("show") in ("all", "bounced") else "open"
        return render(request, "selling/customer_checks.html", {"page_id": "collect", "rows": cc.checks(show), "show": show,
                                                                "today": manila_today().isoformat()})
    cr.flag_overdue()
    owing = [c for c in sr.customers() if c["charged"] > 0.005]
    ctx = {"page_id": "collect", "owing": owing, "today": manila_today().isoformat(), "is_admin": can(request.slicks_user["role"], "settings.edit"),
           "methods": list(cr.METHODS.items()), "tab": "opening" if request.GET.get("tab") == "opening" else "collect", "op_rows": range(12)}
    cid = int(request.GET["c"]) if request.GET.get("c", "").isdigit() else None
    if cid:
        ctx.update({"cust": sr.customer(cid), "rows": cr.owed_rows(cid), "bal": cr.balance(cid), "aging": cr.aging(cid),
                    "advance": cr.advance_balance(cid), "history": cr.collections_of(cid)})
    return render(request, "selling/collect.html", ctx)
