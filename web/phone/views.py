"""The phone pages (owner, 2026-10-12: "We need pricelist, inventory. Stock in, stock out. Those items need mobility without using a laptop"):
Price list, Stock, Stock in and Stock out on a phone, added to its home screen. Part of the same site: the same sign-in, and every save goes
through the same engines as the laptop page it mirrors. Each page shows to a person allowed the matching laptop page (the per-person Hidden /
See / See and do), so the staff phone's Scanner account never sees prices. Off until Settings > Phone switches it on."""
from __future__ import annotations

import json
from functools import wraps

from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render

from masters.views import page_needed  # noqa: F401  (the laptop pages' guard; the phone's own is below, same page ids)


def _say(request, ok: bool, msg: str) -> None:
    (messages.success if ok else messages.error)(request, msg)


def _mine(request) -> dict:
    """Which of the four the person may open, and which they may change on (the laptop page's own rules)."""
    from core.menu import doable_pages, pages_for
    from engines.acting_user import acting_email
    from engines.permissions import hidden_for, may_receive
    role = request.slicks_user["role"]
    see, do = pages_for(role), doable_pages(role)
    # Stock in goes by the receiving switch alone (the Scanner may receive without seeing Purchase orders), unless that page is hidden from the person
    return {"prices": "price-lists" in see, "stock": "stock" in see, "stock_do": "stock" in do,
            "in": may_receive(role)[0] and "purchase-orders" not in hidden_for(acting_email()), "out": "sales" in see and "sales" in do,
            "sale": "sales" in see and "sales" in do and role != "scanner",       # the family records the sale; the staff phone only moves stock (2026-10-12)
            "sale_do": "sales" in do and role != "scanner",
            "count": "stock-counts" in see, "count_do": "stock-counts" in do}


def phone_page(key: str | None, write: bool = False):
    """Open only while the phone pages are switched on, to a person allowed that laptop page; a save needs 'See and do' on it."""
    def deco(view):
        @wraps(view)
        def wrapper(request, *a, **k):
            from engines.settings_registry import phone_on
            if not phone_on():
                return redirect("/")
            mine = _mine(request)
            if key and not mine.get(key):
                return render(request, "phone/not_allowed.html", {"mine": mine}, status=403)
            if write and request.method == "POST" and not mine.get(key + "_do", mine.get(key)):
                _say(request, False, "This page is view only for you.")
                return redirect(request.path)
            request.phone_mine = mine
            return view(request, *a, **k)
        return wrapper
    return deco


def _once(request):
    """The one-time code of a phone form (2026-10-17): None to go ahead (the token is claimed), or the page to answer with when it was already saved
    or is still being saved -- the signal dropped after the server got it and the person pressed again."""
    from engines import form_once
    from engines.acting_user import acting_email
    token = request.POST.get("once", "")
    state, row = form_once.claim(token, acting_email())
    request._once = token if state == "new" else ""
    if state == "done":
        at = row["at"][11:16]
        try:
            from datetime import datetime
            at = datetime.strptime(row["at"][:16], "%Y-%m-%d %H:%M").strftime("%I:%M %p").lstrip("0")
        except ValueError:
            pass
        messages.success(request, f"Already saved at {at}" + (f": {row['note']}" if row["note"] else "") + ". Nothing was added twice.")
        return redirect(row["url"] or request.path)
    if state == "busy":
        messages.warning(request, "That is still being saved from the first press. Wait a moment, then look: nothing was added twice.")
        return redirect(request.path)
    return None


def _once_done(request, ok: bool, msg: str, url: str):
    from engines import form_once
    form_once.finish(getattr(request, "_once", ""), ok, msg, url)
    return redirect(url) if url else None                      # no url: only the claim is dropped (the page answers with the form again)


def _ctx(request, title: str, **more) -> dict:
    return {"title": title, "mine": getattr(request, "phone_mine", None) or _mine(request), **more}


@phone_page(None)
def home(request):
    from engines.phone_lookup import slips_today, waiting_orders
    mine = request.phone_mine
    return render(request, "phone/home.html", _ctx(request, "Slick's", waiting=len(waiting_orders()) if mine["in"] else 0,
                                                    slips=len(slips_today()) if mine["out"] else 0,
                                                    to_invoice=len(__import__("engines.sales_rules", fromlist=["open_releases"]).open_releases()) if mine["sale"] else 0,
                                                    counting=_open_counts() if mine["count"] else 0))


