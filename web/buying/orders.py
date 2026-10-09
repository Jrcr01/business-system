"""Stage D2b (Purchase orders, docs/mockups/d2_buying.html screens 4-8, approved 2026-10-03): the orders and where each stands, one order with its trail to the bank,
a new order saved as it is built (with the payables bars and the daily-limit warning), Receive goods, and Match invoice into a payables batch.
The rules are in engines/po_rules.py (and disbursement_trail_service, cash_insights_service); these views draw the forms and pass on what was typed."""
from __future__ import annotations

from datetime import date, timedelta

from django.contrib import messages
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render

from buying.views import _date, _suppliers
from masters.views import page_needed

PO_STATUS = {"DRAFT": "Draft", "ORDERED": "Ordered", "PARTIAL": "Partly received", "RECEIVED": "Received", "CANCELLED": "Cancelled"}
SCOPES = [("open", "Open (draft, ordered, partly received)"), ("DRAFT", "Draft"), ("ORDERED", "Ordered"), ("PARTIAL", "Partly received"), ("RECEIVED", "Received"),
          ("CANCELLED", "Cancelled"), ("all", "All")]


def _can(request, action: str, created_by: str | None = None) -> bool:
    from engines.permissions import can
    u = request.slicks_user
    return can(u["role"], action, created_by=created_by, me=u["email"])


def _head(request) -> dict:
    """What the buttons above every purchase-order screen need: which of New order, Receive goods and Match invoice this person may use."""
    return {"may_create": _can(request, "po.create", request.slicks_user["email"]), "may_receive": _can(request, "po.receive_goods"), "may_match": _can(request, "po.receive")}


def _say(request, ok: bool, msg: str) -> None:
    (messages.success if ok else messages.error)(request, msg)


def _num(text, default=None):
    try:
        return float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


# --- the orders ---------------------------------------------------------------------------------------------------------------------------------------

@page_needed("purchase-orders")
def orders(request):
    mode = request.GET.get("mode")
    if mode == "receive":
        return redirect("/purchase-orders/receive/")
    if mode == "match":
        return redirect("/purchase-orders/match/")
    from engines.po_rules import acknowledge_over_plan, list_pos, orders_over_plan
    if request.method == "POST" and request.POST.get("action") == "noted":
        _say(request, *acknowledge_over_plan(int(request.POST.get("po_id", 0))))
        return redirect("/purchase-orders/")
    tab = request.GET.get("tab", "orders")
    ctx = {"page_id": "purchase-orders", "tab": tab, **_head(request)}
    if tab == "out":
        ctx.update(_going_out(request))
    elif tab == "pipeline":
        from engines.disbursement_trail_service import pipeline
        only_open = request.GET.get("all") != "1"
        ctx.update({"only_open": only_open, "pipe": pipeline(only_open=only_open).to_dict("records")})
    elif tab == "diffs":
        from engines.po_rules import price_differences
        df = price_differences()
        changed = df[df["difference"].abs() > 0.004] if not df.empty else df
        ctx.update({"diffs": (changed if not changed.empty else df).to_dict("records"), "n_all": len(df), "n_changed": len(changed),
                    "net": float(df["difference"].sum()) if not df.empty else 0.0})
    else:
        scope, sup = request.GET.get("show", "open"), request.GET.get("s", "")
        everyone = list_pos()
        names = sorted(dict(zip(everyone["creditor_id"], everyone["supplier"])).items(), key=lambda x: str(x[1]).lower())
        status = scope if scope in PO_STATUS else None
        df = list_pos(status=status, creditor_id=int(sup) if sup.isdigit() else None)
        if scope == "open":
            df = df[df["status"].isin(["DRAFT", "ORDERED", "PARTIAL"])]
        rows = df.to_dict("records")
        for r in rows:
            r["status_label"] = PO_STATUS.get(r["status"], r["status"])
            r["is_draft"] = r["status"] == "DRAFT"
        over = orders_over_plan().to_dict("records")
        ctx.update({"rows": rows, "scope": scope, "scopes": SCOPES, "sup": sup, "names": names, "total": float(df["total"].sum()) if not df.empty else 0.0,
                    "late": int(df["delivery_overdue"].sum()) if not df.empty else 0, "over": over, "may_ack": _can(request, "po.ack_over_plan")})
    return render(request, "buying/orders.html", ctx)


