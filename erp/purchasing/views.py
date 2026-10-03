"""Purchasing screens (D2, mock-ups approved 2026-10-02): price lists, purchase orders, receiving a supplier invoice. Every rule is in engines/ (the same rules the
Streamlit app used); a refusal comes back as a message and nothing is saved. The role is passed to the rules by the middleware, so a button shown by mistake is
still refused by engines/permissions."""
import base64
import json
import sqlite3
from datetime import date, datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.db import connection
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from core.models import Creditor, Party, Role
from core.roles import role_required
from engines import discount_rules as dr
from engines import po_rules, price_template_service as pt
from engines import product_catalog as pc
from engines.acting_user import acting_email, acting_role
from engines.clock import manila_today
from engines.disbursement_trail_service import trail_for_po
from engines.permissions import can
from engines.po_pdf import build_po_pdf
from engines.price_list_service import current_prices

SEE = (Role.OWNER, Role.PURCHASING, Role.PAYABLES, Role.VIEWER)
LINES = 10
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PO_STATUS = {"DRAFT": "Draft", "ORDERED": "Ordered", "PARTIAL": "Partly received", "RECEIVED": "Received", "CANCELLED": "Cancelled"}


def _suppliers():
    return Creditor.objects.filter(creditor_type="SUPPLIER", is_active=1).order_by("creditor_name")


def _pick_supplier(request):
    """The supplier chosen on this page (GET/POST 'supplier'), or the first one."""
    suppliers = list(_suppliers())
    raw = request.POST.get("supplier") or request.GET.get("supplier") or ""
    chosen = next((s for s in suppliers if str(s.creditor_id) == raw), suppliers[0] if suppliers else None)
    return suppliers, chosen


def _date(text, default=None):
    try:
        return date.fromisoformat((text or "").strip())
    except ValueError:
        return default


def _money(v):
    return None if v is None else round(float(v), 2)


# ---- price lists ---------------------------------------------------------------------------------------------------------------

@role_required(*SEE)
@require_http_methods(["GET", "HEAD", "POST"])
def price_lists(request):
    suppliers, sup = _pick_supplier(request)
    tab = request.POST.get("tab") or request.GET.get("tab") or "current"
    writer = can(acting_role(), "price.write")
    ctx = {"suppliers": suppliers, "sup": sup, "tab": tab, "writer": writer, "today": manila_today().isoformat()}
    if sup is None:
        return render(request, "purchasing/price_lists.html", ctx)
    cid = sup.creditor_id

    if request.method == "POST" and tab == "discounts":
        ok, msg = dr.set_discount(cid, request.POST.get("brand", ""), request.POST.get("chain", ""), _date(request.POST.get("start"), manila_today()))
        (messages.success if ok else messages.error)(request, msg)
        return redirect(f"{request.path}?supplier={cid}&tab=discounts")

    if request.method == "POST" and tab == "import":
        return _import_step(request, ctx, cid)

    if tab == "current":
        on = _date(request.GET.get("on"), manila_today())
        names = {str(r.name).lower(): r.code for r in pc.list_products(active_only=False).itertuples()}
        rows = []
        for r in current_prices(cid, on=on).itertuples():
            net = dr.net_price_on(cid, r.item_name, on) or {}
            prev = r.previous_price
            rows.append({"code": names.get(str(r.item_name).lower(), ""), "item": r.item_name, "list": r.unit_price, "chain": dr.chain_label(net.get("chain", "")),
                         "net": net.get("net_price", r.unit_price), "since": r.effective_date,
                         "change": None if prev is None or prev != prev or prev <= 0 else round((r.unit_price - prev) / prev * 100, 1)})
        ctx.update(rows=rows, on=on.isoformat())
    elif tab == "side":
        category = request.GET.get("category") or ""
        df = pc.supplier_prices(category=category or None)
        ctx.update(side=df.to_dict("records"), category=category, categories=pc.CATEGORIES)
    elif tab == "discounts":
        cur = dr.list_discounts(cid)
        ctx.update(discounts=cur.to_dict("records"), brands=pc.brands())
    return render(request, "purchasing/price_lists.html", ctx)


