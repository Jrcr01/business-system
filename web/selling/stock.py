"""Stock > Items and stock (the revenue cycle, stage A; owner, 2026-10-11): what is on hand and at what cost, one item with everything that happened to
it, the low-stock level per item, the System admin's adjustment, labels to print, and what a scan means. The rules are engines/stock_rules.py; this
view draws the pages and passes on what was typed. A person who may only see the page gets no saves (page_needed)."""
from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render

from masters.views import page_needed

SHOWS = [("", "All"), ("tire", "Tires"), ("battery", "Battery"), ("mags", "Mags"), ("rims", "Rims"), ("low", "Low stock"), ("negative", "Below zero"),
         ("old", "Old stock"), ("uncounted", "Not counted in 90 days")]


def _say(request, ok: bool, msg: str) -> None:
    (messages.success if ok else messages.error)(request, msg)


def _can(request, action: str) -> bool:
    from engines.permissions import can
    return can(request.slicks_user["role"], action)


@page_needed("stock")
def stock(request):
    from engines import stock_rules as sr
    q, show = request.GET.get("q", "").strip(), request.GET.get("show", "")
    if q:                                                        # a scanned label (or a typed code) opens that item
        hit = sr.find_by_code(q)
        if hit:
            return redirect(f"/stock/{hit['id']}/")
    everything = sr.stock_list()
    rows = sr.stock_list(q, show if show in dict(SHOWS) else "")
    if request.GET.get("xlsx") == "1":
        import io

        import pandas as pd
        df = pd.DataFrame([{"Code": r["code"], "Item": r["name"], "Type": sr.CATEGORY_LABEL.get(r["category"], r["category"]), "On hand": r["on_hand"],
                            "Next sale costs": r["next_cost"], "Average of what is left": r["avg_cost"], "Value": r["value"], "Low at": r["reorder_level"], "Last in": r["last_in"] or "", "Last out": r["last_out"] or "",
                            "Oldest DOT": r["dot"], "Last counted": r["last_counted_at"] or ""} for r in rows])
        buf = io.BytesIO()
        df.to_excel(buf, index=False, sheet_name="Stock")
        resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = 'attachment; filename="stock.xlsx"'
        return resp
    counts = sr.summary(everything)
    from engines.common import get_conn
    from engines.label_pdf import DEFAULT_SIZE, SIZES
    with get_conn() as conn:
        brands = [r[0] for r in conn.execute("SELECT DISTINCT brand FROM items WHERE is_active = 1 AND brand != '' ORDER BY brand COLLATE NOCASE")]
    tabs = [(k, label + (f" ({counts[k if k != 'old' else 'old_pcs']:g})" if k in ("low", "negative", "uncounted") and counts.get(k) else "")) for k, label in SHOWS]
    return render(request, "selling/stock.html", {"page_id": "stock", "rows": rows[:400], "more": max(len(rows) - 400, 0), "sum": counts, "q": q, "show": show,
                                                  "tabs": tabs, "old_years": sr.old_years(), "brands": brands, "categories": list(sr.CATEGORY_LABEL.items()),
                                                  "label_sizes": [(k, v[0]) for k, v in SIZES.items()], "label_size": request.session.get("label_size", DEFAULT_SIZE)})


@page_needed("stock")
def stock_item(request, item_id: int):
    from engines import stock_rules as sr
    from engines.label_pdf import DEFAULT_SIZE, SIZES
    it = sr.item(item_id)
    if not it:
        raise Http404
    if request.method == "POST":
        what = request.POST.get("action")
        if what == "level":
            ok, msg = sr.set_reorder_level(item_id, request.POST.get("level"))
        elif what == "adjust":
            ok, msg = sr.adjust(item_id, request.POST.get("counted"), request.POST.get("reason", ""))
        elif what == "barcode":
            ok, msg = sr.learn_barcode(request.POST.get("barcode"), item_id)
        else:
            ok, msg = False, "Unknown action."
        _say(request, ok, msg)
        return redirect(f"/stock/{item_id}/")
    from engines.common import get_conn
    with get_conn() as conn:
        barcodes = [r[0] for r in conn.execute("SELECT barcode FROM item_barcodes WHERE item_id = ? ORDER BY created_at", (int(item_id),))]
    from engines.fifo import batches, sync
    if it["on_hand"] > 0 and not batches(item_id):               # stock from before FIFO (2026-10-17): its first batch, at its average cost
        from engines.common import write_tx
        with write_tx() as conn:
            sync(conn, int(item_id))
    return render(request, "selling/stock_item.html", {"page_id": "stock", "it": it, "ledger": sr.ledger(item_id), "batches": batches(item_id), "barcodes": barcodes,
                                                       "may_adjust": _can(request, "inventory.approve"), "sizes": [(k, v[0]) for k, v in SIZES.items()],
                                                       "size": request.session.get("label_size", DEFAULT_SIZE), "type": sr.CATEGORY_LABEL.get(it["category"], it["category"])})


