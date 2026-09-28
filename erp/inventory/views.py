"""Items and stock pages (Sales step S1, mock-ups approved 2026-09-29). Owner and Sales use them; owner only: adjustments, item
settings, reversing a stock in, confirming / discarding the opening list. Every change goes through inventory/stock.py (audited)."""
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.db.models import Case, Max, Q, Value, When
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from core.models import Party, Role
from core.roles import role_required
from engines.clock import manila_today
from inventory import opening, stock
from inventory.models import SIZE_LABEL, Category, ImportStatus, Item, MoveKind, OpeningImport, StockIn, StockMove

LINES = 8
PLURAL = {Category.TIRE: "Tires", Category.BATTERY: "Batteries", Category.MAGS: "Mags"}
BOTH = (Role.OWNER, Role.SALES)


def _suppliers():
    """Active AP suppliers (inactive in AP = not offered)."""
    return Party.objects.filter(ap_links__creditor__creditor_type="SUPPLIER", ap_links__creditor__is_active=1,
                                is_active=True).distinct().order_by("name")


def _item_choice(item: Item) -> str:
    return f"{item.code} · {item.description}"


def _item_from_choice(text: str) -> Item | None:
    code = (text or "").split("·")[0].strip().upper()
    return Item.objects.filter(code=code).first() if code else None


# ---- items ------------------------------------------------------------------------------------------------------------------------

@role_required(*BOTH)
@require_safe
def items(request):
    show, q = request.GET.get("show", ""), (request.GET.get("q") or "").strip()
    qs = Item.objects.filter(is_active=True) if request.GET.get("inactive") != "1" else Item.objects.all()
    if show in Category.values:
        qs = qs.filter(category=show)
    if q:
        for word in q.split():
            qs = qs.filter(Q(brand__icontains=word) | Q(size_model__icontains=word) | Q(remark__icontains=word) | Q(code__icontains=word))
    order = Case(*[When(category=c, then=Value(i)) for i, c in enumerate(Category.values)])     # tires first
    rows = list(qs.annotate(cat_order=order).order_by("cat_order", "brand", "size_model", "remark"))
    if show == "low":
        rows = [i for i in rows if i.is_low or i.is_negative]
    last_in = {m["item"]: m["last"] for m in StockMove.objects.filter(kind__in=[MoveKind.STOCK_IN, MoveKind.OPENING])
               .values("item").annotate(last=Max("moved_on"))}
    flagged = Item.objects.filter(on_hand__lt=0, is_active=True).count()
    return render(request, "inventory/items.html", {
        "rows": [{"i": i, "last_in": last_in.get(i.pk)} for i in rows], "show": show, "q": q, "categories": PLURAL.items(),
        "inactive": request.GET.get("inactive") == "1", "flagged": flagged,
        "pending_opening": OpeningImport.objects.filter(status=ImportStatus.PENDING).count(),
    })


@role_required(*BOTH)
@require_http_methods(["GET", "HEAD", "POST"])
def item_add(request):
    data = {"category": request.POST.get("category", Category.TIRE), "brand": request.POST.get("brand", ""),
            "size_model": request.POST.get("size_model", ""), "remark": request.POST.get("remark", "")}
    if request.method == "POST":
        try:
            item = stock.add_item(data["category"], data["brand"], data["size_model"], data["remark"])
            messages.success(request, f"{item.code} {item.description} added (no stock yet -- record a stock in).")
            return redirect("inv_item", pk=item.pk)
        except stock.StockError as e:
            messages.error(request, str(e))
    return render(request, "inventory/item_add.html", {"d": data, "categories": Category.choices, "size_label": SIZE_LABEL})


@role_required(*BOTH)
@require_safe
def item_detail(request, pk):
    item = get_object_or_404(Item, pk=pk)
    moves = item.moves.select_related("stock_in")[:200]
    return render(request, "inventory/item_detail.html", {"item": item, "moves": moves, "size_label": SIZE_LABEL[item.category]})


@role_required(Role.OWNER)
@require_POST
def item_adjust(request, pk):
    item = get_object_or_404(Item, pk=pk)
    try:
        move = stock.adjust_to_count(item, request.POST.get("counted", ""), request.POST.get("reason", ""))
        messages.success(request, "No change: the count equals the stock on hand." if move is None
                         else f"Adjusted {move.qty:+d}: now {move.on_hand_after} on hand.")
    except stock.StockError as e:
        messages.error(request, str(e))
    return redirect("inv_item", pk=pk)


@role_required(Role.OWNER)
@require_POST
def item_settings(request, pk):
    from django.db import transaction

    from core import audit

    item = get_object_or_404(Item, pk=pk)
    try:
        level = int(request.POST.get("reorder_level", item.reorder_level))
        if level < 0:
            raise ValueError
    except ValueError:
        messages.error(request, "The low-stock level is a whole number, 0 or more.")
        return redirect("inv_item", pk=pk)
    active = request.POST.get("is_active") == "on"
    changed = {k: v for k, v in (("reorder_level", level), ("is_active", active)) if getattr(item, k) != v}
    if changed:
        with transaction.atomic():
            old = "; ".join(f"{k}: {getattr(item, k)}" for k in changed)
            for k, v in changed.items():
                setattr(item, k, v)
            item.save(update_fields=list(changed))
            audit.log("INV_ITEM_SETTINGS", "inventory_item", item.pk, old=old, new="; ".join(f"{k}: {v}" for k, v in changed.items()))
        messages.success(request, "Saved.")
    return redirect("inv_item", pk=pk)