def _open_counts() -> int:
    from engines.common import get_conn
    with get_conn() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM stock_counts WHERE status = 'COUNTING'").fetchone()[0])


SHORTCUTS = [("in", "Stock in", "/phone/in/", "Goods arrived against an order"),
             ("out", "Stock out", "/phone/out/", "The slip: goods out now"),
             ("count", "Stock count", "/phone/count/", "Count the racks"),
             ("sale", "Record a sale", "/phone/sale/", "From the cash receipt")]


# owner, 2026-10-17: "i want stock one clickaway", then "remove daily payables and record an invoice": the ERP icon = POS + Slicks Inventory
ERP_SHORTCUTS = [("inventory", "Slicks Inventory", "/phone/inventory/", "Stock by rim, one tap away", "stock", False)]


def _with_shortcuts(request, path: str, keep_old: bool):
    """The manifest file plus the person's own shortcuts (owner, 2026-10-17): only the phone pages they may use; read again by the phone when the
    app is opened, so a changed access shows there too."""
    import json
    from django.http import HttpResponse
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    known = bool(getattr(request, "slicks_user", None))
    # Android refreshes an installed app's file in the background, often without the sign-in (owner, 2026-10-17: the ERP icon lost Daily payables and
    # Record an invoice): nobody known = the full set; a page a person may not use answers "not for you" when opened. Known = that person's own.
    mine = _mine(request) if known else {"in": True, "out": True, "count": True, "sale": True}
    may = {"in": mine.get("in"), "out": mine.get("out"), "count": mine.get("count"), "sale": mine.get("sale")}
    # the pictures go inside the file: Android's installed app has its icons fetched by Google, which Cloudflare stops (owner, 2026-10-17: "why no logo")
    import os
    with open(os.path.join(os.path.dirname(__file__), "shortcut_icons.json"), encoding="utf-8") as f:
        pics = json.load(f)
    jobs = [{"name": name, "short_name": name, "description": why, "url": url, "icons": [{"src": pics[key], "sizes": "96x96", "type": "image/png"}]}
            for key, name, url, why in SHORTCUTS if may.get(key)]
    if keep_old:                                                 # the Slick's ERP icon: POS first, then the laptop pages wanted on the go (owner, 2026-10-17)
        from core.menu import doable_pages, pages_for
        role = (getattr(request, "slicks_user", None) or {}).get("role")
        see, do = (pages_for(role), doable_pages(role)) if role else ({"checks", "order-statements", "stock"}, {"checks", "order-statements", "stock"})
        erp = [{"name": name, "short_name": name, "description": why, "url": url, "icons": [{"src": pics[key], "sizes": "96x96", "type": "image/png"}]}
               for key, name, url, why, page, needs_do in ERP_SHORTCUTS if page in (do if needs_do else see)]
        data["shortcuts"] = data.get("shortcuts", []) + erp + (jobs if role == "scanner" else [])   # the staff phone keeps its jobs after them
    else:
        data["shortcuts"] = jobs
    resp = HttpResponse(json.dumps(data, ensure_ascii=False), content_type="application/manifest+json")
    resp["Cache-Control"] = "private, no-cache"
    return resp


def manifest_ops(request):
    """The second app on the phone (owner, 2026-10-12: "Can we have seperate shortcut for on the phone? This will be used primarily for operations"):
    its own id, so Chrome installs it beside the whole-site app; it opens the phone pages and stays within them."""
    from django.contrib.staticfiles import finders
    path = finders.find("phone/manifest-ops.webmanifest")
    if not path:
        raise Http404
    return _with_shortcuts(request, path, keep_old=False)


def manifest(request):
    """The web app manifest, so 'Add to Home screen' gives an icon that opens /phone/ full-screen."""
    from django.contrib.staticfiles import finders
    path = finders.find("phone/manifest.webmanifest")
    if not path:
        raise Http404
    return _with_shortcuts(request, path, keep_old=True)          # the whole-site app: "Slick's POS" first, then the jobs


