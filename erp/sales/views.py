"""Sales pages (S2; mock-ups approved in S0). Owner and Sales use them; owner only: registering / closing booklets and reviewing
below-cost sales. Every change goes through sales/rules.py or sales/daysheet.py (audited, one locked save)."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from core.models import Party, Role
from core.roles import role_required
from engines.clock import manila_today
from inventory.models import Item
from sales import daysheet, rules
from sales.models import DayUpload, InvoiceBooklet, PaymentType, Sale, SaleLine

BOTH = (Role.OWNER, Role.SALES)
LINES = 6
SESSION_KEY = "sales_day_upload"


def _date(v, default=None):
    try:
        return date.fromisoformat(v) if v else default
    except ValueError:
        return default


# ---- booklets ---------------------------------------------------------------------------------------------------------------------

@role_required(*BOTH)
@require_safe
def booklets(request):
    rows = []
    cur = rules.current_booklet()
    for b in InvoiceBooklet.objects.all().order_by("-first_number"):
        used, cancelled = rules.used_numbers(b), rules.cancelled_numbers(b)
        status = "finished" if b.finished_at else ("in_use" if cur and b.pk == cur.pk else "waiting")
        cells = []
        if status == "in_use":
            top = max(used) if used else None
            for n in range(b.first_number, b.last_number + 1):
                kind = "used" if n in used else "cancelled" if n in cancelled else "gap" if top and n < top else "unused"
                cells.append((b.fmt(n), kind))
        rows.append({"b": b, "status": status, "used": len(used), "cancelled": len(cancelled),
                     "unused": b.size - len(used) - len(cancelled), "cells": cells})
    return render(request, "sales/booklets.html", {"rows": rows, "gaps": [(b.label, b.fmt(n)) for b, n in rules.gaps()], "current": cur,
                                                   "unused": [cur.fmt(n) for n in rules.unused_numbers(cur)] if cur else []})


@role_required(Role.OWNER)
@require_POST
def booklet_register(request):
    try:
        b = rules.register_booklet(request.POST.get("label"), request.POST.get("first"), request.POST.get("last"))
        messages.success(request, f"{b} registered.")
    except rules.SaleError as e:
        messages.error(request, str(e))
    return redirect("sales_booklets")


@role_required(Role.OWNER)
@require_POST
def booklet_close(request, pk):
    b = get_object_or_404(InvoiceBooklet, pk=pk)
    try:
        n = rules.close_booklet(b, request.POST.get("note", ""))
        messages.success(request, f"{b.label} closed; {n} unused number(s) cancelled.")
    except rules.SaleError as e:
        messages.error(request, str(e))
    return redirect("sales_booklets")


@role_required(*BOTH)
@require_POST
def number_cancel(request):
    try:
        c = rules.cancel_number(request.POST.get("number"), request.POST.get("reason", ""))
        messages.success(request, f"{c.booklet.fmt(c.number)} is cancelled.")
    except rules.SaleError as e:
        messages.error(request, str(e))
    return redirect("sales_booklets")


# ---- sales list and detail --------------------------------------------------------------------------------------------------------

@role_required(*BOTH)
@require_safe
def sales_list(request):
    today = manila_today()
    start = _date(request.GET.get("from"), today)
    end = _date(request.GET.get("to"), start)
    if end < start:
        start, end = end, start
    qs = Sale.objects.filter(sale_date__gte=start, sale_date__lte=end).select_related("customer", "booklet")
    by_pay = qs.values("payment_type").annotate(n=Count("id"), total=Sum("total")).order_by("payment_type")
    pieces = SaleLine.objects.filter(sale__in=qs).aggregate(q=Sum("qty"))["q"] or 0
    totals = qs.aggregate(total=Sum("total"), profit=Sum("profit"))
    return render(request, "sales/sales_list.html", {
        "sales": qs.order_by("-sale_date", "-number"), "start": start, "end": end, "pieces": pieces,
        "total": totals["total"] or Decimal("0"), "profit": totals["profit"] or Decimal("0"),
        "by_pay": [{"label": PaymentType(p["payment_type"]).label, "n": p["n"], "total": p["total"]} for p in by_pay],
        "prev": start - timedelta(days=1), "next": start + timedelta(days=1), "today": today,
        "to_review": SaleLine.objects.filter(below_cost=True, reviewed_at__isnull=True).count() if request.user.is_owner else 0,
    })


@role_required(*BOTH)
@require_safe
def sale_detail(request, pk):
    sale = get_object_or_404(Sale.objects.select_related("customer", "booklet", "upload"), pk=pk)
    return render(request, "sales/sale_detail.html", {"sale": sale, "lines": sale.lines.select_related("item")})


# ---- quick sale -------------------------------------------------------------------------------------------------------------------

def _customers_for_sale():
    walk_in = Party.objects.filter(is_walk_in=True)
    others = Party.objects.filter(is_active=True).filter(Q(is_customer=True) | Q(is_affiliate=True)).exclude(is_walk_in=True).order_by("name")
    return list(walk_in) + list(others)


@role_required(*BOTH)
@require_http_methods(["GET", "HEAD", "POST"])
def sale_new(request):
    cur = rules.current_booklet()
    unused = [cur.fmt(n) for n in rules.unused_numbers(cur)] if cur else []
    walk_in = Party.objects.filter(is_walk_in=True).first()
    P = request.POST
    f = {"number": P.get("number", unused[0] if unused else ""), "sale_date": P.get("sale_date", manila_today().isoformat()),
         "customer": P.get("customer", str(walk_in.pk) if walk_in else ""), "customer_note": P.get("customer_note", ""),
         "payment_type": P.get("payment_type", PaymentType.CASH), "check_bank": P.get("check_bank", ""),
         "check_number": P.get("check_number", ""), "check_date": P.get("check_date", ""), "payment_ref": P.get("payment_ref", ""),
         "lines": [(P.get(f"item_{i}", ""), P.get(f"qty_{i}", ""), P.get(f"price_{i}", ""), P.get(f"reason_{i}", "")) for i in range(LINES)]}
    chk = None
    if request.method == "POST":
        try:
            lines = []
            for text, qty, price, reason in f["lines"]:
                if not text.strip() and not qty.strip() and not price.strip():
                    continue
                item = Item.objects.filter(code=text.split("·")[0].strip().upper()).first() if text.strip() else None
                if item is None:
                    raise rules.SaleError(f"{text or 'A line'}: pick the item from the list.")
                lines.append(rules.SaleLineInput(item, qty, price, reason))
            s = rules.SaleInput(number=f["number"], sale_date=_date(f["sale_date"]), customer=Party.objects.filter(pk=f["customer"] or 0).first(),
                                payment_type=f["payment_type"], lines=lines, customer_note=f["customer_note"], check_bank=f["check_bank"],
                                check_number=f["check_number"], check_date=_date(f["check_date"]), payment_ref=f["payment_ref"])
            if P.get("action") == "save":
                sale = rules.record_sale(s)
                messages.success(request, f"Invoice {sale.number_text} saved: PHP {sale.total:,.2f}, profit PHP {sale.profit:,.2f}.")
                return redirect("sales_sale", pk=sale.pk)
            chk = rules.check_sale(s)
        except rules.SaleError as e:
            messages.error(request, str(e))
    return render(request, "sales/sale_form.html", {
        "f": f, "chk": chk, "numbers": unused, "booklet": cur, "customers": _customers_for_sale(), "payments": PaymentType.choices,
        "choices": [daysheet.item_choice(i) for i in Item.objects.filter(is_active=True).order_by("category", "brand", "size_model")],
        "today": manila_today().isoformat(),
    })


# ---- day sheet ----------------------------------------------------------------------------------------------------------------------

@role_required(*BOTH)
@require_safe
def daysheet_template(request):
    day = manila_today()
    resp = HttpResponse(daysheet.build_template(day), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="slicks_day_sheet_{day:%Y-%m-%d}.xlsx"'
    return resp


def _serialise(parsed: dict, file_name: str) -> dict:
    return {"file_name": file_name, "date": parsed["date"].isoformat(), "sales": parsed["sales"], "cancel": parsed["cancel"],
            "total": str(parsed["total"]), "profit": str(parsed["profit"]), "count": parsed["count"], "warnings": parsed["warnings"]}


@role_required(*BOTH)
@require_http_methods(["GET", "HEAD", "POST"])
def daysheet_upload(request):
    parsed = None
    if request.method == "POST" and request.POST.get("action") == "save":
        data = request.session.get(SESSION_KEY)
        if not data:
            messages.error(request, "Upload the day sheet again -- the checked copy expired.")
            return redirect("sales_daysheet")
        try:
            up = daysheet.save_day(data, data["file_name"])
            request.session.pop(SESSION_KEY, None)
            messages.success(request, f"Day saved: {up.sales_count} sale(s), PHP {up.total:,.2f}, profit PHP {up.profit:,.2f}.")
            return redirect(f"/sales/?from={up.sale_date.isoformat()}")
        except rules.SaleError as e:
            request.session.pop(SESSION_KEY, None)
            messages.error(request, f"Nothing was saved: {e} Fix the sheet and upload again.")
            return redirect("sales_daysheet")
    if request.method == "POST" and request.FILES.get("file"):
        upload = request.FILES["file"]
        if upload.size > 5 * 1024 * 1024:
            messages.error(request, "The file is too big for a day sheet (over 5 MB).")
        else:
            parsed = daysheet.parse(upload.read(), upload.name)
            if parsed["errors"]:
                request.session.pop(SESSION_KEY, None)
            else:
                request.session[SESSION_KEY] = _serialise(parsed, upload.name)
    return render(request, "sales/daysheet.html", {"parsed": parsed, "uploads": DayUpload.objects.order_by("-uploaded_at")[:15]})


# ---- below cost review (owner) ------------------------------------------------------------------------------------------------------

@role_required(Role.OWNER)
@require_safe
def below_cost(request):
    lines = SaleLine.objects.filter(below_cost=True).select_related("sale", "sale__customer", "sale__booklet", "item")
    show_all = request.GET.get("all") == "1"
    if not show_all:
        lines = lines.filter(reviewed_at__isnull=True)
    return render(request, "sales/below_cost.html", {"lines": lines.order_by("-sale__sale_date", "-sale__number")[:300], "show_all": show_all})


@role_required(Role.OWNER)
@require_POST
def below_cost_reviewed(request, pk):
    rules.mark_reviewed(get_object_or_404(SaleLine, pk=pk, below_cost=True))
    return redirect("sales_below_cost")