def _going_out(request) -> dict:
    """What is going out, simply: the total per day (or per week) from today, checks, loan and card dues and open orders all counted."""
    import pandas as pd

    from engines.cash_insights_service import scheduled_outflows
    from engines.clock import manila_today
    today = manila_today()
    out = scheduled_outflows(today, today + timedelta(days=120))
    weekly = request.GET.get("by") == "week"
    if out.empty:
        return {"weekly": weekly, "out": []}
    day = pd.to_datetime(out["day"]).dt.date
    if weekly:
        table = out.assign(day=day.map(lambda d: d - timedelta(days=d.weekday()))).groupby("day", as_index=False)["amount"].sum()
    else:
        table = out.assign(day=day).groupby("day", as_index=False)["amount"].sum()
    return {"weekly": weekly, "out": table.sort_values("day").to_dict("records")}


# --- one order ----------------------------------------------------------------------------------------------------------------------------------------

def _po_or_404(po_id: int) -> dict:
    from engines.po_rules import get_po
    po = get_po(po_id)
    if po is None:
        raise Http404
    return po


@page_needed("purchase-orders")
def order(request, po_id: int):
    from engines.permissions import hide_check_details
    from engines.po_rules import cancel_po, close_short, order_po
    po = _po_or_404(po_id)
    h = po["header"]
    owner_of = h.get("created_by")
    if request.method == "POST":
        what = request.POST.get("action")
        if what == "order":
            if _can(request, "po.create", owner_of) and _over_limit(po_id):
                return redirect(f"/purchase-orders/{po_id}/edit/?confirm=1")      # the "Confirm order anyway" dialog first
            ok, msg = order_po(po_id)
        elif what == "cancel":
            ok, msg = cancel_po(po_id, request.POST.get("reason", ""))
        elif what == "close_short":
            ok, msg = close_short(po_id, request.POST.get("reason", ""))
        else:
            ok, msg = False, "Unknown action."
        _say(request, ok, msg)
        return redirect(f"/purchase-orders/{po_id}/")
    status = h["status"]
    trail = None
    if status != "DRAFT":
        from engines.disbursement_trail_service import trail_for_po
        tr = trail_for_po(po_id)
        if tr:
            trail = {"stages": tr["stages"], "current": next((s for s in tr["stages"] if s["key"] == tr["current"]), None),
                     "dates": tr["dates"].to_dict("records"), "invoices": tr["invoices"].to_dict("records"),
                     "checks": [] if hide_check_details(request.slicks_user["role"]) else tr["checks"].to_dict("records")}
    ctx = {"page_id": "purchase-orders", "po": po, "h": h, "lines": po["lines"].to_dict("records"), "status_label": PO_STATUS.get(status, status), "trail": trail,
           "may_order": status == "DRAFT" and _can(request, "po.order", owner_of),
           "may_pdf": status in ("ORDERED", "PARTIAL", "RECEIVED"),
           "may_receive": status in ("ORDERED", "PARTIAL") and _can(request, "po.receive_goods"),
           "may_match": status in ("ORDERED", "PARTIAL", "RECEIVED") and _can(request, "po.receive"),
           "may_change": status in ("DRAFT", "ORDERED") and _can(request, "po.create", owner_of) and not _has_receipt(po_id),
           "may_cancel": status in ("DRAFT", "ORDERED") and _can(request, "po.cancel", owner_of) and not _has_receipt(po_id),
           "may_close": status == "PARTIAL" and _can(request, "po.cancel", owner_of)}
    return render(request, "buying/order.html", ctx)