@phone_page("prices")
def prices(request):
    from engines.phone_lookup import prices as lookup
    q = request.GET.get("q", "").strip()
    rows = lookup(q) if q else []
    return render(request, "phone/prices.html", _ctx(request, "Price list", q=q, rows=rows, asked=bool(q)))


@phone_page("stock")
def look(request):
    """Scan a tire: its stock and, to a person who sees prices, the price list (owner, 2026-10-17; mock-up ak_phone_features #6). ?code= answers
    as JSON for the page's own script."""
    from django.http import JsonResponse
    from engines import stock_rules as sr
    from engines.phone_lookup import price_of_item, stock_card
    code = request.GET.get("code", "").strip()
    if not code:
        return render(request, "phone/look.html", _ctx(request, "Scan a tire"))
    hit = sr.find_by_code(code)
    if not hit:
        return JsonResponse({"ok": False, "code": code, "msg": f"No item has the code {code}. Teach it on Stock: find the item, then Teach this barcode."})
    card = stock_card(hit["id"]) or {}
    out = {"ok": True, "id": hit["id"], "name": card.get("name") or hit["name"], "code": card.get("code") or hit["code"], "on_hand": card.get("on_hand"),
           "dot": card.get("dot") or "", "old": bool(card.get("old")), "low_at": card.get("reorder_level"), "low": bool(card.get("low"))}
    if request.phone_mine["prices"]:                           # never on the staff phone (owner, 2026-10-12)
        out["prices"] = [{**p, "net": round(p["net"], 2)} for p in price_of_item(hit["id"])]
    return JsonResponse(out)


@phone_page("stock", write=True)
def stock(request):
    from engines import stock_rules as sr
    from engines.phone_lookup import stock_card
    if request.method == "POST":
        what = request.POST.get("action")
        iid = int(request.POST.get("item_id") or 0)
        if what == "barcode":
            ok, msg = sr.learn_barcode(request.POST.get("barcode"), iid)
        elif what == "take_in":                                  # a trade-in or a surplus lot (owner, 2026-10-12)
            again = _once(request)
            if again:
                return again
            ok, msg = sr.take_in(iid, request.POST.get("qty"), request.POST.get("cost"), request.POST.get("note", ""))
            _say(request, ok, msg)
            return _once_done(request, ok, msg, f"/phone/stock/?id={iid}")
        elif what == "level":
            ok, msg = sr.set_reorder_level(iid, request.POST.get("level"))
        else:
            ok, msg = False, "Unknown action."
        _say(request, ok, msg)
        return redirect(f"/phone/stock/?id={iid}")
    q, show = request.GET.get("q", "").strip(), request.GET.get("show", "")
    item, found, rows = None, False, []
    if request.GET.get("id", "").isdigit():
        item = stock_card(int(request.GET["id"]))
    elif q:
        hit = sr.find_by_code(q)
        if hit:
            item, found = stock_card(hit["id"]), True
        else:
            rows = sr.stock_list(q)[:40]
    elif show in ("low", "negative"):
        rows = sr.stock_list("", show)[:80]
    counts = sr.summary()
    return render(request, "phone/stock.html", _ctx(request, "Stock", q=q, show=show, item=item, found=found, rows=rows, counts=counts, may_do=request.phone_mine["stock_do"],
                                                     surplus=sr.surplus_items() if request.phone_mine["stock_do"] else [], take_in=request.GET.get("take") == "1"))


