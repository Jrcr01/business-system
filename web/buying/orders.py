"""Stage D2b (Purchase orders, docs/mockups/d2_buying.html screens 4-8, approved 2026-10-03): the orders and where each stands, one order with its trail to the bank,
a new order saved as it is built (with the payables bars and the daily-limit warning), Receive goods, and Match invoice into a payables batch.
Owner, 2026-10-11: the calendar is on the first screen, items are found by searching, and a draft is SENT to the supplier (a request) and becomes a
purchase order only when they confirm what is available (order_confirm).
The rules are in engines/po_rules.py (and disbursement_trail_service, cash_insights_service); these views draw the forms and pass on what was typed."""
from __future__ import annotations

from datetime import date, timedelta

from django.contrib import messages
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render

from buying.views import _date, _suppliers
from masters.views import page_needed

PO_STATUS = {"DRAFT": "Draft", "ORDERED": "Ordered", "PARTIAL": "Partly received", "RECEIVED": "Received", "CANCELLED": "Cancelled"}
SCOPES = [("open", "Open (draft, sent, ordered, partly received)"), ("DRAFT", "Draft"), ("SENT", "Sent to supplier"), ("ORDERED", "Ordered"), ("PARTIAL", "Partly received"), ("RECEIVED", "Received"),
          ("CANCELLED", "Cancelled"), ("all", "All")]


def _can(request, action: str, created_by: str | None = None) -> bool:
    from engines.permissions import can
    u = request.slicks_user
    return can(u["role"], action, created_by=created_by, me=u["email"])


def _head(request) -> dict:
    """What the buttons above every purchase-order screen need: which of New order, Receive goods and Match invoice this person may use."""
    return {"may_create": _can(request, "po.create", request.slicks_user["email"]), "may_receive": _may_receive(request)}


def _may_receive(request) -> bool:
    """Receiving always records the payable (owner, 2026-10-11): one switch, whichever of the two older ones the person has."""
    return _can(request, "po.receive_goods") or _can(request, "po.receive")


def _say(request, ok: bool, msg: str) -> None:
    (messages.success if ok else messages.error)(request, msg)


def _cannot_fall_due(request, cid: int, day, order_day) -> None:
    """A plain note, not an error (owner, 2026-10-12: "yes change it to a plainer note"): the day clicked cannot be this order's pay day -- too early
    (before the order date plus the supplier's terms) or not a bank day. The list under the calendar still shows the day clicked."""
    from engines.po_rules import expected_pay_date
    if day is None:
        return
    first = expected_pay_date(cid, order_day)
    if first and day < first:
        msg = (f"This order cannot fall due on {day:%m/%d/%Y}: the earliest is {first:%m/%d/%Y} (the order date plus the supplier's terms). "
               f"Its pay day is unchanged; {day:%m/%d/%Y} is shown below.")
    else:
        msg = (f"{day:%m/%d/%Y} cannot be this order's pay day: it is a weekend, a holiday or a marked day off, or the supplier's terms do not land on it. "
               f"Its pay day is unchanged; {day:%m/%d/%Y} is shown below.")
    messages.info(request, msg)


def _manila_day(stamp) -> date | None:
    """The Manila date of a time the database stamped (CURRENT_TIMESTAMP is UTC: a request sent at 2 AM here must not read as yesterday)."""
    from datetime import datetime
    try:
        return (datetime.fromisoformat(str(stamp)[:19]) + timedelta(hours=8)).date()
    except (TypeError, ValueError):
        return None


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
    if tab not in ("orders", "pipeline", "diffs"):               # "Going out" was removed (owner, 2026-10-11): the order calendar and Payables > Calendar show it
        tab = "orders"
    ctx = {"page_id": "purchase-orders", "tab": tab, **_head(request)}
    if tab == "pipeline":
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
        status = "DRAFT" if scope == "SENT" else (scope if scope in PO_STATUS else None)
        df = list_pos(status=status, creditor_id=int(sup) if sup.isdigit() else None)
        if scope == "open":
            df = df[df["status"].isin(["DRAFT", "ORDERED", "PARTIAL"])]
        elif scope in ("DRAFT", "SENT"):                         # a sent request is still a draft underneath
            df = df[df["requested_at"].notna() == (scope == "SENT")]
        from engines.clock import manila_today
        today = manila_today()
        rows = df.to_dict("records")
        for r in rows:
            r["is_draft"] = r["status"] == "DRAFT"
            r["is_sent"] = r["is_draft"] and isinstance(r["requested_at"], str) and bool(r["requested_at"])
            r["status_label"] = "Sent to supplier" if r["is_sent"] else PO_STATUS.get(r["status"], r["status"])
            r["waiting"] = (today - _manila_day(r["requested_at"])).days if r["is_sent"] else None
        over = orders_over_plan().to_dict("records")
        ctx.update({"rows": rows, "scope": scope, "scopes": SCOPES, "sup": sup, "names": names, "total": float(df["total"].sum()) if not df.empty else 0.0,
                    "late": int(df["delivery_overdue"].sum()) if not df.empty else 0, "over": over, "may_ack": _can(request, "po.ack_over_plan")})
    return render(request, "buying/orders.html", ctx)