def _import_step(request, ctx, cid):
    """Excel import in two steps: 'preview' reads the uploaded file and shows what would change (nothing saved); 'confirm' saves it."""
    step = request.POST.get("step")
    if not can(acting_role(), "price.write"):
        messages.error(request, "Your role can look at prices but not change them.")
        return redirect(f"{request.path}?supplier={cid}&tab=import")
    if step == "preview":
        upload = request.FILES.get("file")
        if upload is None:
            messages.error(request, "Choose the filled template (.xlsx) first.")
            return redirect(f"{request.path}?supplier={cid}&tab=import")
        data = upload.read()
        start = _date(request.POST.get("start"), manila_today())
        request.session["pl_import"] = {"cid": cid, "file": base64.b64encode(data).decode(), "start": start.isoformat(), "name": upload.name}
        parsed = pt.parse_workbook(data, default_date=start)
        cmp = pt.compare(cid, parsed)
        ctx.update(preview=True, parsed_counts=parsed["counts"], problems=parsed["problems"], n_rows=len(parsed["rows"]), cmp=cmp.to_dict("records"),
                   summary=cmp["status"].value_counts().to_dict(), fname=upload.name, start=start.isoformat())
        return render(request, "purchasing/price_lists.html", ctx)
    if step == "confirm":
        saved = request.session.pop("pl_import", None)
        if not saved or saved.get("cid") != cid:
            messages.error(request, "The uploaded file is no longer available. Upload it again.")
            return redirect(f"{request.path}?supplier={cid}&tab=import")
        parsed = pt.parse_workbook(base64.b64decode(saved["file"]), default_date=_date(saved["start"]))
        ok, msg, _ = pt.import_workbook(cid, parsed, "replace" if request.POST.get("dup") == "replace" else "skip")
        (messages.success if ok else messages.error)(request, msg)
    return redirect(f"{request.path}?supplier={cid}&tab=import")