@phone_page("in", write=True)
def stock_in(request):
    """A delivery against an order, the same step as Receive on the laptop (buying.orders._arrive): the goods are received and the DR / SI is the
    payable. The phone posts one card per line; a line with a quantity is a ticked line."""
    from buying.orders import _arrive
    from engines.clock import manila_today
    from engines.phone_lookup import waiting_orders
    from engines.po_rules import many_drs, receivable_lines
    orders = waiting_orders()
    picked = request.POST.get("po") if request.method == "POST" else request.GET.get("po", "")
    if not picked and len(orders) == 1:
        picked = str(orders[0]["po_id"])
    order = next((o for o in orders if str(o["po_id"]) == str(picked)), None)
    ask = None
    if request.method == "POST" and not order:
        _say(request, False, "That order is not waiting for goods any more.")
        return redirect("/phone/in/")
    if request.method == "POST" and order:
        again = _once(request)
        if again:
            return again
        p = request.POST.copy()
        coming = [ln for ln in receivable_lines(order["creditor_id"]).to_dict("records") if int(ln["po_id"]) == order["po_id"]]
        for ln in coming:                                        # a quantity typed = that line ticked (the laptop has a checkbox)
            lid = int(ln["line_id"])
            try:
                got = float(str(p.get(f"qty_{lid}", "")).replace(",", "") or 0)
            except ValueError:
                got = 0.0
            p[f"pick_{lid}"] = "1" if got > 0 else ""
        p["s"] = str(order["creditor_id"])
        request.POST = p
        ok, msg, ask = _arrive(request, order["creditor_id"], coming, True, True)
        if ask is None:
            _say(request, ok, msg)
            return _once_done(request, ok, msg, "/phone/in/" + (f"?po={order['po_id']}" if not ok else ""))
        _once_done(request, False, "", "")                       # a question first (more than ordered / part not come): the answer is a new send
    lines = []
    if order:
        lines = [ln for ln in receivable_lines(order["creditor_id"]).to_dict("records") if int(ln["po_id"]) == order["po_id"]]
        for ln in lines:
            ln["remaining"] = float(ln["remaining"])
            ln["qty_typed"] = request.POST.get(f"qty_{int(ln['line_id'])}", f"{ln['remaining']:g}") if request.method == "POST" else f"{ln['remaining']:g}"
            ln["dot_typed"] = request.POST.get(f"dot_{int(ln['line_id'])}", "") if request.method == "POST" else ""
    codes = {}
    if lines:
        from engines.common import get_conn
        with get_conn() as conn:
            for ln in lines:
                row = conn.execute("SELECT i.id, UPPER(i.code) FROM purchase_order_lines l JOIN items i ON i.id = l.item_id WHERE l.line_id = ?", (int(ln["line_id"]),)).fetchone()
                if row:
                    cs = [row[1]] + [c[0].upper() for c in conn.execute("SELECT barcode FROM item_barcodes WHERE item_id = ?", (row[0],))]
                    codes[int(ln["line_id"])] = cs
    return render(request, "phone/in.html", _ctx(request, "Stock in", orders=orders, order=order, lines=lines, ask=ask, today=manila_today(), many=many_drs(),
                                                  codes_json=codes, doc=request.POST.get("doc", ""), dr=request.POST.get("dr", "")))


@phone_page("out", write=True)
def stock_out(request):
    """The slip (engines/sales_rules.release): the stock is out the moment Done is pressed; the invoice is written by hand and recorded after close.
    Reached from the phone whether or not Settings > Sell says sales start from a scan (owner, 2026-10-12: that switch is about the laptop and the
    Review flag; stock out on the phone is a must)."""
    from engines import sales_rules as sr
    from engines.phone_lookup import slips_today
    from selling.sales import _customer, _lines
    if request.method == "POST":
        p = request.POST
        if p.get("action") == "undo":
            ok, msg = sr.cancel_release(int(p.get("sale_id") or 0), "undone on the phone: the goods did not go out")
            _say(request, ok, msg)
            return redirect("/phone/out/")
        again = _once(request)
        if again:
            return again
        cust, name = _customer(p)
        ok, msg, sale_id = sr.release(cust, name, _lines(p), "", si_hint=p.get("si_hint", ""))
        _say(request, ok, msg)
        if ok:
            return _once_done(request, True, msg, "/phone/out/")
        _once_done(request, False, "", "")
        start = json.dumps([{"id": int(ln["item_id"]), "qty": float(ln["qty"] or 0), "price": ""} for ln in _lines(p) if str(ln["item_id"]).isdigit()])
        return render(request, "phone/out.html", _ctx(request, "Stock out", customer=p.get("customer", ""), si_hint=p.get("si_hint", ""), scan_lines=json.loads(start),
                                                       names=[c["name"] for c in sr.customers()], slips=slips_today(), scan_mode="out"))
    return render(request, "phone/out.html", _ctx(request, "Stock out", customer="", si_hint="", scan_lines=[], names=[c["name"] for c in sr.customers()],
                                                   slips=slips_today(), scan_mode="out"))