def _has_receipt(po_id: int) -> bool:
    from engines.common import get_conn
    with get_conn() as conn:
        return bool(conn.execute("SELECT 1 FROM po_receipts WHERE po_id = ? LIMIT 1", (po_id,)).fetchone())


@page_needed("purchase-orders")
def order_pdf(request, po_id: int):
    from engines.po_pdf import build_po_pdf
    po = _po_or_404(po_id)
    if po["header"]["status"] not in ("ORDERED", "PARTIAL", "RECEIVED"):
        raise Http404
    data = build_po_pdf(po_id)
    if not data:
        raise Http404
    resp = HttpResponse(data, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{po["header"]["po_no"].replace(" ", "_")}.pdf"'
    return resp


# --- making and changing an order (saved as it is built) ----------------------------------------------------------------------------------------------

def _line_from_form(p, prefix: str = "") -> dict:
    """One line as typed. A blank discount = the supplier's usual one; 0 = none on this line. A blank list price = the price list's."""
    from engines.discount_rules import parse_chain
    text = p.get(prefix + "chain", "").strip()
    chain = None if text == "" else ("" if parse_chain(text) == [0.0] else text)
    lp = p.get(prefix + "list_price", "").strip()
    return {"item_name": p.get(prefix + "item", "").strip(), "unit": p.get(prefix + "unit", "").strip(), "qty": _num(p.get(prefix + "qty"), 0),
            "list_price": _num(lp) if lp else None, "discount_chain": chain}


def _saved_lines(po: dict) -> list[dict]:
    """The order's lines as they are saved, in the form save_po takes back (a net price typed with no list price is kept as list = net, no discount)."""
    out = []
    for ln in po["lines"].to_dict("records"):
        lp = ln["list_price"] if ln["list_price"] == ln["list_price"] and ln["list_price"] is not None else ln["est_unit_price"]
        chain = ln["discount_chain"] if isinstance(ln["discount_chain"], str) else ""
        out.append({"item_name": ln["item_name"], "unit": ln["unit"] if isinstance(ln["unit"], str) else "", "qty": ln["qty"], "list_price": lp, "discount_chain": chain})
    return out


@page_needed("purchase-orders")
def order_new(request):
    """Step 1: the supplier, the dates and the first item make the draft (an order is never saved without an item)."""
    from engines.clock import manila_today
    from engines.po_rules import default_delivery, save_po
    if not _can(request, "po.create", request.slicks_user["email"]):
        from core.menu import PAGES
        return render(request, "core/not_allowed.html", {"page": PAGES["purchase-orders"]}, status=403)
    suppliers = _suppliers(with_prices=True)                    # only suppliers with a price list can be ordered from (owner, 2026-10-08)
    p = request.POST if request.method == "POST" else request.GET
    cid = int(p.get("s") or (suppliers[0][0] if suppliers else 0) or 0)
    if cid and cid not in dict(suppliers):
        _say(request, False, "That supplier has no price list yet. Add its prices on Price lists first.")
        cid = suppliers[0][0] if suppliers else 0
        if request.method == "POST":
            return redirect("/purchase-orders/new/")
    order_day = _date(p.get("order_date"), manila_today())
    delivery = _date(p.get("delivery")) or (default_delivery(cid, order_day) if cid else order_day + timedelta(days=7))
    if request.method == "POST" and p.get("action") == "start":
        line = _line_from_form(p)
        lines = _picked(p) + ([line] if line["item_name"] else [])           # the ticked items, and/or one typed by hand
        ok, msg, po_id = save_po(None, cid, order_day, delivery, lines, p.get("notes", ""))
        if ok:
            _say(request, True, "Draft saved. Add the other items below.")
            return redirect(f"/purchase-orders/{po_id}/edit/")
        _say(request, False, msg)
    return render(request, "buying/order_new.html", {"page_id": "purchase-orders", **_head(request), "suppliers": suppliers, "cid": cid, "order_day": order_day, "delivery": delivery,
                                                     "names": _names(cid), "v": p})


def _picked(p) -> list[dict]:
    """The lines ticked in the item picker (pick_name_<n> / pick_qty_<n>): list price and discount from the price list."""
    out = []
    for key in p:
        if key.startswith("pick_name_"):
            n = key.removeprefix("pick_name_")
            name, qty = p.get(key, "").strip(), _num(p.get(f"pick_qty_{n}"), 0)
            if name and qty > 0:
                out.append(_line_from_form({"item": name, "qty": qty, "unit": p.get(f"pick_unit_{n}", ""), "list_price": "", "chain": ""}))
    return out


@page_needed("purchase-orders")
def picker_items(request):
    """For the item picker: this supplier's items with their price on the order date (engines/price_list_service.picker_items)."""
    from engines.price_list_service import picker_items as items
    cid = int(request.GET.get("s") or 0)
    if not cid:
        return JsonResponse({"items": []})
    return JsonResponse({"items": items(cid, _date(request.GET.get("on")))})


def _names(cid: int) -> list[str]:
    from engines.price_list_service import item_names
    return item_names(cid) if cid else []


@page_needed("purchase-orders")
def order_edit(request, po_id: int):
    from engines.po_rules import CHANGED, delivery_for_week, po_stamp, save_po
    po = _po_or_404(po_id)
    h = po["header"]
    cid = int(h["creditor_id"])
    if h["status"] not in ("DRAFT", "ORDERED") or _has_receipt(po_id) or not _can(request, "po.create", h.get("created_by")):
        _say(request, False, "This order can no longer be changed here.")
        return redirect(f"/purchase-orders/{po_id}/")
    if request.method == "POST":
        p, what = request.POST, ("remove" if request.POST.get("remove", "") != "" else request.POST.get("action", ""))
        lines = _saved_lines(po)
        order_day, delivery, notes = _date(p.get("order_date"), date.fromisoformat(str(h["order_date"])[:10])), _date(p.get("delivery")), p.get("notes", h["notes"] or "")
        if delivery is None and h["expected_delivery_date"]:
            delivery = date.fromisoformat(str(h["expected_delivery_date"])[:10])
        if what == "add":
            new = _line_from_form(p, "new_")
            if not new["item_name"]:
                _say(request, False, "Type or choose the item.")
                return redirect(f"/purchase-orders/{po_id}/edit/")
            lines.append(new)
        elif what == "add_many":                                 # the item picker: several ticked items in one save
            picked = _picked(p)
            if not picked:
                _say(request, False, "Tick the items to add and give each a quantity.")
                return redirect(f"/purchase-orders/{po_id}/edit/")
            lines += picked
        elif what == "update":
            for i, ln in enumerate(lines):
                if f"qty_{i}" in p:
                    edited = _line_from_form({"item": ln["item_name"], "unit": p.get(f"unit_{i}", ln["unit"]), "qty": p.get(f"qty_{i}"),
                                              "list_price": p.get(f"list_price_{i}"), "chain": p.get(f"chain_{i}")})
                    lines[i] = edited
        elif what == "remove":
            i = int(p.get("remove", -1))
            if len(lines) <= 1:
                _say(request, False, "An order needs at least one item. To drop the whole order, cancel it instead.")
                return redirect(f"/purchase-orders/{po_id}/edit/")
            lines = [ln for k, ln in enumerate(lines) if k != i]
        elif what == "week":
            d = delivery_for_week(cid, _date(p.get("week")), order_day)
            if d is None:
                _say(request, False, "That week is before the order could be paid.")
                return redirect(f"/purchase-orders/{po_id}/edit/")
            delivery = d
        elif what == "use_delivery":
            delivery = _date(p.get("use")) or delivery
        elif what == "pay_day":                                  # a day clicked on the month calendar
            from engines.po_rules import delivery_for_day
            d = delivery_for_day(cid, _date(p.get("day")), order_day)
            if d is None:
                _say(request, False, "No delivery date makes the order fall due that day (it is before the order could be paid, or not a bank day).")
                return redirect(f"/purchase-orders/{po_id}/edit/?view=month&m={p.get('m', '')}#payables")
            delivery = d
        elif what not in ("dates", "order"):
            _say(request, False, "Unknown action.")
            return redirect(f"/purchase-orders/{po_id}/edit/")
        ok, msg, _ = save_po(po_id, cid, order_day, delivery, lines, notes, expected_stamp=p.get("stamp", ""))
        if not ok and msg == CHANGED:
            _say(request, False, "Someone else changed this order after you opened it. Nothing was saved; this is their version. Make your change again.")
            return redirect(f"/purchase-orders/{po_id}/edit/")
        if not ok:
            _say(request, False, msg)
            return redirect(f"/purchase-orders/{po_id}/edit/")
        if what == "order":
            if not p.get("confirm_over") and _over_limit(po_id):
                return redirect(f"/purchase-orders/{po_id}/edit/?confirm=1")      # the "Confirm order anyway" dialog first
            from engines.po_rules import order_po
            ok, msg = order_po(po_id)
            _say(request, ok, msg)
            return redirect(f"/purchase-orders/{po_id}/" if ok else f"/purchase-orders/{po_id}/edit/")
        if what == "pay_day":
            return redirect(f"/purchase-orders/{po_id}/edit/?view=month#payables")
        return redirect(f"/purchase-orders/{po_id}/edit/" + ("#payables" if what in ("week", "use_delivery", "dates") else ""))
    return render(request, "buying/order_edit.html", {"page_id": "purchase-orders", **_edit_ctx(request, po, po_stamp(po_id))})


def _over_limit(po_id: int) -> bool:
    """Would ordering this put its pay day over the daily limit? (owner, 2026-10-08: then a dialog asks first; no approval step.)"""
    from engines.po_rules import day_guard, get_po
    po = get_po(po_id)
    h = po["header"]
    return bool(h["expected_delivery_date"]) and day_guard(int(h["creditor_id"]), h["expected_delivery_date"], float(po["total"]), po_id)["over"]


def _edit_ctx(request, po: dict, stamp: str) -> dict:
    from engines.discount_rules import chain_label
    from engines.permissions import hide_check_details
    from engines.po_rules import day_guard, days_strip, expected_pay_date, week_schedule, weeks_strip
    from engines.settings_registry import forecast_shown
    h = po["header"]
    po_id, cid, total = int(h["po_id"]), int(h["creditor_id"]), float(po["total"])
    delivery = h["expected_delivery_date"]
    lines = []
    for ln in po["lines"].to_dict("records"):
        lp = ln["list_price"] if ln["list_price"] == ln["list_price"] and ln["list_price"] is not None else ln["est_unit_price"]
        chain = ln["discount_chain"] if isinstance(ln["discount_chain"], str) else ""
        lines.append({**ln, "list_shown": lp, "chain_shown": chain if chain else "0", "chain_label": chain_label(chain) or "none",
                      "unit": ln["unit"] if isinstance(ln["unit"], str) else ""})
    pay = expected_pay_date(cid, delivery)
    ctx = {"po": po, "h": h, "lines": lines, "stamp": stamp, "total": total, "pay": pay, "names": _names(cid), "may_order": h["status"] == "DRAFT" and _can(request, "po.order", h.get("created_by"))}
    if pay is None:
        return ctx
    guard = day_guard(cid, delivery, total, po_id)
    view = request.GET.get("view", "month")
    cal = None
    if view == "month":
        from engines.po_rules import month_calendar
        cal = month_calendar(_date(request.GET.get("m")) or pay, pay, po_id)
    if view == "weekly":
        strip = weeks_strip(pay, exclude_po_id=po_id)
        bars = [{"label": f"{r.week_start:%m/%d}", "total": float(r.total), "mine": total if r.selected else 0.0, "selected": bool(r.selected), "lightest": bool(r.lightest),
                 "week": r.week_start, "reachable": True} for r in strip.itertuples()]
    else:
        strip = days_strip(pay, before=7, after=14, exclude_po_id=po_id)
        bars = [{"label": f"{r.day:%m/%d} {r.day:%a}", "total": float(r.total), "mine": total if r.selected else 0.0, "selected": bool(r.selected), "lightest": bool(r.lightest)}
                for r in strip.itertuples()]
    top = max([b["total"] + b["mine"] for b in bars] + [1.0])
    for b in bars:
        b["h"], b["h_mine"] = round(b["total"] / top * 100, 1), round(b["mine"] / top * 100, 1)
    weeks = [{"week": r.week_start, "label": f"{r.week_start:%m/%d}", "total": float(r.total), "selected": bool(r.selected), "lightest": bool(r.lightest)}
             for r in weeks_strip(pay, exclude_po_id=po_id).itertuples()]
    sched = week_schedule(pay, exclude_po_id=po_id)
    show_ref = not hide_check_details(request.slicks_user["role"])
    cash = None
    if forecast_shown() and total > 0:
        from engines.po_rules import cash_check
        cash = cash_check(cid, total, delivery, exclude_po_id=po_id)
    ctx.update({"guard": guard, "view": view, "bars": bars, "weeks": weeks, "sched": sched, "sched_rows": sched["rows"].to_dict("records") if sched else [],
                "show_ref": show_ref, "cash": cash, "cal": cal,
                "confirm": request.GET.get("confirm") == "1" and guard["over"] and ctx["may_order"]})
    return ctx


@page_needed("purchase-orders")
def price_hint(request):
    """For the item box on an order: the supplier's list price, unit and usual discount on the order date (filled in as the item is chosen)."""
    from engines.discount_rules import chain_text, standing_chain
    from engines.price_list_service import price_on
    cid, item, on = int(request.GET.get("s") or 0), request.GET.get("item", ""), _date(request.GET.get("on"))
    listed = price_on(cid, item, on) if cid and item else None
    if not listed:
        return JsonResponse({"found": False})
    return JsonResponse({"found": True, "unit": listed["unit"] or "", "list_price": float(listed["unit_price"]), "since": str(listed["effective_date"])[:10],
                         "chain": chain_text(standing_chain(cid, listed["item_name"], on)) or "0"})


# --- receiving and matching ---------------------------------------------------------------------------------------------------------------------------

def _supplier_pick(request, suppliers):
    ids = [s for s, _ in suppliers]
    cid = int(request.GET.get("s") or request.POST.get("s") or request.session.get("rcv_supplier") or (ids[0] if ids else 0) or 0)
    if cid not in ids:
        cid = ids[0] if ids else 0
    request.session["rcv_supplier"] = cid
    return cid


def _batches(cid: int) -> list[tuple[int, str]]:
    from engines.common import get_conn
    with get_conn() as conn:
        rows = conn.execute("SELECT statement_id, statement_ref, status FROM order_statements WHERE creditor_id = ? AND status != 'Cancelled' AND is_locked = 0 "
                            "ORDER BY CASE WHEN status = 'Draft' THEN 0 ELSE 1 END, statement_id DESC", (cid,)).fetchall()
    return [(int(r[0]), f"{r[1]} ({r[2]})") for r in rows]


def _picks(p, lines: list[dict], with_price: bool) -> list[dict]:
    out = []
    for ln in lines:
        lid = int(ln["line_id"])
        if p.get(f"pick_{lid}") == "1":
            pk = {"line_id": lid, "qty": _num(p.get(f"qty_{lid}"), 0)}
            if with_price:
                pk["actual_unit_price"] = _num(p.get(f"price_{lid}"), -1)
            out.append(pk)
    return out


def _match(request, cid: int, lines: list[dict], goods_received: bool):
    """Saves an invoice for the ticked lines into the chosen batch (a new one when asked)."""
    from engines.po_rules import receive_into_batch
    from engines.statement_rules import create_order_statement
    p = request.POST
    picks = _picks(p, lines, True)
    if not picks:
        return False, "Tick at least one line the invoice covers."
    from engines.statement_rules import doc_reference
    ref = doc_reference(p.get("doc"), p.get("dr"))
    if not ref:
        return False, "Enter the document reference: the supplier's SI no. or DR no."
    batch = p.get("batch", "")
    sid = int(batch) if batch.isdigit() else (create_order_statement(cid) if batch == "new" else None)      # None: waits for a batch (owner, 2026-10-03)
    override = _num(p.get("override"))
    ok, msg, _ = receive_into_batch(cid, sid, ref, _date(p.get("inv_date")), picks, update_prices=p.get("update_prices") == "1",
                                    gross_override=override if override and override > 0 else None, goods_received=goods_received, dr_no=p.get("dr", "").strip() or None)
    return ok, msg


@page_needed("purchase-orders")
def receive(request):
    """A delivery arrived: tick what came. A person who may also match invoices can do both at once when the invoice came with the goods."""
    from engines.clock import manila_today
    from engines.po_rules import receivable_lines, receive_goods
    if not _can(request, "po.receive_goods"):
        from core.menu import PAGES
        return render(request, "core/not_allowed.html", {"page": PAGES["purchase-orders"]}, status=403)
    suppliers = _suppliers()
    cid = _supplier_pick(request, suppliers)
    both = request.GET.get("both") == "1" and _can(request, "po.receive")
    lines = receivable_lines(cid).to_dict("records") if cid else []
    if request.method == "POST":
        p = request.POST
        if p.get("action") == "both":
            ok, msg = _match(request, cid, lines, goods_received=False)
        else:
            picks = _picks(p, lines, False)
            ok, msg, _ = receive_goods(cid, picks, _date(p.get("received"), manila_today()), p.get("ref", ""), p.get("kind", "delivery receipt"))
        _say(request, ok, msg)
        return redirect(f"/purchase-orders/receive/?s={cid}" + ("&both=1" if p.get("action") == "both" and not ok else ""))
    pre = request.GET.get("po", "")
    for ln in lines:
        ln["checked"] = str(ln["po_id"]) == pre
    return render(request, "buying/receive.html", {"page_id": "purchase-orders", **_head(request), "suppliers": suppliers, "cid": cid, "lines": lines, "both": both,
                                                   "may_both": _can(request, "po.receive"), "today": manila_today(), "batches": _batches(cid) if both else []})


@page_needed("purchase-orders")
def match(request):
    """The supplier's invoice arrived: match it to the goods received and put it in a payables batch at the price actually charged."""
    from engines.clock import manila_today
    from engines.po_rules import invoiceable_lines, receivable_lines
    if not _can(request, "po.receive"):
        from core.menu import PAGES
        return render(request, "core/not_allowed.html", {"page": PAGES["purchase-orders"]}, status=403)
    suppliers = _suppliers()
    cid = _supplier_pick(request, suppliers)
    waiting = invoiceable_lines(cid).to_dict("records") if cid else []
    unreceived = receivable_lines(cid).to_dict("records") if cid else []
    if request.method == "POST":
        goods_received = request.POST.get("action") == "match"
        ok, msg = _match(request, cid, waiting if goods_received else unreceived, goods_received)
        _say(request, ok, msg)
        return redirect(f"/purchase-orders/match/?s={cid}")
    pre = request.GET.get("po", "")
    refs = [str(r["received_ref"]) for r in waiting if r.get("received_ref") and not str(r["received_ref"]).startswith("DR ")]
    came_with = refs[0] if len(set(refs)) == 1 else ""
    for ln in waiting:
        ln["checked"] = str(ln["po_id"]) == pre or (bool(came_with) and str(ln.get("received_ref")) == came_with)
    for ln in unreceived:
        ln["checked"] = str(ln["po_id"]) == pre and not waiting
    pre_batch = request.GET.get("batch", "")                   # opened from a batch's "From a purchase order": that batch is chosen
    return render(request, "buying/match.html", {"page_id": "purchase-orders", **_head(request), "suppliers": suppliers, "cid": cid, "waiting": waiting, "unreceived": unreceived,
                                                 "came_with": came_with, "today": manila_today(), "batches": _batches(cid), "pre_batch": pre_batch})
