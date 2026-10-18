"""Sell (the revenue cycle, stage A; owner, 2026-10-11): the counter sale typed live, the day's sales in the notebook's order, goods released before
their invoice, one sale (void it, take an item back), customers and the sales invoice booklets. Rules: engines/sales_rules.py. These views draw the
forms and pass on what was typed; a person who may only see a page gets no saves (page_needed)."""
from __future__ import annotations

import json

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render

from masters.views import page_needed

PAY_ROWS = 3


def _say(request, ok: bool, msg: str) -> None:
    (messages.success if ok else messages.error)(request, msg)


def _can(request, action: str) -> bool:
    from engines.permissions import can
    return can(request.slicks_user["role"], action)


def _lines(p) -> list[dict]:
    """The scanned / chosen lines (selling/_scan_lines.html): ln_item_<n>, ln_qty_<n>, ln_price_<n>."""
    out = []
    for key in p:
        if key.startswith("ln_item_"):
            n = key.removeprefix("ln_item_")
            out.append({"item_id": p.get(key), "qty": p.get(f"ln_qty_{n}"), "price": p.get(f"ln_price_{n}", "")})
    return out


def _services(p) -> list[dict]:
    return [{"description": p.get(f"svc_desc_{k}", ""), "amount": p.get(f"svc_amt_{k}", "")} for k in range(4)]


def _payments(p) -> list[dict]:
    return [{"method": p.get(f"pay_method_{k}", ""), "amount": p.get(f"pay_amount_{k}", ""), "bank": p.get(f"pay_bank_{k}", ""), "check_no": p.get(f"pay_no_{k}", ""),
             "check_date": p.get(f"pay_date_{k}", ""), "reference": p.get(f"pay_ref_{k}", "")} for k in range(PAY_ROWS)]


def _customer(p) -> tuple[int | None, str]:
    """What was typed in Customer: a known customer's name (that customer), or anything else (a walk-in, with the name as a note)."""
    from engines.sales_rules import customers
    name = " ".join(p.get("customer", "").split())
    if not name or name.lower() in ("walk-in", "walk in", "walkin"):
        return None, ""
    hit = next((c for c in customers() if c["name"].lower() == name.lower()), None)
    return (hit["customer_id"], "") if hit else (None, name)


def _pay_ctx(p=None) -> list[dict]:
    rows = _payments(p) if p else [{"method": "CASH", "amount": "", "bank": "", "check_no": "", "check_date": "", "reference": ""}] + [dict.fromkeys(("method", "amount", "bank", "check_no", "check_date", "reference"), "")] * (PAY_ROWS - 1)
    return [{"k": k, **r} for k, r in enumerate(rows)]


def _scan_ctx(request, p, start: str) -> dict:
    from engines import sales_rules as sr
    return {"page_id": "sales", "customer": p.get("customer", "") if p else "", "si_hint": p.get("si_hint", "") if p else "",
            "names": [c["name"] for c in sr.customers()], "scan_lines": json.loads(start), "slips": sr.open_releases()}