@phone_page("sale", write=True)
def sale(request):
    """Record a sale from the cash receipt (owner, 2026-10-12: "The input amount is cash receipts just workback the profit"): the invoice is written
    by hand; this records it whenever -- at the counter, idle, or at night. The receipt is spread over the scanned lines by cost (engines/pos_sale);
    a slip of the day can be picked so goods already out are invoiced, not taken twice. One payment for the whole receipt."""
    from engines import pos_sale
    from engines import sales_rules as sr
    from engines.clock import manila_today
    from selling.sales import _customer, _lines
    today = manila_today()
    slips = sr.open_releases()
    if request.method == "POST":
        again = _once(request)
        if again:
            return again
        p = request.POST
        cust, name = _customer(p)
        services = [{"description": p.get(f"svc_desc_{k}", ""), "amount": p.get(f"svc_amt_{k}", "")} for k in range(3)]
        payment = {"method": p.get("method", "CASH"), "reference": p.get("reference", ""), "bank": p.get("bank", ""), "check_no": p.get("check_no", ""), "check_date": p.get("check_date", "")}
        rel = int(p.get("release_id")) if str(p.get("release_id", "")).isdigit() else None
        surplus = [{"item_id": p.get(f"sp_item_{k}", ""), "qty": p.get(f"sp_qty_{k}", ""), "cost": p.get(f"sp_cost_{k}", "")} for k in range(2)]
        ok, msg, sale_id = pos_sale.record(p.get("si_no"), p.get("sale_date") or today.isoformat(), cust, name, _lines(p), p.get("receipt"), services, payment, rel,
                                           p.get("below_reason", ""), surplus)
        _say(request, ok, msg)
        if ok:
            return _once_done(request, True, msg, "/phone/sale/")
        _once_done(request, False, "", "")
        start = json.dumps([{"id": int(ln["item_id"]), "qty": float(ln["qty"] or 0), "price": ""} for ln in _lines(p) if str(ln["item_id"]).isdigit()])
        ctx = dict(si_no=p.get("si_no", ""), sale_date=p.get("sale_date") or today.isoformat(), customer=p.get("customer", ""), receipt=p.get("receipt", ""),
                   services=[{"k": k, **s} for k, s in enumerate(services)], method=p.get("method", "CASH"), reference=p.get("reference", ""), release_id=rel or "",
                   surplus_rows=[{"k": k, **s} for k, s in enumerate(surplus)])
    else:
        rel = int(request.GET.get("slip")) if request.GET.get("slip", "").isdigit() else None
        start, pre = "[]", {}
        if not rel and request.GET.get("item", "").isdigit():  # from a quick quote (owner, 2026-10-17): that tire, those pieces, the quoted total
            try:
                q_n = float(request.GET.get("qty") or 1)
                q_r = float(request.GET.get("receipt") or 0)
            except ValueError:
                q_n, q_r = 1.0, 0.0
            start = json.dumps([{"id": int(request.GET["item"]), "qty": q_n, "price": ""}])
            pre = {"receipt": f"{q_r:.2f}" if q_r > 0 else ""}
        if rel:                                                  # the slip's goods, already out: shown, not scanned again
            head = sr.get_sale(rel)
            if head and head["status"] == "RELEASED":
                start = json.dumps([{"id": int(ln["item_id"]), "qty": float(ln["qty"]), "price": ""} for ln in head["lines"] if ln["item_id"]])
                pre = {"customer": head["customer"] if head["customer"] != "Walk-in" else "", "si_no": f"{int(head['si_hint']):06d}" if str(head.get("si_hint") or "").isdigit() else ""}
            else:
                rel = None
        nxt = sr.next_si_no()
        ctx = dict(si_no=pre.get("si_no") or (f"{nxt:06d}" if nxt else ""), sale_date=today.isoformat(), customer=pre.get("customer", ""), receipt=pre.get("receipt", ""),
                   services=[{"k": k, "description": "", "amount": ""} for k in range(3)], method="CASH", reference="", release_id=rel or "",
                   surplus_rows=[{"k": k, "item_id": "", "qty": "", "cost": ""} for k in range(2)])
    from engines.stock_rules import surplus_items
    return render(request, "phone/sale.html", _ctx(request, "Record a sale", scan_lines=json.loads(start), scan_mode="out", names=[c["name"] for c in sr.customers()],
                                                    slips=slips, methods=list(sr.METHODS.items()), today=today, no_booklet=sr.next_si_no() is None,
                                                    surplus_items=surplus_items() if not rel else [], **ctx))


