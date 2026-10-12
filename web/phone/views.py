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
            "in": may_receive(role)[0] and "purchase-orders" not in hidden_for(acting_email()), "out": "sales" in see and "sales" in do}


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


def _ctx(request, title: str, **more) -> dict:
    return {"title": title, "mine": getattr(request, "phone_mine", None) or _mine(request), **more}


@phone_page(None)
def home(request):
    from engines.phone_lookup import slips_today, waiting_orders
    mine = request.phone_mine
    return render(request, "phone/home.html", _ctx(request, "Slick's", waiting=len(waiting_orders()) if mine["in"] else 0,
                                                    slips=len(slips_today()) if mine["out"] else 0))


def manifest_ops(request):
    """The second app on the phone (owner, 2026-10-12: "Can we have seperate shortcut for on the phone? This will be used primarily for operations"):
    its own id, so Chrome installs it beside the whole-site app; it opens the phone pages and stays within them."""
    from django.contrib.staticfiles import finders
    path = finders.find("phone/manifest-ops.webmanifest")
    if not path:
        raise Http404
    return FileResponse(open(path, "rb"), content_type="application/manifest+json")


def manifest(request):
    """The web app manifest, so 'Add to Home screen' gives an icon that opens /phone/ full-screen."""
    from django.contrib.staticfiles import finders
    path = finders.find("phone/manifest.webmanifest")
    if not path:
        raise Http404
    return FileResponse(open(path, "rb"), content_type="application/manifest+json")


@phone_page("prices")
def prices(request):
    from engines.phone_lookup import prices as lookup
    q = request.GET.get("q", "").strip()
    rows = lookup(q) if q else []
    return render(request, "phone/prices.html", _ctx(request, "Price list", q=q, rows=rows, asked=bool(q)))


@phone_page("stock", write=True)
def stock(request):
    from engines import stock_rules as sr
    from engines.phone_lookup import stock_card
    if request.method == "POST":
        what = request.POST.get("action")
        iid = int(request.POST.get("item_id") or 0)
        if what == "barcode":
            ok, msg = sr.learn_barcode(request.POST.get("barcode"), iid)
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
    return render(request, "phone/stock.html", _ctx(request, "Stock", q=q, show=show, item=item, found=found, rows=rows, counts=counts, may_do=request.phone_mine["stock_do"]))


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
            return redirect("/phone/in/" + (f"?po={order['po_id']}" if not ok else ""))
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
                                                  codes_json=json.dumps(codes), doc=request.POST.get("doc", ""), dr=request.POST.get("dr", "")))


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
        cust, name = _customer(p)
        ok, msg, sale_id = sr.release(cust, name, _lines(p), "", si_hint=p.get("si_hint", ""))
        _say(request, ok, msg)
        if ok:
            return redirect("/phone/out/")
        start = json.dumps([{"id": int(ln["item_id"]), "qty": float(ln["qty"] or 0), "price": ""} for ln in _lines(p) if str(ln["item_id"]).isdigit()])
        return render(request, "phone/out.html", _ctx(request, "Stock out", customer=p.get("customer", ""), si_hint=p.get("si_hint", ""), scan_lines=json.loads(start),
                                                       names=[c["name"] for c in sr.customers()], slips=slips_today(), scan_mode="out"))
    return render(request, "phone/out.html", _ctx(request, "Stock out", customer="", si_hint="", scan_lines=[], names=[c["name"] for c in sr.customers()],
                                                   slips=slips_today(), scan_mode="out"))