@page_needed("sales")
def sales(request):
    from engines import sales_rules as sr
    from engines.clock import manila_today
    view = request.GET.get("view", "")
    if request.method == "POST":
        p = request.POST
        cust, name = _customer(p)
        if p.get("action") == "slip":                           # the Scan out page (owner, 2026-10-11): Done -- the stock is out, the invoice after close
            from engines.settings_registry import sales_scan_on
            if not sales_scan_on():
                _say(request, False, "Scanning is switched off: Settings > Sell > Sales start from a scan.")
                return redirect("/sales/")
            ok, msg, sale_id = sr.release(cust, name, _lines(p), p.get("note", ""), si_hint=p.get("si_hint", ""))
            _say(request, ok, msg)
            if ok:
                return redirect("/sales/?view=scan")
            start = json.dumps([{"id": int(ln["item_id"]), "qty": float(ln["qty"] or 0), "price": ""} for ln in _lines(p) if str(ln["item_id"]).isdigit()])
            return render(request, "selling/scan.html", _scan_ctx(request, p, start))
        if p.get("action") == "release":
            ok, msg, sale_id = sr.release(cust, name, _lines(p), p.get("note", ""))
            _say(request, ok, msg)
            if ok:
                return redirect("/sales/?view=released")
        else:
            ok, msg, sale_id = sr.save_sale(p.get("si_no"), p.get("sale_date"), cust, name, _lines(p), _services(p), _payments(p), p.get("below_reason", ""))
            _say(request, ok, msg)
            if ok and p.get("qt", "").isdigit():                # made from a quotation (stage C, 2026-10-17): it is sold now
                from engines.quote_rules import mark_sold
                mark_sold(int(p["qt"]), sale_id)
            if ok:
                return redirect("/sales/?view=new")             # the next invoice straight away: they are typed one after another after close
        start = json.dumps([{"id": int(ln["item_id"]), "qty": float(ln["qty"] or 0), "price": ln["price"]} for ln in _lines(p) if str(ln["item_id"]).isdigit()])
        ctx = _sale_ctx(request, p, start)
        if p.get("qt", "").isdigit():
            from engines.quote_rules import get_quote
            ctx["qt"] = get_quote(int(p["qt"]))
        return render(request, "selling/sale.html", ctx)
    if view == "day":
        from datetime import date, timedelta
        try:
            day = date.fromisoformat(request.GET.get("d", ""))
        except ValueError:
            day = manila_today()
        sheet = sr.day_sheet(day)
        return render(request, "selling/day_sheet.html", {"page_id": "sales", "d": sheet, "prev": day - timedelta(days=1), "next": day + timedelta(days=1) if day < manila_today() else None,
                                                          "is_today": day == manila_today(), "methods": sr.METHODS, "skipped": sr.skipped() if day == manila_today() else []})
    from engines.settings_registry import sales_scan_on
    if view == "scan":                                      # the day: scan what leaves, Done per customer (owner, 2026-10-11)
        if not sales_scan_on():                             # hidden until the scanning routine starts ("hide first the scan thing")
            return redirect("/sales/")
        return render(request, "selling/scan.html", _scan_ctx(request, None, "[]"))
    if view == "new":                                           # an invoice typed with no slip
        if request.GET.get("qt", "").isdigit():                 # Make it a sale, from a quotation (stage C, 2026-10-17)
            from engines.quote_rules import get_quote
            qt = get_quote(int(request.GET["qt"]))
            if qt and qt["state"] in ("WAITING", "LAPSED"):
                ctx = _sale_ctx(request, None, json.dumps([{"id": ln["item_id"], "qty": ln["qty"], "price": ln["price"]} for ln in qt["lines"] if ln["item_id"]]))
                ctx.update({"customer": qt["customer"], "qt": qt})
                return render(request, "selling/sale.html", ctx)
        return render(request, "selling/sale.html", _sale_ctx(request, None, "[]"))
    sr.flag_open_releases()                                     # the page: the day's slips, each becoming its invoice after close
    rows = sr.open_releases()
    if not rows and view != "released":                         # nothing scanned (or scanning off): recording the sales IS the page (owner, 2026-10-11)
        return render(request, "selling/sale.html", _sale_ctx(request, None, "[]"))
    nxt = sr.next_si_no()
    return render(request, "selling/released.html", {"page_id": "sales", "rows": rows, "next_si": f"{nxt:06d}" if nxt else ""})


def _sale_ctx(request, p, start: str) -> dict:
    from engines import sales_rules as sr
    from engines.clock import manila_today
    nxt = sr.next_si_no()
    return {"page_id": "sales", "si_no": (p.get("si_no") if p else (f"{nxt:06d}" if nxt else "")), "today": manila_today(), "sale_date": p.get("sale_date") if p else manila_today().isoformat(),
            "customer": p.get("customer", "") if p else "", "names": [c["name"] for c in sr.customers()], "scan_lines": json.loads(start), "pays": _pay_ctx(p), "methods": list(sr.METHODS.items()),
            "services": [{"k": k, **s} for k, s in enumerate(_services(p) if p else [{"description": "", "amount": ""}] * 4)], "below_reason": p.get("below_reason", "") if p else "",
            "no_booklet": nxt is None, "skipped": sr.skipped(), "open_releases": len(sr.open_releases()),
            "scan_on": __import__("engines.settings_registry", fromlist=["sales_scan_on"]).sales_scan_on()}


@page_needed("sales")
def sale(request, sale_id: int):
    from engines import sales_rules as sr
    from engines.clock import manila_today
    s = sr.get_sale(sale_id)
    if not s:
        raise Http404
    if request.method == "POST":
        p, what = request.POST, request.POST.get("action")
        if what == "void":
            ok, msg = sr.void_sale(sale_id, p.get("reason", ""))
        elif what == "return":
            ok, msg = sr.return_item(sale_id, int(p.get("line_id") or 0), p.get("qty"), p.get("reason", ""))
        elif what == "invoice":
            prices = {ln["line_id"]: p.get(f"price_{ln['line_id']}", "") for ln in s["lines"]}
            ok, msg = sr.invoice_release(sale_id, p.get("si_no"), p.get("sale_date"), prices, _services(p), _payments(p), p.get("below_reason", ""))
        elif what == "cancel_release":
            ok, msg = sr.cancel_release(sale_id, p.get("reason", ""))
        else:
            ok, msg = False, "Unknown action."
        _say(request, ok, msg)
        if ok and what == "cancel_release":
            return redirect("/sales/?view=released")
        return redirect(f"/sales/{sale_id}/")
    nxt = sr.next_si_no()
    offered = str(s.get("si_hint") or "") or (f"{nxt:06d}" if nxt else "")      # the number typed at the scan comes first (owner, 2026-10-11)
    return render(request, "selling/sale_page.html", {"page_id": "sales", "s": s, "pays": _pay_ctx(), "methods": list(sr.METHODS.items()), "today": manila_today(),
                                                      "si_no": offered, "services": [{"k": k, "description": "", "amount": ""} for k in range(4)],
                                                      "items": [ln for ln in s["lines"] if not ln["service"]]})