@phone_page("sale")
def day(request):
    """Sales today, phone-sized: what was invoiced, and the slips still waiting for their invoice."""
    from datetime import date, timedelta
    from engines import sales_rules as sr
    from engines.clock import manila_today
    try:
        d = date.fromisoformat(request.GET.get("d", ""))
    except ValueError:
        d = manila_today()
    sheet = sr.day_sheet(d)
    by_sale: dict = {}
    for r in sheet["rows"]:
        s = by_sale.setdefault(r["sale_id"], {"sale_id": r["sale_id"], "ref": r["ref"], "customer": r["customer"], "amount": 0.0, "profit": 0.0, "items": []})
        s["amount"] = round(s["amount"] + float(r["amount"]), 2)
        s["profit"] = round(s["profit"] + float(r["profit"] or 0), 2)
        s["items"].append(f"{r['qty']:g} × {r['item']}")
    return render(request, "phone/day.html", _ctx(request, "Sales today", d=d, sheet=sheet, sales=list(by_sale.values()), prev=d - timedelta(days=1),
                                                   nxt=d + timedelta(days=1) if d < manila_today() else None, slips=[s for s in sr.open_releases() if str(s["day"])[:10] == d.isoformat()]))


@phone_page("count", write=True)
def count(request):
    """Stock counts on the phone (owner, 2026-10-12): the open counts, and Start a count by type / brand. The same counts as the laptop page."""
    from engines import count_rules as cr
    from engines.common import get_conn
    from engines.stock_rules import CATEGORY_LABEL
    if request.method == "POST" and request.POST.get("action") == "start":
        p = request.POST
        ok, msg, cid = cr.start_count(p.get("category", ""), p.get("brand", ""), p.get("uncounted") == "1", "", p.get("counted_by", ""))
        _say(request, ok, msg)
        return redirect(f"/phone/count/{cid}/" if ok else "/phone/count/")
    if request.method == "POST" and request.POST.get("action") == "start_opening":
        ok, msg, cid = cr.start_opening(request.POST.get("counted_by", ""))
        _say(request, ok, msg)
        return redirect(f"/phone/count/{cid}/" if ok else "/phone/count/")
    with get_conn() as conn:
        brands = [r[0] for r in conn.execute("SELECT DISTINCT brand FROM items WHERE is_active = 1 AND brand != '' ORDER BY brand COLLATE NOCASE")]
    rows = [c for c in cr.listing() if c["status"] in ("COUNTING", "WAITING")]
    return render(request, "phone/count.html", _ctx(request, "Stock count", rows=rows, brands=brands, categories=list(CATEGORY_LABEL.items()),
                                                      me_name=request.slicks_user.get("name", "")))


