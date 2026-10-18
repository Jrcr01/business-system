"""Stage C, customers (owner, 2026-10-17; mock-up docs/mockups/f_stage_c_customers.html; rules engines/quote_rules.py): Sell > Quotations (the quick
quote, kept for a customer), one quotation (print, share, make it a sale), Sell > Follow-ups, and one customer's page with its agreed prices."""
from __future__ import annotations

import json

from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render

from masters.views import page_needed


def _say(request, ok: bool, msg: str) -> None:
    (messages.success if ok else messages.error)(request, msg)


def _stage_c():
    from engines.features import revenue_on, stage_c_on
    return revenue_on() and stage_c_on()


@page_needed("quotations")
def quotations(request):
    from engines import quote_rules as qr
    show = request.GET.get("show", "waiting")
    if show not in ("waiting", "sold", "lapsed", "all"):
        show = "waiting"
    return render(request, "selling/quotations.html", {"page_id": "quotations", "rows": qr.quotes(show), "show": show, "n": qr.counts()})


@page_needed("quotations", "sales.use")
def quotation_new(request):
    """The quick quote on the laptop, for one size or several; Save as quotation keeps it for a customer."""
    from datetime import timedelta
    from engines import quote_rules as qr
    from engines import sales_rules as sr
    from engines.clock import manila_today
    from engines.settings_registry import int_value
    if request.method == "POST":
        p = request.POST
        try:
            lines = json.loads(p.get("lines") or "[]")
        except ValueError:
            lines = []
        ok, msg, qid = qr.save_quote(None, p.get("customer", ""), p.get("good_until"), lines, p.get("note", ""))
        _say(request, ok, msg)
        return redirect(f"/quotations/{qid}/" if ok else "/quotations/new/")
    return render(request, "selling/quotation_new.html", {"page_id": "quotations", "names": [c["name"] for c in sr.customers()],
                                                          "until": (manila_today() + timedelta(days=qr.good_days())).isoformat(), "step": int_value("quote_round_to"),
                                                          "size": request.GET.get("size", "")})


@page_needed("quotations")
def quotation_data(request):
    """For the builder's own script: sizes found (?q=), the brands on hand for one size (?size=), a customer's agreed prices (?customer=&items=)."""
    from engines import quote_rules as qr
    from engines import rim_stock
    if request.GET.get("q"):
        return JsonResponse({"sizes": [{"size": g["size"], "on_hand": g["total"]} for g in rim_stock.find(request.GET["q"]) if g["total"] > 0][:20]})
    if request.GET.get("size"):
        return JsonResponse({"size": request.GET["size"], "lines": rim_stock.quote_lines(request.GET["size"])})
    if "customer" in request.GET:
        from engines import sales_rules as sr
        name = " ".join(request.GET.get("customer", "").split()).lower()
        hit = next((c for c in sr.customers() if c["name"].lower() == name), None)
        ids = [int(i) for i in request.GET.get("items", "").split(",") if i.isdigit()]
        return JsonResponse({"known": bool(hit), "agreed": {str(k): v for k, v in qr.agreed(hit["customer_id"] if hit else None, ids).items()}})
    return JsonResponse({})


@page_needed("quotations")
def quotation(request, qt_id: int):
    from engines import quote_rules as qr
    q = qr.get_quote(qt_id)
    if not q:
        raise Http404
    if request.method == "POST":
        p, what = request.POST, request.POST.get("action")
        if what == "cancel":
            ok, msg = qr.cancel_quote(qt_id, p.get("reason", ""))
        elif what == "contact" and q["customer_id"]:
            ok, msg = qr.note_contact(q["customer_id"], p.get("note", ""), p.get("next_on"), qt_id)
        else:
            ok, msg = False, "Unknown action."
        _say(request, ok, msg)
        return redirect(f"/quotations/{qt_id}/")
    from engines.rim_stock import quote_footer
    if request.GET.get("print") == "1":                          # the customer's copy: selling prices only
        return render(request, "selling/quotation_print.html", {"q": q, "footer": quote_footer()})
    return render(request, "selling/quotation.html", {"page_id": "quotations", "q": q, "share": qr.share_text(q),
                                                      "margin": round(sum((ln["price"] - ln["cost"]) * ln["qty"] for ln in q["lines"] if ln["cost"]), 2)})


@page_needed("follow-ups")
def follow_ups(request):
    from engines import quote_rules as qr
    if request.method == "POST":
        p = request.POST
        ok, msg = qr.note_contact(int(p.get("customer_id") or 0), p.get("note", ""), p.get("next_on"), int(p["qt_id"]) if p.get("qt_id", "").isdigit() else None)
        _say(request, ok, msg)
        return redirect("/follow-ups/")
    return render(request, "selling/follow_ups.html", {"page_id": "follow-ups", "rows": qr.follow_ups()})


@page_needed("customers")
def customer_page(request, customer_id: int):
    """One customer (stage C): quotes, sales, what they owe, agreed prices, contact notes."""
    if not _stage_c():
        raise Http404
    from engines import quote_rules as qr
    if request.method == "POST":
        p, what = request.POST, request.POST.get("action")
        if what == "agreed":
            ok, msg = qr.set_agreed(customer_id, int(p["item_id"]) if p.get("item_id", "").isdigit() else None, p.get("brand", ""), p.get("price", ""),
                                    p.get("pct", ""), p.get("since"), p.get("until"))
        elif what == "stop":
            ok, msg = qr.stop_agreed(int(p.get("cp_id") or 0))
        elif what == "contact":
            ok, msg = qr.note_contact(customer_id, p.get("note", ""), p.get("next_on"))
        else:
            ok, msg = False, "Unknown action."
        _say(request, ok, msg)
        return redirect(f"/customers/{customer_id}/")
    d = qr.customer_page(customer_id)
    if not d:
        raise Http404
    from engines.product_catalog import brands
    return render(request, "selling/customer_page.html", {"page_id": "customers", **d, "brands": brands()})