@page_needed("stock")
def labels(request):
    """The label sheet as a PDF: ?item=<id>&n=<copies>, or ?receipt=<id> (one label per piece that came on that delivery), or ?brand=<brand>
    (&category=, &per=onhand | a number of each: a whole brand in one print-out, owner 2026-10-12); &size= one of label_pdf.SIZES.
    The size last chosen is remembered for this person's next print (the owner has not settled on one)."""
    from engines import stock_rules as sr
    from engines.common import get_conn
    from engines.label_pdf import DEFAULT_SIZE, SIZES, labels_pdf
    size = request.GET.get("size") if request.GET.get("size") in SIZES else request.session.get("label_size", DEFAULT_SIZE)
    request.session["label_size"] = size
    rows = []
    if request.GET.get("receipt", "").isdigit():
        with get_conn() as conn:
            got = conn.execute("""SELECT i.code, i.name, x.dot, x.qty FROM po_receipt_lines x JOIN purchase_order_lines l ON l.line_id = x.line_id
                                  JOIN items i ON i.id = l.item_id WHERE x.receipt_id = ? ORDER BY x.receipt_line_id""", (int(request.GET["receipt"]),)).fetchall()
        rows = [{"code": c, "name": n, "dot": sr.dot_text(d), "copies": int(round(float(q)))} for c, n, d, q in got]
    elif request.GET.get("item", "").isdigit():
        it = sr.item(int(request.GET["item"]))
        if it:
            try:
                n = max(1, min(int(request.GET.get("n") or 1), 500))
            except ValueError:
                n = 1
            rows = [{"code": it["code"], "name": it["name"], "dot": it["dot"], "copies": n}]
    elif request.GET.get("brand", "").strip():
        brand, category, per = request.GET["brand"].strip(), request.GET.get("category", ""), request.GET.get("per", "onhand")
        try:
            each = max(1, min(int(request.GET.get("n") or 1), 50))
        except ValueError:
            each = 1
        with get_conn() as conn:
            got = conn.execute("""SELECT code, name, oldest_dot, on_hand FROM items WHERE is_active = 1 AND lower(brand) = lower(?) AND (? = '' OR category = ?)
                                  ORDER BY name COLLATE NOCASE""", (brand, category, category)).fetchall()
        for code, name, dot, on_hand in got:
            copies = int(round(float(on_hand or 0))) if per == "onhand" else each
            if copies > 0:
                rows.append({"code": code, "name": name, "dot": sr.dot_text(dot) if float(on_hand or 0) > 0 else "", "copies": copies})
        total = sum(r["copies"] for r in rows)
        if not rows:
            why = "nothing of it is on hand: choose 'this many of each' to print labels anyway" if got and per == "onhand" else "no item of that brand"
            return HttpResponse(f"No label to print for {brand}: {why}.", content_type="text/plain; charset=utf-8", status=404)
        if total > 3000:
            return HttpResponse(f"That is {total:,} labels in one print-out. Choose a type as well, or fewer of each.", content_type="text/plain; charset=utf-8", status=400)
    if not rows:
        raise Http404
    resp = HttpResponse(labels_pdf(rows, size), content_type="application/pdf")
    resp["Content-Disposition"] = 'inline; filename="labels.pdf"'
    return resp


def _any_stock_page(request) -> bool:
    """The scan box is on several pages: any person who sees one of them may ask what a code means."""
    from core.menu import pages_for
    from engines.features import revenue_on
    return revenue_on() and bool({"stock", "stock-counts", "affiliates", "sales", "purchase-orders"} & pages_for(request.slicks_user["role"]))


def picker_items(request):
    """Every active item for the scan-and-search box (engines/stock_rules.picker_items)."""
    if not _any_stock_page(request):
        return JsonResponse({"items": []}, status=403)
    from engines.stock_rules import picker_items as items
    return JsonResponse({"items": items()})


def learn(request):
    """An unknown barcode was scanned and the person said which item it is: remembered from now on."""
    if request.method != "POST" or not _any_stock_page(request):
        return JsonResponse({"ok": False, "msg": "Not allowed."}, status=403)
    from engines.stock_rules import learn_barcode
    try:
        item_id = int(request.POST.get("item_id") or 0)
    except ValueError:
        item_id = 0
    ok, msg = learn_barcode(request.POST.get("barcode"), item_id)
    return JsonResponse({"ok": ok, "msg": msg})