# ---- stock in ---------------------------------------------------------------------------------------------------------------------

@role_required(*BOTH)
@require_safe
def stock_ins(request):
    return render(request, "inventory/stock_ins.html", {"docs": StockIn.objects.select_related("supplier")[:200]})


@role_required(*BOTH)
@require_http_methods(["GET", "HEAD", "POST"])
def stock_in_new(request):
    form = {"supplier": request.POST.get("supplier", ""), "received_on": request.POST.get("received_on", manila_today().isoformat()),
            "supplier_ref": request.POST.get("supplier_ref", ""),
            "lines": [(request.POST.get(f"item_{i}", ""), request.POST.get(f"qty_{i}", ""), request.POST.get(f"cost_{i}", ""))
                      for i in range(LINES)]}
    preview = None
    if request.method == "POST":
        try:
            supplier = _suppliers().filter(pk=form["supplier"] or 0).first()
            try:
                received_on = date.fromisoformat(form["received_on"])
            except ValueError:
                raise stock.StockError("Give the date received as YYYY-MM-DD.")
            lines = []
            for text, qty, cost in form["lines"]:
                if not text.strip() and not qty.strip() and not cost.strip():
                    continue
                item = _item_from_choice(text)
                if item is None:
                    raise stock.StockError(f"{text or 'A line'}: pick an item from the list (add it first if it's new).")
                if not qty.strip() or not cost.strip():
                    raise stock.StockError(f"{item.code}: give both the quantity and the cost.")
                lines.append((item, qty, cost))
            if request.POST.get("action") == "save":
                doc = stock.record_stock_in(supplier, received_on, form["supplier_ref"], lines)
                messages.success(request, f"{doc.number} saved: {len(lines)} line(s), PHP {doc.total:,.2f}.")
                return redirect("inv_stock_in", pk=doc.pk)
            preview = stock.check_stock_in(supplier, received_on, lines)
        except stock.StockError as e:
            messages.error(request, str(e))
    return render(request, "inventory/stock_in_form.html", {
        "f": form, "suppliers": _suppliers(), "preview": preview, "today": manila_today().isoformat(),
        "choices": [_item_choice(i) for i in Item.objects.filter(is_active=True)],
        "total": sum((p["amount"] for p in preview), Decimal("0")) if preview else None,
    })


@role_required(*BOTH)
@require_safe
def stock_in_detail(request, pk):
    doc = get_object_or_404(StockIn.objects.select_related("supplier"), pk=pk)
    return render(request, "inventory/stock_in_detail.html", {"doc": doc, "moves": doc.moves.select_related("item").order_by("id")})


@role_required(Role.OWNER)
@require_POST
def stock_in_reverse(request, pk):
    doc = get_object_or_404(StockIn, pk=pk)
    try:
        stock.reverse_stock_in(doc, request.POST.get("reason", ""))
        messages.success(request, f"{doc.number} is reversed; its pieces left the stock at their own cost.")
    except stock.StockError as e:
        messages.error(request, str(e))
    return redirect("inv_stock_in", pk=pk)


# ---- opening list -----------------------------------------------------------------------------------------------------------------

@role_required(*BOTH)
@require_safe
def opening_template(request):
    resp = HttpResponse(opening.build_template(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = 'attachment; filename="slicks_opening_price_list.xlsx"'
    return resp


@role_required(*BOTH)
@require_http_methods(["GET", "HEAD", "POST"])
def opening_list(request):
    result = None
    if request.method == "POST" and request.FILES.get("file"):
        from django.db import transaction

        from core import audit
        from engines.acting_user import acting_user

        upload = request.FILES["file"]
        if upload.size > 5 * 1024 * 1024:
            messages.error(request, "The file is too big for a price list (over 5 MB).")
        else:
            result = opening.parse(upload.read())
            if not result["errors"]:
                if OpeningImport.objects.filter(status=ImportStatus.PENDING).exists():
                    messages.error(request, "Another list is waiting for the owner. Confirm or discard it first.")
                else:
                    with transaction.atomic():
                        imp = OpeningImport.objects.create(file_name=upload.name[:200], rows=result["rows"],
                                                           items_count=result["items_count"], pieces=result["pieces"],
                                                           value=result["value"], uploaded_by=acting_user())
                        audit.log("INV_OPENING_UPLOADED", "inventory_openingimport", imp.pk,
                                  new=f"{imp.file_name}: {imp.items_count} rows, {imp.pieces} pcs, PHP {imp.value:,.2f}")
                    messages.success(request, "The list is checked and waiting for the owner to confirm the totals.")
                    return redirect("inv_opening")
    return render(request, "inventory/opening.html", {"result": result, "imports": OpeningImport.objects.all()[:20]})


@role_required(Role.OWNER)
@require_POST
def opening_decide(request, pk):
    imp = get_object_or_404(OpeningImport, pk=pk)
    try:
        if request.POST.get("decision") == "confirm":
            r = stock.confirm_opening(imp)
            messages.success(request, f"Opening stock recorded: {r['created']} new item(s), {r['opened']} opened"
                             + (f"; {len(r['skipped'])} already had stock and were left alone." if r["skipped"] else "."))
        else:
            stock.discard_opening(imp, request.POST.get("reason", ""))
            messages.success(request, "The list is discarded; nothing was recorded.")
    except stock.StockError as e:
        messages.error(request, str(e))
    return redirect("inv_opening")