# --- one order ----------------------------------------------------------------------------------------------------------------------------------------

def _po_or_404(po_id: int) -> dict:
    from engines.po_rules import get_po
    po = get_po(po_id)
    if po is None:
        raise Http404
    return po


def _delivery_snapshot(receipt_id: int) -> dict | None:
    """A delivery's figures before it is undone to be corrected: its lines by order line, its document and date, and the same in words."""
    from engines.common import get_conn
    with get_conn() as conn:
        r = conn.execute("""SELECT r.received_date, r.supplier_invoice_no, i.doc_ref, i.dr_no, i.invoice_date FROM po_receipts r LEFT JOIN invoices i ON i.invoice_id = r.invoice_id
                            WHERE r.receipt_id = ?""", (receipt_id,)).fetchone()
        if not r:
            return None
        lines = conn.execute("""SELECT x.line_id, x.qty, x.unit_price, l.item_name FROM po_receipt_lines x JOIN purchase_order_lines l ON l.line_id = x.line_id
                                WHERE x.receipt_id = ? ORDER BY x.receipt_line_id""", (receipt_id,)).fetchall()
    doc = r[2] or r[1] or ""
    si = "" if str(doc).upper().startswith("DR ") else doc
    dr = r[3] or (str(doc)[3:] if str(doc).upper().startswith("DR ") else "")
    day = str(r[4] or r[0] or "")[:10]
    text = f"{doc or 'no document'} · {day} · " + "; ".join(f"{float(q):g} x {n} @ {float(p):,.2f}" for _, q, p, n in lines)
    return {"doc": doc, "si": si, "dr": dr, "date": day, "lines": {str(a): {"qty": float(b), "price": float(c)} for a, b, c, _ in lines}, "text": text}


@page_needed("purchase-orders")
def order(request, po_id: int):
    from engines.permissions import hide_check_details
    from engines.po_rules import cancel_po, close_short, is_request, request_po, unsend_request
    po = _po_or_404(po_id)
    h = po["header"]
    owner_of = h.get("created_by")
    if request.method == "POST":
        what = request.POST.get("action")
        if what == "request":                                    # Send to supplier (owner, 2026-10-11): a PO only once they confirm
            if _can(request, "po.create", owner_of) and _over_limit(po_id):
                return redirect(f"/purchase-orders/{po_id}/edit/?confirm=1")      # the "over your daily limit" dialog first
            ok, msg = request_po(po_id)
        elif what == "unsend":
            ok, msg = unsend_request(po_id)
        elif what == "undo_delivery":                            # a delivery recorded wrongly: back to "still to come" (owner, 2026-10-11)
            from engines.po_rules import undo_receipt
            ok, msg = undo_receipt(int(request.POST.get("receipt_id") or 0), request.POST.get("reason", ""))
        elif what == "edit_delivery":                            # owner, 2026-10-19: correct a delivery = undo it, then record it again, filled in
            redo = _delivery_snapshot(int(request.POST.get("receipt_id") or 0))
            why = " ".join(request.POST.get("why", "").split())
            from engines.po_rules import undo_receipt
            ok, msg = undo_receipt(int(request.POST.get("receipt_id") or 0), "to correct it" + (f": {why}" if why else ""))
            if ok and redo:
                from engines.common import write_tx
                from engines.entry_changes import log
                with write_tx() as conn:
                    log(conn, "DELIVERY_EDITED", "purchase_orders", po_id, f"{redo['doc'] or 'delivery'} ({h['po_no']})", redo["text"],
                        "undone to be recorded again (see the delivery recorded next)", why)
                request.session["redo_delivery"] = {**redo, "po_id": po_id}
                _say(request, True, f"{redo['doc'] or 'The delivery'} is undone. Correct what was wrong below and record it again.")
                return redirect(f"/purchase-orders/receive/?s={h['creditor_id']}&po={po_id}")
        elif what == "add_si":                                   # the SI came later for goods that came on a DR: the same payable
            from engines.po_rules import add_si_to_receipt
            ok, msg = add_si_to_receipt(int(request.POST.get("receipt_id") or 0), request.POST.get("si", ""))
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
    sent = is_request(h)
    ctx = {"page_id": "purchase-orders", "po": po, "h": h, "lines": po["lines"].to_dict("records"), "trail": trail, "sent": sent,
           "status_label": "Sent to supplier" if sent else PO_STATUS.get(status, status), "sent_on": _manila_day(h.get("requested_at")) if sent else None,
           "may_send": status == "DRAFT" and not sent and _can(request, "po.order", owner_of),
           "may_confirm": sent and _can(request, "po.order", owner_of),
           "may_pdf": status in ("ORDERED", "PARTIAL", "RECEIVED"),
           "may_receive": status in ("ORDERED", "PARTIAL") and _can(request, "po.receive_goods"),
           "may_match": status in ("ORDERED", "PARTIAL", "RECEIVED") and _can(request, "po.receive"),
           "may_change": status in ("DRAFT", "ORDERED") and _can(request, "po.create", owner_of) and not _has_receipt(po_id),
           "may_cancel": status in ("DRAFT", "ORDERED") and _can(request, "po.cancel", owner_of) and not _has_receipt(po_id),
           "may_close": status == "PARTIAL" and _can(request, "po.cancel", owner_of)}
    if status not in ("DRAFT", "CANCELLED"):
        from engines.po_rules import deliveries
        ctx["deliveries"] = deliveries(po_id)
        ctx["may_inv"] = ctx["may_undo"] = _may_receive(request)
        ctx["open_to_receive"] = ((status in ("ORDERED", "PARTIAL") and ctx["may_inv"])
                                  or (ctx["may_inv"] and any(d["invoice_id"] is None and d["lines"] for d in ctx["deliveries"])))
        if status in ("ORDERED", "PARTIAL"):
            left = [(max(float(ln["qty"]) - float(ln["qty_received"]), 0.0), float(ln["est_unit_price"])) for ln in ctx["lines"]]
            ctx["to_come"] = {"pcs": sum(q for q, _ in left), "amount": sum(q * pr for q, pr in left)}
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