@role_required(*SEE)
@require_safe
def price_template(request):
    """The Excel template, blank or with this supplier's current prices. (Made when asked.)"""
    cid = request.GET.get("supplier")
    data = pt.build_template(int(cid)) if cid and cid.isdigit() and request.GET.get("filled") == "1" else pt.build_template()
    name = "price_list_template.xlsx" if not request.GET.get("filled") else f"price_list_{cid}.xlsx"
    resp = HttpResponse(data, content_type=XLSX)
    resp["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp


# ---- purchase orders -------------------------------------------------------------------------------------------------------------

@role_required(*SEE)
@require_safe
def po_list(request):
    scope = request.GET.get("show", "open")
    status = {"draft": "DRAFT", "ordered": "ORDERED", "partial": "PARTIAL", "received": "RECEIVED", "cancelled": "CANCELLED"}.get(scope)
    df = po_rules.list_pos(status=status)
    if scope == "open":
        df = df[df["status"].isin(["DRAFT", "ORDERED", "PARTIAL"])]
    rows = [dict(r, status_text=PO_STATUS.get(r["status"], r["status"])) for r in df.to_dict("records")]
    role = acting_role()
    return render(request, "purchasing/po_list.html", {"rows": rows, "show": scope, "can_create": can(role, "po.create", acting_email(), acting_email()),
                                                      "total": sum(r["total"] for r in rows), "late": sum(1 for r in rows if r.get("delivery_overdue"))})


def _item_options():
    return [f"{r.code} · {r.name}" for r in pc.list_products().itertuples()]


def _lines_from_post(post):
    lines = []
    for i in range(LINES):
        text = (post.get(f"item_{i}") or "").strip()
        if not text:
            continue
        name = text.split("·", 1)[1].strip() if "·" in text else text
        price = (post.get(f"price_{i}") or "").replace(",", "").strip()
        disc = (post.get(f"disc_{i}") or "").strip()
        lines.append({"item_name": name, "unit": "", "qty": (post.get(f"qty_{i}") or "").replace(",", "").strip() or 0,
                      "list_price": float(price) if price.replace(".", "", 1).isdigit() else None,
                      "discount_chain": None if disc == "" else ("" if disc in ("0", "0%") else disc)})
    return lines


def _lines_for_form(po):
    out = []
    for ln in po["lines"].itertuples():
        listed = ln.list_price if ln.list_price == ln.list_price and ln.list_price is not None else ln.est_unit_price
        chain = (ln.discount_chain or "") if ln.list_price == ln.list_price and ln.list_price is not None else ""
        out.append({"item": ln.item_name, "qty": f"{ln.qty:g}", "price": f"{listed:.2f}", "disc": chain.replace(",", ", ") or "0"})
    return out


def _panel(cid, order_date, delivery, edit_id, estimate, daily):
    """The 'payables when this order falls due' panel (weekly strip, daily bars, that week's list)."""
    pay = po_rules.expected_pay_date(cid, delivery)
    if pay is None:
        return None
    strip = po_rules.weeks_strip(pay, exclude_po_id=edit_id)
    top = max(float(strip["total"].max()), 1.0)
    weeks = [{"start": r.week_start.isoformat(), "label": f"{r.week_start:%b %d}", "total": r.total, "h": max(4, round(r.total / top * 100)), "sel": bool(r.selected),
              "low": bool(r.lightest), "reachable": po_rules.delivery_for_week(cid, r.week_start, order_date) is not None} for r in strip.itertuples()]
    days = []
    if daily:
        d = po_rules.days_strip(pay, exclude_po_id=edit_id)
        dtop = max(float(d["total"].max()), 1.0)
        days = [{"label": f"{r.day:%b %d} {r.day:%a}", "total": r.total, "h": max(3, round(r.total / dtop * 100)), "sel": bool(r.selected), "low": bool(r.lightest)} for r in d.itertuples()]
    sched = po_rules.week_schedule(pay, exclude_po_id=edit_id)
    hide = acting_role() not in (Role.OWNER, Role.PAYABLES)                      # check numbers stay with the roles that handle checks
    return {"pay": pay, "weeks": weeks, "days": days, "daily": daily, "sched_total": sched["total"], "week_start": sched["week_start"], "week_end": sched["week_end"],
            "sched_rows": [{"day": r.day, "payee": r.payee, "kind": r.kind, "reference": "" if hide else r.reference, "amount": r.amount} for r in sched["rows"].itertuples()],
            "adds": estimate, "becomes": sched["total"] + estimate}


@role_required(*SEE)
@require_http_methods(["GET", "HEAD", "POST"])
def po_form(request, pk=None):
    role, me = acting_role(), acting_email()
    existing = po_rules.get_po(pk) if pk else None
    if pk and existing is None:
        raise Http404
    if not can(role, "po.create", existing["header"]["created_by"] if existing else me, me):
        messages.error(request, "Your role can't make or change purchase orders" + (" that someone else made." if existing else "."))
        return redirect("po_list")
    suppliers = list(_suppliers())
    today = manila_today()
    if request.method == "POST":
        action = request.POST.get("action", "refresh")
        cid = int(request.POST.get("supplier") or 0) or (suppliers[0].creditor_id if suppliers else 0)
        order_date = _date(request.POST.get("order_date"), today)
        delivery = _date(request.POST.get("delivery"))
        notes = request.POST.get("notes", "")
        daily = request.POST.get("view") == "daily"
        lines = _lines_from_post(request.POST)
        f_lines = [{"item": request.POST.get(f"item_{i}", ""), "qty": request.POST.get(f"qty_{i}", ""), "price": request.POST.get(f"price_{i}", ""),
                    "disc": request.POST.get(f"disc_{i}", "")} for i in range(LINES)]
        if action.startswith("week:"):
            moved = po_rules.delivery_for_week(cid, _date(action[5:]), order_date)
            if moved:
                delivery = moved
            else:
                messages.error(request, "That week is before this order could be paid.")
        elif action in ("save", "save_order"):
            ok, msg, po_id = po_rules.save_po(pk, cid, order_date, delivery, lines, notes)
            if ok and action == "save_order":
                ok, msg = po_rules.order_po(po_id)
                if not ok:
                    messages.warning(request, f"Saved as a draft, but not ordered: {msg}")
                    return redirect("po_detail", pk=po_id)
            if ok:
                messages.success(request, msg if action == "save" else "Ordered. The PDF is ready to download.")
                return redirect("po_detail", pk=po_id)
            messages.error(request, msg)
        form = {"supplier": cid, "order_date": order_date.isoformat(), "delivery": delivery.isoformat() if delivery else "", "notes": notes, "lines": f_lines, "daily": daily}
    elif existing:
        h = existing["header"]
        form = {"supplier": int(h["creditor_id"]), "order_date": str(h["order_date"])[:10], "delivery": str(h["expected_delivery_date"] or "")[:10], "notes": h["notes"] or "",
                "lines": [{"item": l["item"], "qty": l["qty"], "price": l["price"], "disc": l["disc"]} for l in _lines_for_form(existing)] + [{}] * LINES, "daily": False}
        form["lines"] = form["lines"][:max(LINES, len(_lines_for_form(existing)))]
    else:
        cid = int(request.GET.get("supplier") or 0) or (suppliers[0].creditor_id if suppliers else 0)
        form = {"supplier": cid, "order_date": today.isoformat(), "delivery": po_rules.default_delivery(cid, today).isoformat() if cid else "", "notes": "",
                "lines": [{}] * LINES, "daily": False}
    cid, order_date, delivery = form["supplier"], _date(form["order_date"], today), _date(form["delivery"])
    clean, problem = po_rules._clean_lines(cid, order_date, _lines_from_post(request.POST)) if request.method == "POST" else (None, "")
    if clean is None and existing and request.method != "POST":
        clean, problem = po_rules._clean_lines(cid, order_date, [{"item_name": l["item"], "qty": l["qty"], "list_price": float(l["price"]), "discount_chain": "" if l["disc"] in ("0", "") else l["disc"]}
                                                                  for l in _lines_for_form(existing)])
    preview = [{"item": c["item_name"], "qty": c["qty"], "net": c["est_unit_price"], "amount": c["est_amount"], "chain": dr.chain_label(c["discount_chain"] or "")} for c in (clean or [])]
    estimate = sum(p["amount"] for p in preview)
    panel = _panel(cid, order_date, delivery, pk, estimate, form.get("daily")) if cid and delivery else None
    return render(request, "purchasing/po_form.html", {"suppliers": suppliers, "f": form, "edit": existing, "items": _item_options(), "panel": panel, "preview": preview,
                                                       "estimate": estimate, "cid": cid, "problem": problem if request.method == "POST" else ""})


@role_required(*SEE)
@require_http_methods(["GET", "HEAD", "POST"])
def po_detail(request, pk):
    po = po_rules.get_po(pk)
    if po is None:
        raise Http404
    h = po["header"]
    role, me = acting_role(), acting_email()
    owner_of = h.get("created_by")
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "order":
            ok, msg = po_rules.order_po(pk)
        elif action == "cancel":
            ok, msg = po_rules.cancel_po(pk, request.POST.get("reason", ""))
        elif action == "close_short":
            ok, msg = po_rules.close_short(pk, request.POST.get("reason", ""))
        else:
            ok, msg = False, "Unknown action."
        (messages.success if ok else messages.error)(request, msg)
        return redirect("po_detail", pk=pk)
    status = h["status"]
    trail = trail_for_po(pk) if status != "DRAFT" else None
    return render(request, "purchasing/po_detail.html", {
        "po": po, "h": h, "status_text": PO_STATUS.get(status, status), "lines": po["lines"].to_dict("records"), "trail": trail,
        "may_order": can(role, "po.order", owner_of, me), "may_edit": can(role, "po.create", owner_of, me) and status in ("DRAFT", "ORDERED"),
        "may_cancel": can(role, "po.cancel", owner_of, me), "may_receive": can(role, "po.receive") and status in ("ORDERED", "PARTIAL"),
        "can_pdf": status in ("ORDERED", "PARTIAL", "RECEIVED") and can(role, "po.order", owner_of, me)})


@role_required(*SEE)
@require_safe
def po_pdf(request, pk):
    pdf = build_po_pdf(pk)
    if not pdf:
        raise Http404("This order has no PDF (not ordered yet, or not yours to send).")
    po = po_rules.get_po(pk)
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{str(po["header"]["po_no"]).replace(" ", "_")}.pdf"'
    return resp


# ---- receiving -------------------------------------------------------------------------------------------------------------------

def _open_batches(cid):
    with connection.cursor() as cur:
        cur.execute("SELECT statement_id, statement_ref FROM order_statements WHERE creditor_id = %s AND status != 'Cancelled' AND is_locked = 0 ORDER BY statement_id DESC", [cid])
        return cur.fetchall()


def _book_stock(cid, doc_ref, received_on, picks):
    """Goods received go into stock at the price actually charged (weighted average). Lines that are not catalog items (free text) are not stocked."""
    from inventory import stock
    from inventory.models import Item

    from core.parties import sync_ap_links

    sync_ap_links()                                                 # every AP supplier is also a party (the stock-in supplier list)
    party = Party.objects.filter(ap_links__creditor_id=cid).first()
    if party is None:
        return "Stock not booked: this supplier is not on the Parties list yet."
    with connection.cursor() as cur:
        ids = ",".join(str(int(p["line_id"])) for p in picks)
        cur.execute(f"SELECT line_id, item_id FROM purchase_order_lines WHERE line_id IN ({ids})")
        item_of = dict(cur.fetchall())
    lines = {}
    for p in picks:
        item = Item.objects.filter(pk=item_of.get(int(p["line_id"]))).first() if item_of.get(int(p["line_id"])) else None
        if item and item.is_active and float(p["qty"]) == int(float(p["qty"])):
            qty, cost = lines.get(item.pk, (item, 0, 0))[1:]
            total_cost = (cost * qty) + float(p["actual_unit_price"]) * float(p["qty"])
            qty += int(float(p["qty"]))
            lines[item.pk] = (item, qty, round(total_cost / qty, 4))
    if not lines:
        return "No stock booked: none of these lines is a catalog item (or the quantity is not a whole number)."
    try:
        doc = stock.record_stock_in(party, received_on, doc_ref, list(lines.values()))
    except stock.StockError as e:
        return f"Invoice saved, but stock was not booked: {e}"
    return f"Stock booked as {doc.number}."


@role_required(Role.OWNER, Role.PAYABLES)
@require_http_methods(["GET", "HEAD", "POST"])
def receive(request):
    suppliers, sup = _pick_supplier(request)
    ctx = {"suppliers": suppliers, "sup": sup, "today": manila_today().isoformat()}
    if sup is None:
        return render(request, "purchasing/receive.html", ctx)
    cid = sup.creditor_id
    lines = po_rules.receivable_lines(cid)
    rows = lines.to_dict("records")
    pre = request.GET.get("po")
    for r in rows:
        r["ticked"] = bool(pre and str(r["po_id"]) == pre)
    if request.method == "POST" and request.POST.get("step") == "receive":
        picks = []
        for r in rows:
            if request.POST.get(f"pick_{r['line_id']}"):
                picks.append({"line_id": r["line_id"], "qty": request.POST.get(f"qty_{r['line_id']}") or r["remaining"],
                              "actual_unit_price": (request.POST.get(f"price_{r['line_id']}") or str(r["est_unit_price"])).replace(",", "")})
        batch = int(request.POST.get("batch") or 0)
        if batch == 0 and picks:
            from engines.statement_rules import create_order_statement
            batch = create_order_statement(cid)
        gross = (request.POST.get("gross") or "").replace(",", "").strip()
        received_on = manila_today()
        ok, msg, inv = po_rules.receive_into_batch(cid, batch, request.POST.get("invoice_no", ""), _date(request.POST.get("invoice_date")), picks,
                                                   update_prices=request.POST.get("update_prices") == "on", received_date=received_on,
                                                   gross_override=float(gross) if gross else None, book_stock=False)
        if ok:
            messages.success(request, msg)
            messages.info(request, _book_stock(cid, request.POST.get("invoice_no", ""), received_on, picks))
            return redirect(f"{request.path}?supplier={cid}")
        messages.error(request, msg)
        for r in rows:
            r["ticked"] = bool(request.POST.get(f"pick_{r['line_id']}"))
    ctx.update(rows=rows, batches=_open_batches(cid), f=request.POST if request.method == "POST" else {})
    return render(request, "purchasing/receive.html", ctx)