@page_needed("customers")
def customers(request):
    from engines import sales_rules as sr
    if request.method == "POST":
        p, what = request.POST, request.POST.get("action")
        if what == "save":
            cid = int(p["customer_id"]) if p.get("customer_id", "").isdigit() else None
            ok, msg, _ = sr.save_customer(cid, p.get("name", ""), p.get("type", "RETAIL"), p.get("terms", ""), p.get("contact_person", ""), p.get("contact_number", ""),
                                          p.get("is_affiliate") == "1")
        elif what == "credit":
            ok, msg = sr.set_credit_allowed(int(p.get("customer_id") or 0), p.get("on") == "1")
        else:
            ok, msg = False, "Unknown action."
        _say(request, ok, msg)
        return redirect("/customers/")
    q = request.GET.get("q", "")
    edit = sr.customer(int(request.GET["edit"])) if request.GET.get("edit", "").isdigit() else None
    from selling.quotes import _stage_c
    return render(request, "selling/customers.html", {"page_id": "customers", "rows": sr.customers(q), "q": q, "types": list(sr.CUSTOMER_TYPES.items()), "edit": edit, "stage_c": _stage_c(),
                                                      "may_credit": _can(request, "inventory.approve")})


@page_needed("si-booklets")
def si_booklets(request):
    from engines import sales_rules as sr
    if request.method == "POST":
        p, what = request.POST, request.POST.get("action")
        if what == "register":
            ok, msg = sr.register_booklet(p.get("start"), p.get("end"), p.get("holder", ""))
        elif what == "cancel_leaf":
            ok, msg = sr.cancel_leaf(p.get("si_no"), p.get("reason", ""))
        elif what == "use":
            ok, msg = sr.use_booklet(int(p.get("booklet_id") or 0))
        else:
            ok, msg = False, "Unknown action."
        _say(request, ok, msg)
        return redirect("/si-booklets/")
    rows = sr.booklets()
    last = max((b["end_no"] for b in rows), default=0)
    return render(request, "selling/si_booklets.html", {"page_id": "si-booklets", "rows": rows, "skipped": sr.skipped(), "suggest_start": last + 1 if last else "",
                                                        "suggest_end": last + 50 if last else ""})


# --- affiliates -----------------------------------------------------------------------------------------------------------------------------------------

@page_needed("affiliates")
def affiliates(request):
    from engines import affiliate_rules as ar
    from engines import sales_rules as sr
    if request.method == "POST" and request.POST.get("action") == "add":
        p = request.POST
        ok, msg, cid = sr.save_customer(None, p.get("name", ""), "OTHER", "", p.get("contact_person", ""), p.get("contact_number", ""), True)
        _say(request, ok, msg)
        return redirect(f"/affiliates/{cid}/" if ok else "/affiliates/")
    ar.flag_long_open()
    return render(request, "selling/affiliates.html", {"page_id": "affiliates", "rows": ar.balances(), "days": ar.open_days()})


@page_needed("affiliates")
def affiliate(request, customer_id: int):
    from engines import affiliate_rules as ar
    from engines import sales_rules as sr
    from engines.clock import manila_today
    who = sr.customer(customer_id)
    if not who or not who["is_affiliate"]:
        raise Http404
    if request.method == "POST":
        p, what = request.POST, request.POST.get("action")
        if what in ("lend", "borrow"):
            ok, msg, _ = ar.record_move(customer_id, what.upper(), _lines(p), p.get("move_date"), p.get("taken_by", ""), p.get("note", ""))
        elif what == "settle":
            picks = [{"line_id": k.removeprefix("pick_"), "qty": p.get("qty_" + k.removeprefix("pick_"))} for k in p if k.startswith("pick_")]
            ok, msg = ar.settle(customer_id, picks, p.get("how", ""), p.get("ref", ""), p.get("si_no"), _payments(p))
        else:
            ok, msg = False, "Unknown action."
        _say(request, ok, msg)
        return redirect(f"/affiliates/{customer_id}/" + (f"?do={what}" if not ok and what in ("lend", "borrow") else ""))
    lines = ar.open_lines(customer_id)
    nxt = sr.next_si_no()
    do = request.GET.get("do", "")
    return render(request, "selling/affiliate.html", {"page_id": "affiliates", "who": who, "lines": lines, "lent": [x for x in lines if x["direction"] == "LEND"],
                                                      "borrowed": [x for x in lines if x["direction"] == "BORROW"], "history": ar.history(customer_id), "today": manila_today(),
                                                      "do": do if do in ("lend", "borrow") else "", "scan_mode": do if do in ("lend", "borrow") else "", "scan_lines": [],
                                                      "pays": _pay_ctx(), "methods": [m for m in sr.METHODS.items() if m[0] != "CHARGE"], "si_no": f"{nxt:06d}" if nxt else "",
                                                      "bal": next((b for b in ar.balances() if b["customer_id"] == int(customer_id)), None), "days": ar.open_days()})