@phone_page("count", write=True)
def count_sheet(request, count_id: int):
    """One count on the phone: scan each piece (every scan adds 1 to its item), or type; Save keeps the figures (the laptop sheet shows the same);
    Finish hands the differences to the System admin on the laptop. Autosave posts here too (action=save, from the page's own script)."""
    from engines import count_rules as cr
    c = cr.get_count(count_id)
    if not c:
        raise Http404
    if request.method == "POST":
        p, what = request.POST, request.POST.get("action", "")
        if what in ("finish", "add") and p.get("quiet") != "1":   # adding and finishing happen once; a Save of the same figures again is harmless
            again = _once(request)
            if again:
                return again
        if what in ("save", "finish"):
            typed, bad = {}, None
            for row in cr.sheet_rows(count_id):
                raw = str(p.get(f"c_{row['line_id']}", "")).replace(",", "").strip()
                if raw == "":
                    n = None
                else:
                    try:
                        n = float(raw)
                    except ValueError:
                        bad = f"{row['name']}: '{raw}' is not a quantity."
                        break
                typed[row["line_id"]] = (n, p.get(f"n_{row['line_id']}", ""))
                if f"k_{row['line_id']}" in p:                   # an opening sheet, to a person who sees costs: the cost typed from the notebook
                    k = str(p.get(f"k_{row['line_id']}", "")).replace(",", "").strip()
                    try:
                        typed[row["line_id"]] += (float(k) if k else None,)
                    except ValueError:
                        bad = f"{row['name']}: '{k}' is not a cost."
                        break
            ok, msg = (False, bad) if bad else cr.save_counted(count_id, typed, p.get("counted_by") if p.get("counted_by") is not None else None)
            if ok and what == "finish":
                ok, msg = cr.finish(count_id)
        elif what == "cancel":
            ok, msg = cr.cancel(count_id)
        elif what == "add":                                      # an opening count: an item the system has, found at the rack
            ok, msg = cr.add_line(count_id, int(p.get("item_id") or 0), p.get("qty", ""), p.get("cost", "") if request.phone_mine["prices"] else None, p.get("dot", ""))
        else:
            ok, msg = False, "Unknown action."
        if p.get("quiet") == "1":                                # the autosave: a plain answer, no page
            from django.http import JsonResponse
            return JsonResponse({"ok": ok, "msg": msg})
        _say(request, ok, msg)
        url = "/phone/count/" if (what in ("cancel", "finish") and ok) else f"/phone/count/{count_id}/"
        if what in ("finish", "add"):
            return _once_done(request, ok, msg, url)
        return redirect(url)
    rows = cr.sheet_rows(count_id)
    done = sum(1 for r in rows if r["counted"] is not None)
    if c["opening"]:
        return render(request, "phone/count_open.html", _ctx(request, c["count_no"], c=c, rows=rows, counting=c["status"] == "COUNTING",
                                                              show_cost=request.phone_mine["prices"], may_add=_may_add(request),
                                                              pieces=sum(float(r["counted"] or 0) for r in rows),
                                                              lines_json={r["item_id"]: r["line_id"] for r in rows}))
    return render(request, "phone/count_sheet.html", _ctx(request, c["count_no"], c=c, rows=rows, done=done, counting=c["status"] == "COUNTING",
                                                           codes_json={r["line_id"]: [x.upper() for x in r["codes"]] for r in rows}))


@phone_page("count")
def count_print(request, count_id: int):
    """The paper sheet from the phone (owner, 2026-10-17): the same one the laptop prints."""
    from engines import count_rules as cr
    c = cr.get_count(count_id)
    if not c or c["status"] != "COUNTING":
        raise Http404
    return render(request, "selling/count_sheet.html", {"c": c, "sheet": cr.sheet_rows(count_id)})


def _may_add(request) -> bool:
    """New items are added at the rack by the family only, never from the staff phone (owner, 2026-10-13)."""
    return request.slicks_user["role"] != "scanner" and request.phone_mine["count_do"]