@page_needed("purchase-orders")
def request_pdf(request, po_id: int):
    """The order request the supplier gets while the order is still a draft: "not yet a purchase order, please confirm availability"."""
    from engines.po_pdf import build_po_pdf
    po = _po_or_404(po_id)
    data = build_po_pdf(po_id, request=True)
    if not data:
        raise Http404
    resp = HttpResponse(data, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="Order_request_{po["header"]["request_no"]}.pdf"'
    return resp


@page_needed("purchase-orders")
def order_confirm(request, po_id: int):
    """The supplier answered (owner, 2026-10-11): the available quantity per line (0 drops the line) and the delivery date they promised; what is
    available becomes the purchase order. Over the daily limit: the confirm dialog first."""
    from engines.po_rules import CHANGED, confirm_request, day_guard, expected_pay_date, is_request, po_stamp
    po = _po_or_404(po_id)
    h = po["header"]
    if not is_request(h) or not _can(request, "po.order", h.get("created_by")):
        _say(request, False, "Only a request sent to the supplier can be confirmed.")
        return redirect(f"/purchase-orders/{po_id}/")
    cid = int(h["creditor_id"])
    lines = po["lines"].to_dict("records")
    delivery = _date(h["expected_delivery_date"])
    avail = {int(ln["line_id"]): float(ln["qty"]) for ln in lines}
    guard = None
    if request.method == "POST":
        p = request.POST
        delivery = _date(p.get("delivery")) or delivery
        bad = None
        for ln in lines:
            raw = (p.get(f"avail_{ln['line_id']}") or "").strip()
            n = _num(raw) if raw else float(ln["qty"])
            if n is None:
                bad = f"{ln['item_name']}: '{raw}' is not a quantity."
            else:
                avail[int(ln["line_id"])] = n
        if bad:
            _say(request, False, bad)
        else:
            total = sum(avail[int(ln["line_id"])] * float(ln["est_unit_price"]) for ln in lines)
            g = day_guard(cid, delivery, total, po_id)
            if g["over"] and not p.get("confirm_over"):
                guard = g                                         # "Confirm order anyway" (no approval step; it goes on the Review list)
            else:
                ok, msg = confirm_request(po_id, avail, delivery, p.get("stamp"))
                if not ok and msg == CHANGED:
                    _say(request, False, "Someone else changed this request after you opened it. Nothing was saved; this is their version.")
                    return redirect(f"/purchase-orders/{po_id}/confirm/")
                _say(request, ok, msg)
                if ok:
                    return redirect(f"/purchase-orders/{po_id}/")
    rows = [{**ln, "avail": avail[int(ln["line_id"])], "amount": round(avail[int(ln["line_id"])] * float(ln["est_unit_price"]), 2)} for ln in lines]
    return render(request, "buying/order_confirm.html", {"page_id": "purchase-orders", "po": po, "h": h, "rows": rows, "delivery": delivery, "guard": guard,
                                                         "total": sum(r["amount"] for r in rows), "asked": po["total"], "pay": expected_pay_date(cid, delivery),
                                                         "stamp": po_stamp(po_id)})


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
    """The first screen (owner, 2026-10-11): supplier and dates, the payables calendar straight away (before anything is saved), then the search.
    Adding the first item saves the draft (an order is never saved without an item) and carries on in the same layout."""
    from urllib.parse import urlencode

    from engines.clock import manila_today
    from engines.po_rules import default_delivery, delivery_for_day, save_po
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
    if request.method == "GET" and p.get("day") and cid:         # a day clicked on the calendar: the delivery date that makes the order fall due then
        d = delivery_for_day(cid, _date(p.get("day")), order_day)
        if d is None:
            _cannot_fall_due(request, cid, _date(p.get("day")), order_day)
        else:
            delivery = d
    if request.method == "POST" and p.get("action") == "start":
        line = _line_from_form(p)
        lines = _picked(p) + ([line] if line["item_name"] else [])           # the ticked items, and/or one typed by hand
        ok, msg, po_id = save_po(None, cid, order_day, delivery, lines, p.get("notes", ""))
        if ok:
            _say(request, True, "Draft saved.")
            return redirect(f"/purchase-orders/{po_id}/edit/")
        _say(request, False, "Search for an item and give it a quantity first." if msg == "Add at least one item." else msg)
    ctx = {"page_id": "purchase-orders", **_head(request), "suppliers": suppliers, "cid": cid, "order_day": order_day, "delivery": delivery, "names": _names(cid), "v": p,
           "qs": urlencode({"s": cid, "order_date": order_day.isoformat(), "delivery": delivery.isoformat()}) + "&", "total": 0.0}
    if cid:
        ctx.update(_calendar_ctx(request, cid, delivery, 0.0, None))
    return render(request, "buying/order_new.html", ctx)


def _picked(p) -> list[dict]:
    """The lines ticked in the item picker (pick_name_<n> / pick_qty_<n>): list price and discount from the price list."""
    out = []
    for key in p:
        if key.startswith("pick_name_"):
            n = key.removeprefix("pick_name_")
            name, qty = p.get(key, "").strip(), _num(p.get(f"pick_qty_{n}"), 0)
            if name and qty > 0:
                out.append({**_line_from_form({"item": name, "qty": qty, "unit": p.get(f"pick_unit_{n}", ""), "list_price": "", "chain": ""}),
                            "actual": _num(p.get(f"pick_price_{n}"))})        # the price on the document, where the picker asks for one (Receive)
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
    from engines.po_rules import CHANGED, delivery_for_week, is_request, po_stamp, save_po
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
                _cannot_fall_due(request, cid, _date(p.get("day")), order_day)
                return redirect(f"/purchase-orders/{po_id}/edit/?view=month&m={p.get('m', '')}&look={p.get('day', '')}#payables")
            delivery = d
        elif what == "order":                                    # no shortcut (owner, 2026-10-11): a PO only once the supplier confirmed
            _say(request, False, "Send this draft to the supplier first. It becomes a purchase order when they confirm the items are available.")
            return redirect(f"/purchase-orders/{po_id}/edit/")
        elif what not in ("dates", "request"):
            _say(request, False, "Unknown action.")
            return redirect(f"/purchase-orders/{po_id}/edit/")
        ok, msg, _ = save_po(po_id, cid, order_day, delivery, lines, notes, expected_stamp=p.get("stamp", ""))
        if not ok and msg == CHANGED:
            _say(request, False, "Someone else changed this order after you opened it. Nothing was saved; this is their version. Make your change again.")
            return redirect(f"/purchase-orders/{po_id}/edit/")
        if not ok:
            _say(request, False, msg)
            return redirect(f"/purchase-orders/{po_id}/edit/")
        if what == "request":
            if h["status"] != "DRAFT" or is_request(h):
                _say(request, False, "Only a draft not sent yet is sent to the supplier.")
                return redirect(f"/purchase-orders/{po_id}/")
            if not p.get("confirm_over") and _over_limit(po_id):
                return redirect(f"/purchase-orders/{po_id}/edit/?confirm=1")      # the "over your daily limit" dialog first: the earliest point to change the date
            from engines.po_rules import request_po
            ok, msg = request_po(po_id)
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
    from engines.po_rules import is_request
    h = po["header"]
    po_id, cid, total = int(h["po_id"]), int(h["creditor_id"]), float(po["total"])
    lines = []
    for ln in po["lines"].to_dict("records"):
        lp = ln["list_price"] if ln["list_price"] == ln["list_price"] and ln["list_price"] is not None else ln["est_unit_price"]
        chain = ln["discount_chain"] if isinstance(ln["discount_chain"], str) else ""
        lines.append({**ln, "list_shown": lp, "chain_shown": chain if chain else "0", "chain_label": chain_label(chain) or "none",
                      "unit": ln["unit"] if isinstance(ln["unit"], str) else ""})
    sent = is_request(h)
    ctx = {"po": po, "h": h, "lines": lines, "stamp": stamp, "total": total, "names": _names(cid), "sent": sent, "qs": "", "cid": cid,
           "may_send": h["status"] == "DRAFT" and not sent and _can(request, "po.order", h.get("created_by"))}
    ctx.update(_calendar_ctx(request, cid, h["expected_delivery_date"], total, po_id))
    ctx["confirm"] = request.GET.get("confirm") == "1" and bool(ctx.get("guard")) and ctx["guard"]["over"] and ctx["may_send"]
    return ctx


def _calendar_ctx(request, cid: int, delivery, total: float, po_id: int | None) -> dict:
    """The payables on the day an order would fall due: the month calendar, the daily-limit warning and the clicked day's own list, split into
    what is actually committed and what is an estimate (owner, 2026-10-12).
    Works before the order is saved (po_id None, total 0): the calendar is the first thing on the screen (owner, 2026-10-11)."""
    from engines.permissions import hide_check_details
    from engines.po_rules import day_guard, expected_pay_date, week_schedule, weeks_strip
    from engines.settings_registry import forecast_shown
    pay = expected_pay_date(cid, delivery)
    if pay is None:
        return {"pay": None}
    guard = day_guard(cid, delivery, total, po_id)
    from engines.po_rules import day_schedule, month_calendar
    cal = month_calendar(_date(request.GET.get("m")) or pay, pay, po_id)     # the month only (owner, 2026-10-12: daily and weekly views removed)
    weeks = [{"week": r.week_start, "label": f"{r.week_start:%m/%d}", "total": float(r.total), "selected": bool(r.selected), "lightest": bool(r.lightest)}
             for r in weeks_strip(pay, exclude_po_id=po_id).itertuples()]
    sched = week_schedule(pay, exclude_po_id=po_id)
    looked = _date(request.GET.get("look") or request.GET.get("day")) or pay     # the day clicked, even when the order cannot fall due on it (owner, 2026-10-12)
    day = day_schedule(looked, exclude_po_id=po_id)
    cash = None
    if forecast_shown() and total > 0:
        from engines.po_rules import cash_check
        cash = cash_check(cid, total, delivery, exclude_po_id=po_id)
    from engines.order_room import weeks as room_weeks
    room = room_weeks(exclude_po_id=po_id, order_pay_day=pay, order_amount=total)
    return {"pay": pay, "guard": guard, "weeks": weeks, "sched": sched, "sched_rows": sched["rows"].to_dict("records") if sched else [], "daysched": day, "looked_is_pay": looked == pay,
            "show_ref": not hide_check_details(request.slicks_user["role"]), "cash": cash, "cal": cal, "room": room,
            "room_pick": next((w for w in room["weeks"] if w["selected"]), None)}


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
            pk = {"line_id": lid, "qty": _num(p.get(f"qty_{lid}"), 0), "dot": p.get(f"dot_{lid}", "")}      # the tires' DOT, when typed (stage A)
            if with_price:
                pk["actual_unit_price"] = _num(p.get(f"price_{lid}"), -1)
            out.append(pk)
    return out


def _match(request, cid: int, lines: list[dict] | None, goods_received: bool, picks: list[dict] | None = None, allow_more: bool = False,
           receipt_id: int | None = None, dr: str | None = None):
    """Saves the payable for the ticked lines (the SI, or the DR when that is all the supplier gives) into the chosen batch -- a new one when asked.
    `picks` given: those lines instead of the ticked ones. `receipt_id` + `dr`: the document of a delivery recorded earlier."""
    from engines.po_rules import receive_into_batch
    from engines.statement_rules import create_order_statement, doc_reference
    p = request.POST
    picks = _picks(p, lines, True) if picks is None else picks
    if not picks:
        return False, "Tick at least one line."
    dr = p.get("dr", "") if dr is None else dr
    ref = doc_reference(p.get("doc"), dr)
    if not ref:
        return False, "Type the SI no. or the DR no.: at least one."
    batch = p.get("batch", "") if _can(request, "invoice.enter") else ""       # which batch is the payables side's choice; otherwise it waits for one
    sid = int(batch) if batch.isdigit() else (create_order_statement(cid) if batch == "new" else None)      # None: waits for a batch (owner, 2026-10-03)
    override = _num(p.get("override"))
    ok, msg, _ = receive_into_batch(cid, sid, ref, _date(p.get("inv_date")), picks, update_prices=p.get("update_prices") == "1",
                                    gross_override=override if override and override > 0 else None, goods_received=goods_received, dr_no=dr.strip() or None,
                                    allow_more=allow_more, receipt_id=receipt_id)
    return ok, msg


def _waiting_suppliers(with_invoices: bool) -> list[tuple[int, str]]:
    """Only the suppliers with something to receive: an ordered PO with goods still to come, or (for a person who records invoices) goods received
    on a DR and waiting for their SI."""
    from engines.common import get_conn
    waiting_si = "OR l.qty_received - COALESCE(l.qty_invoiced, l.qty_received) > 0.0000001" if with_invoices else ""
    with get_conn() as conn:
        rows = conn.execute(f"""SELECT DISTINCT c.creditor_id, c.creditor_name FROM purchase_orders p JOIN purchase_order_lines l ON l.po_id = p.po_id
                                JOIN creditors c ON c.creditor_id = p.creditor_id
                                WHERE (p.status IN ('ORDERED', 'PARTIAL') AND l.qty - l.qty_received > 0.0000001) {waiting_si}
                                ORDER BY c.creditor_name COLLATE NOCASE""").fetchall()
    return [(int(r[0]), r[1]) for r in rows]


def _arrive(request, cid: int, coming: list[dict], may_goods: bool, may_inv: bool):
    """A delivery typed on Receive (owner, 2026-10-11): the SI no. or the DR no. -- either one records the goods AND the payable ("our suppliers dont give
    SI just DR then that only binds us"). Returns (ok, message, ask): `ask` is a question to put first, nothing saved yet --
      * more than ordered: "agreed with the supplier?";
      * part of the order has not come while Settings says one DR per order: the rest will not come, or keep it open for another DR."""
    from engines.po_rules import add_line_at_delivery, close_short, flag_delivery, many_drs, remove_added_line
    p = request.POST
    si, dr = p.get("doc", "").strip(), p.get("dr", "").strip()
    picks = _picks(p, coming, True)
    extras = _picked(p)                                          # items that came but were not on the order (the drop-down)
    po = int(p.get("po")) if p.get("po", "").isdigit() else None
    if not picks and not extras:
        return False, "Tick what arrived.", None
    if not si and not dr:
        return False, "Type the SI no. or the DR no.: at least one.", None
    if not (may_inv or may_goods):
        return False, "Receiving is switched off for you.", None
    if extras and po is None:
        return False, "Choose the purchase order first, then add the item that was not on it.", None
    by_line = {int(ln["line_id"]): ln for ln in coming}
    got = {int(pk["line_id"]): float(pk["qty"] or 0) for pk in picks}
    more = [(by_line[i], q) for i, q in got.items() if q > float(by_line[i]["remaining"]) + 1e-6]
    touched = {int(by_line[i]["po_id"]) for i in got} | ({po} if extras else set())
    rest = {}
    for ln in coming:
        left = max(float(ln["remaining"]) - got.get(int(ln["line_id"]), 0.0), 0.0)
        if int(ln["po_id"]) in touched and left > 1e-6:
            pcs, amt, no = rest.get(int(ln["po_id"]), (0.0, 0.0, ln["po_no"]))
            rest[int(ln["po_id"])] = (pcs + left, amt + left * float(ln["est_unit_price"]), no)
    one_dr = not many_drs()
    choice = p.get("rest", "")
    if (more and p.get("confirm_more") != "1") or (rest and one_dr and choice not in ("close", "open")):
        return False, "", {"more": [f"{ln['item_name']}: {q:g} typed, {float(ln['remaining']):g} still to come on {ln['po_no']} ({q - float(ln['remaining']):g} more than ordered)" for ln, q in more],
                           "rest": [f"{no}: {pcs:g} pc(s) have not arrived (₱{amt:,.2f})" for pcs, amt, no in rest.values()] if one_dr else [],
                           "carry": [(k, v) for k, v in p.items() if k not in ("csrfmiddlewaretoken", "rest", "confirm_more")]}
    added = []
    for ex in extras:
        ok, msg, line_id, price = add_line_at_delivery(po, ex["item_name"], ex["qty"], ex["unit"])
        if not ok:
            for a in added:
                remove_added_line(a[0])
            return False, msg, None
        added.append((line_id, ex["item_name"], ex["qty"]))
        picks.append({"line_id": line_id, "qty": ex["qty"], "actual_unit_price": ex["actual"] if ex.get("actual") and ex["actual"] > 0 else price})
    for pk in picks:                                             # a price left empty = the order's price
        if pk.get("actual_unit_price") is None or pk["actual_unit_price"] < 0:
            pk["actual_unit_price"] = float(by_line[int(pk["line_id"])]["est_unit_price"]) if int(pk["line_id"]) in by_line else pk.get("actual_unit_price")
    ok, msg = _match(request, cid, None, False, picks=picks, allow_more=bool(more))      # the document that binds us, SI or DR: goods and payable in ONE step, always
    if not ok:
        for a in added:
            remove_added_line(a[0])
        return False, msg, None
    ref = si or f"DR {dr}"
    for ln, q in more:
        flag_delivery(int(ln["po_id"]), "PO_RECEIVED_MORE", f"{q:g} x {ln['item_name']} received on {ref} against {float(ln['remaining']):g} still to come (agreed with the supplier)")
    for _, name, q in added:
        flag_delivery(po, "PO_ITEM_ADDED_AT_DELIVERY", f"{q:g} x {name} came on {ref} and was not on the order")
    notes = []
    for po_id, (pcs, amt, no) in rest.items():
        if not one_dr:
            continue
        if choice == "close":
            ok2, msg2 = close_short(po_id, f"the rest will not come (said when {ref} was recorded)", receiving=True)
            notes.append(msg2)
        else:
            flag_delivery(po_id, "PO_MORE_THAN_ONE_DR", f"{pcs:g} pc(s) (₱{amt:,.2f}) still to come after {ref}; kept open for another DR")
            notes.append(f"{no} stays open for another DR.")
    return True, " ".join([msg] + notes), None


@page_needed("purchase-orders")
def receive(request):
    """ONE page for what arrives (owner, 2026-10-11: "receive goods and match invoice can be combined"; "the important is either DR or SI is recorded"):
      * a delivery -- tick what came and type the SI no. and / or the DR no. (at least one): the goods are received and the payable is recorded under
        that reference, at the price charged. A person who may only receive records the goods on the DR, and Payables records the document;
      * deliveries recorded that way, whose document is not recorded yet -- one form per DR, with what came on it.
    The two switches stay separate: receiving goods (po.receive_goods) and recording payables (po.receive)."""
    from engines.clock import manila_today
    from engines.po_rules import invoiceable_lines, many_drs, pending_receipts, receivable_lines, receive_goods
    may_goods = may_inv = _may_receive(request)                 # one switch (owner, 2026-10-11: "receiving always records the payable")
    if not may_inv:
        from core.menu import PAGES
        return render(request, "core/not_allowed.html", {"page": PAGES["purchase-orders"]}, status=403)
    suppliers = _waiting_suppliers(may_inv)                     # owner, 2026-10-11: "limit only those PO need to receive"
    cid = _supplier_pick(request, suppliers)
    coming = receivable_lines(cid).to_dict("records") if cid else []
    waiting = invoiceable_lines(cid).to_dict("records") if cid and may_inv else []
    pending = pending_receipts(cid) if cid and may_inv else []
    ask = None
    if request.method == "POST":
        p = request.POST
        what = p.get("action")
        if what == "document":                                   # the document of a delivery recorded earlier
            rec = next((r for r in pending if str(r["receipt_id"]) == p.get("receipt_id", "")), None)
            if not rec:
                ok, msg = False, "That delivery already has its document, or recording documents is switched off for you."
            else:
                picks = [{"line_id": ln["line_id"], "qty": ln["qty"], "actual_unit_price": _num(p.get(f"price_{ln['line_id']}"), ln["est_unit_price"])} for ln in rec["lines"]]
                ok, msg = _match(request, cid, None, True, picks=picks, receipt_id=rec["receipt_id"], dr=rec["ref"])
        elif what == "match":                                    # an invoice typed against goods already in (the older form)
            ok, msg = _match(request, cid, waiting, goods_received=True) if may_inv else (False, "Recording invoices is switched off for you.")
        elif what == "goods":                                    # the older goods-only form (kept for links and scripts that still post it)
            ok, msg = (receive_goods(cid, _picks(p, coming, False), _date(p.get("received"), manila_today()), p.get("ref", ""), p.get("kind", "delivery receipt"))[:2]
                       if may_goods else (False, "Receiving goods is switched off for you."))
        else:
            ok, msg, ask = _arrive(request, cid, coming, may_goods, may_inv)
        if ask is None:
            _say(request, ok, msg)
            po = p.get("po", "")
            return redirect(f"/purchase-orders/receive/?s={cid}" + (f"&po={po}" if po and not ok else ""))
    src = request.POST if request.method == "POST" else request.GET
    pre, pre_batch = src.get("po", ""), request.GET.get("batch", "")
    in_pending = {}
    for r in pending:
        for ln in r["lines"]:
            in_pending[ln["line_id"]] = in_pending.get(ln["line_id"], 0.0) + ln["qty"]
    for ln in waiting:                                           # what no listed delivery accounts for (recorded before deliveries kept their items)
        ln["remaining"] = float(ln["remaining"]) - in_pending.get(int(ln["line_id"]), 0.0)
    waiting = [ln for ln in waiting if ln["remaining"] > 1e-6]
    orders_ = sorted({(int(ln["po_id"]), ln["po_no"]) for ln in coming + waiting} | {(r["po_id"], r["po_no"]) for r in pending}, key=lambda x: x[1])
    if not pre and len(orders_) == 1 and request.method == "GET" and "po" not in request.GET:
        pre = str(orders_[0][0])                                 # one order waiting: it is the one that arrived
    if pre.isdigit() and int(pre) in dict(orders_):              # one order chosen: only its lines, already ticked
        coming, waiting = [ln for ln in coming if int(ln["po_id"]) == int(pre)], [ln for ln in waiting if int(ln["po_id"]) == int(pre)]
        pending = [r for r in pending if r["po_id"] == int(pre)]
    else:
        pre = ""
    redo = request.session.get("redo_delivery") if request.method == "GET" else None
    if redo and str(redo.get("po_id")) != str(pre):
        redo = None
    elif redo:
        request.session.pop("redo_delivery", None)
    for ln in coming:
        ln["checked"] = bool(pre) and (not redo or str(ln["line_id"]) in redo["lines"])
    for ln in waiting:
        ln["checked"] = bool(pre)
    return render(request, "buying/receive.html", {"page_id": "purchase-orders", **_head(request), "suppliers": suppliers, "cid": cid, "coming": coming, "waiting": waiting,
                                                   "pending": pending, "may_goods": may_goods, "may_inv": may_inv, "today": manila_today(),
                                                   "may_batch": _can(request, "invoice.enter"), "batches": _batches(cid) if _can(request, "invoice.enter") else [],
                                                   "orders": orders_, "pre": pre, "pre_batch": pre_batch, "came_with": "", "ask": ask, "many": many_drs(), "redo": redo})


@page_needed("purchase-orders")
def match(request):
    """Match invoice was its own page until 2026-10-11; it is part of Receive now. Old links land there; a form still posted here is handled there."""
    if request.method == "POST":
        return receive(request)
    q = request.GET.urlencode()
    return redirect("/purchase-orders/receive/" + (f"?{q}" if q else ""))


@page_needed("purchase-orders")
def order_split(request, po_id: int):
    """Split a draft order into parts (owner, 2026-10-10: "suggest a partition of purchase order"): the suggestion (?n= parts) side by side, editable
    quantities and delivery dates, Recalculate (pay days and day loads, nothing saved), Save the parts; then the parts with "Order all parts"."""
    from engines.po_rules import CHANGED, SPLIT_MAX, get_po, po_stamp, request_po, save_split, split_days, suggest_split
    po = _po_or_404(po_id)
    h = po["header"]
    done = [int(x) for x in request.GET.get("parts", "").split(",") if x.isdigit()]
    if done:                                                   # after saving: the parts, and Order all parts
        if request.method == "POST" and request.POST.get("action") == "send_all":
            if not _can(request, "po.order", h.get("created_by")):
                _say(request, False, "Ordering is switched off for you.")
                return redirect(request.get_full_path())
            over = [i for i in done if _over_limit(i)]
            if over and not request.POST.get("confirm_over"):
                request.session["split_over"] = over
                return redirect(request.get_full_path())
            results = [request_po(i) for i in done]              # each part is its own request; each becomes a PO when the supplier confirms it
            sent = sum(1 for ok, _ in results if ok)
            problems = [msg for ok, msg in results if not ok]
            _say(request, not problems, f"{sent} part(s) sent to the supplier. Download each order request from its page." + (" " + " ".join(problems) if problems else ""))
            return redirect("/purchase-orders/?show=SENT")
        rows = []
        for i in done:
            p = get_po(i)
            if p:
                ph = p["header"]
                info = split_days(int(ph["creditor_id"]), i, [{"delivery": ph["expected_delivery_date"], "amount": p["total"]}])[0] if ph["expected_delivery_date"] else {}
                rows.append({"po_id": i, "ref": ph.get("request_no") or ph["po_no"], "status": ph["status"], "total": p["total"], "delivery": ph["expected_delivery_date"],
                             "notes": ph["notes"], **info})
        return render(request, "buying/order_split.html", {"page_id": "purchase-orders", "po": po, "h": h, "done": rows, "parts_q": request.GET.get("parts"),
                                                           "over_ids": request.session.pop("split_over", None), **_head(request)})
    if h["status"] != "DRAFT" or not _can(request, "po.create", h.get("created_by")):
        _say(request, False, "Only a draft order can be split.")
        return redirect(f"/purchase-orders/{po_id}/")
    n = max(2, min(SPLIT_MAX, int(request.POST.get("n") or request.GET.get("n") or 2)))
    if request.method == "POST":
        p = request.POST
        lines = [int(x) for x in p.getlist("line")]
        parts = []
        for k in range(n):
            qty = {}
            for lid in lines:
                raw = (p.get(f"q_{lid}_{k}") or "").replace(",", "").strip()
                try:
                    qty[lid] = float(raw) if raw else 0.0
                except ValueError:
                    _say(request, False, f"Part {k + 1}: '{raw}' is not a quantity.")
                    return redirect(f"/purchase-orders/{po_id}/split/?n={n}")
            parts.append({"delivery": _date(p.get(f"d_{k}")), "qty": qty})
        if p.get("action") == "save":
            ok, msg, ids = save_split(po_id, parts, p.get("stamp"))
            if not ok and msg == CHANGED:
                _say(request, False, "Someone else changed this order after you opened it. Nothing was saved; this is their version.")
                return redirect(f"/purchase-orders/{po_id}/split/?n={n}")
            _say(request, ok, msg)
            if ok:
                return redirect(f"/purchase-orders/{po_id}/split/?parts={','.join(map(str, ids))}")
        sug = {"lines": suggest_split(po_id, n)["lines"], "total": None}
        by_line = {ln["line_id"]: ln for ln in sug["lines"]}
        shown = []
        for pt in parts:                                           # Recalculate (or a refused save): the edited parts, with their days
            amt = round(sum(q * by_line[lid]["unit_price"] for lid, q in pt["qty"].items() if lid in by_line), 2)
            shown.append({"qty": pt["qty"], "amount": amt, "delivery": pt["delivery"]})
        for pt, info in zip(shown, split_days(int(h["creditor_id"]), po_id, shown)):
            pt.update(info)
        sug["parts"], sug["total"] = shown, round(sum(ln["amount"] for ln in sug["lines"]), 2)
    else:
        sug = suggest_split(po_id, n)
    from engines.day_cap import ceiling, target
    rows = [{**ln, "per": [pt["qty"].get(ln["line_id"], 0) for pt in sug["parts"]]} for ln in sug["lines"]]
    return render(request, "buying/order_split.html", {"page_id": "purchase-orders", "po": po, "h": h, "n": n, "nums": range(2, SPLIT_MAX + 1),
                                                       "rows": rows, "parts": sug["parts"], "total": sug["total"], "stamp": po_stamp(po_id),
                                                       "cap_target": target(), "cap_ceiling": ceiling(), **_head(request)})