@phone_page("count", write=True)
def count_add(request, count_id: int):
    """An opening count: the tire in front of you is not in the system. Its fields, how many, and the cost from the notebook: made and counted in one step."""
    import re

    from engines import count_rules as cr
    from engines.product_catalog import CATEGORY_FIELDS, CODE, TIRE_SIZE, brands
    c = cr.get_count(count_id)
    if not c or not c["opening"]:
        raise Http404
    if not _may_add(request):
        _say(request, False, "New items are added by the family. Tell them which tire is not in the system.")
        return redirect(f"/phone/count/{count_id}/")
    typed = {}
    if request.method == "POST":
        again = _once(request)
        if again:
            return again
        p = request.POST
        cat = next((label for label, code in CODE.items() if code == p.get("category")), "")
        values = {f["key"]: p.get(f"f_{CODE[cat]}_{f['key']}", "") for f in CATEGORY_FIELDS.get(cat, [])}
        ok, msg = cr.add_new(count_id, cat, p.get("brand", ""), values, p.get("qty", ""), p.get("cost", "") if request.phone_mine["prices"] else None, p.get("dot", ""))
        _say(request, ok, msg)
        if ok:
            return _once_done(request, True, msg, f"/phone/count/{count_id}/")
        _once_done(request, False, "", "")
        typed = p
    q = " ".join(request.GET.get("q", "").split())
    size = TIRE_SIZE.search(q.upper().replace(" ", ""))
    loose = re.search(r"\b(\d{3})[ /]?(\d{2})[ /rR]*(\d{2})\b", q)            # typed the quick way: "205 55 16" is 205/55R16
    word = next((w for w in q.split() if re.fullmatch(r"[A-Za-z][A-Za-z.&'-]+", w)), "")
    known = brands()
    start = {"brand": next((b for b in known if word and b.lower().startswith(word.lower())), word.title()), "size": size.group(0) if size else ("{}/{}R{}".format(*loose.groups()) if loose else "")}
    groups = [{"code": CODE[label], "label": "Tire" if label == "Tires" else label,
               "fields": [dict(f, name=f"f_{CODE[label]}_{f['key']}", value=typed.get(f"f_{CODE[label]}_{f['key']}", start["size"] if (label == "Tires" and f["key"] == "size") else ""))
                          for f in flds]} for label, flds in CATEGORY_FIELDS.items()]
    return render(request, "phone/count_add.html", _ctx(request, "Add an item", c=c, groups=groups, brands=known, show_cost=request.phone_mine["prices"],
                                                         category=typed.get("category", "tire"), brand=typed.get("brand", start["brand"]),
                                                         qty=typed.get("qty", ""), cost=typed.get("cost", ""), dot=typed.get("dot", "")))


# ================= Slicks Inventory and the quick quotation (owner, 2026-10-17; mock-ups an_slicks_inventory, ao_quick_quotation; answers all A) ====

@phone_page("stock")
def inventory(request):
    """Tires by rim like the stock notebook: size, brand, how many. Its own app icon ("Slicks Inventory"), opening on the last rim used."""
    from engines import rim_stock
    q = request.GET.get("q", "").strip()
    tabs = rim_stock.tabs()
    rim = request.GET.get("rim") or request.COOKIES.get("slk_rim") or "16"
    if rim not in tabs:
        rim = "16"
    groups = rim_stock.find(q) if q else rim_stock.rim_tab(rim)
    resp = render(request, "phone/inventory.html", _ctx(request, "Slicks Inventory", tabs=tabs, rim=rim, q=q, groups=groups,
                                                         pieces=sum(g["total"] for g in groups)))
    if not q:
        resp.set_cookie("slk_rim", rim, max_age=31536000, samesite="Lax")
    return resp


@phone_page("prices")
def quote(request):
    """A quick quote for a walk-in customer canvassing prices: one size, what is on hand at cost (FIFO, the next sale's), plus the markup the person
    picks (+100, +200 or typed; default at cost), rounded up to the next ₱50. Nothing is saved. Never on the staff phone (it shows costs)."""
    from engines import rim_stock
    from engines.permissions import can
    from engines.settings_registry import int_value
    admin = can(request.slicks_user["role"], "settings.edit")
    if request.method == "POST" and admin:
        ok, msg = rim_stock.save_quote_footer(request.POST.get("footer", ""))
        _say(request, ok, msg)
        return redirect(request.get_full_path())
    size = request.GET.get("size", "").strip()
    lines = rim_stock.quote_lines(size) if size else []
    return render(request, "phone/quote.html", _ctx(request, f"Quote · {size}" if size else "Quote", size=size, rim=rim_stock.rim_of(size),
                                                     lines_json=json.dumps(lines), has_lines=bool(lines), step=int_value("quote_round_to"),
                                                     footer=rim_stock.quote_footer(), admin=admin, may_sell=request.phone_mine.get("sale_do"),
                                                     sizes=rim_stock.find(request.GET.get("q", "")) if not size and request.GET.get("q") else []))


def manifest_inv(request):
    """The third app (owner, 2026-10-17: "Slicks Inventory ... one click away"): its own id and icon, opening the stock by rim."""
    from django.contrib.staticfiles import finders
    from django.http import HttpResponse
    path = finders.find("phone/manifest-inv.webmanifest")
    if not path:
        raise Http404
    with open(path, encoding="utf-8") as f:
        body = f.read()
    resp = HttpResponse(body, content_type="application/manifest+json")
    resp["Cache-Control"] = "private, no-cache"
    return resp

