"""Stage D2a (Price lists, docs/mockups/d2_buying.html screens 1-3, approved 2026-10-03): one supplier's prices with the last-invoice check, add a price, Excel import
(check first, then import), discounts, and the catalog with every supplier's price side by side. The rules are in engines/ (price_list_service, discount_rules,
price_reconcile, price_template_service, product_catalog); these views only draw the forms and pass on what was typed."""
from __future__ import annotations

import secrets
import time
from datetime import date
from pathlib import Path

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render

from masters.views import page_needed

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
STATUS_TEXT = {"new": "new", "changed": "changed", "same": "same", "missing": "not in file"}


def _uploads() -> Path:
    """An uploaded price file waits here between "Check the file" and "Import": beside the database, never in git."""
    import engines.db_engine as d
    return Path(d.DB_PATH).resolve().parent / "web_uploads"


def _suppliers(with_prices: bool = False) -> list[tuple[int, str]]:
    """Active suppliers. with_prices: only those with a price list (an active price row), for a new purchase order (owner, 2026-10-08)."""
    from engines.common import get_conn
    has = " AND EXISTS (SELECT 1 FROM supplier_price_list p WHERE p.creditor_id = c.creditor_id AND p.is_active = 1)" if with_prices else ""
    with get_conn() as conn:
        return [(int(r[0]), r[1]) for r in conn.execute(f"SELECT creditor_id, creditor_name FROM creditors c WHERE creditor_type = 'SUPPLIER' AND is_active = 1{has} ORDER BY creditor_name")]


def _date(text, default: date | None = None) -> date | None:
    try:
        return date.fromisoformat(str(text)[:10])
    except (TypeError, ValueError):
        return default


def _writer(request) -> bool:
    from engines.permissions import can
    return can(request.slicks_user["role"], "price.write")


def _back(cid, tab: str, **extra) -> str:
    from urllib.parse import urlencode
    return "/price-lists/?" + urlencode({"s": cid, "tab": tab, **{k: v for k, v in extra.items() if v not in (None, "")}})


@page_needed("price-lists")
def price_lists(request):
    from engines.clock import manila_today
    suppliers = _suppliers()
    ids = [s for s, _ in suppliers]
    cid = int(request.GET.get("s") or request.POST.get("s") or request.session.get("pl_supplier") or (ids[0] if ids else 0) or 0)
    if cid not in ids:
        cid = ids[0] if ids else 0
    request.session["pl_supplier"] = cid
    writer = _writer(request)
    tabs = [("prices", "Prices")] + ([("add", "Add a price"), ("import", "Import Excel"), ("discounts", "Discounts")] if writer else []) + [("catalog", "Catalog")]
    tab = request.GET.get("tab") or request.POST.get("tab") or "prices"
    if tab not in dict(tabs):
        tab = "prices"
    if request.method == "POST":
        if not writer:
            from core.menu import PAGES
            return render(request, "core/not_allowed.html", {"page": PAGES["price-lists"]}, status=403)
        return _post(request, cid, tab)
    ctx = {"page_id": "price-lists", "suppliers": suppliers, "cid": cid, "supplier": dict(suppliers).get(cid, ""), "tabs": tabs, "tab": tab, "writer": writer,
           "today": manila_today()}
    if cid or tab == "catalog":
        ctx.update({"prices": _prices_ctx, "add": _add_ctx, "import": _import_ctx, "discounts": _discounts_ctx, "catalog": _catalog_ctx}[tab](request, cid))
    return render(request, "buying/price_lists.html", ctx)


# --- the tabs -----------------------------------------------------------------------------------------------------------------------------------------

def _prices_ctx(request, cid: int) -> dict:
    from engines.clock import manila_today
    from engines.price_list_service import current_prices, price_history
    from engines.price_reconcile import invoice_vs_list
    as_of = _date(request.GET.get("on"), manila_today())
    rows = current_prices(cid, on=as_of).to_dict("records")
    for r in rows:
        prev = r.get("previous_price")
        r["change"] = round((r["unit_price"] - prev) / prev * 100, 1) if prev not in (None, "") and prev == prev and prev > 0 else None
    item = request.GET.get("item", "")
    history = price_history(cid, item).to_dict("records") if item else []
    return {"as_of": as_of, "rows": rows, "item": item, "history": history, "recon": invoice_vs_list(cid, as_of).to_dict("records")}


def _add_ctx(request, cid: int) -> dict:
    from engines import product_catalog as pc
    from engines.price_list_service import item_names
    names = sorted(set(item_names(cid)) | set(pc.list_products()["name"]), key=str.lower)
    return {"names": names}


def _import_ctx(request, cid: int) -> dict:
    """After 'Check the file': the counts, the problems and the lines by category, read again from the waiting upload (nothing is saved yet)."""
    token = request.GET.get("file", "")
    path = _upload_path(token)
    if not path:
        return {}
    from engines import price_template_service as pt
    start = _date(request.GET.get("start"))
    parsed = pt.parse_workbook(path.read_bytes(), default_date=start)
    cmp = pt.compare(cid, parsed) if parsed["rows"] else None
    by_cat = {}
    if cmp is not None:
        for r in cmp.to_dict("records"):
            r["status_text"] = STATUS_TEXT.get(r["status"], r["status"])
            by_cat.setdefault(r["category"], []).append(r)
    summary = cmp["status"].value_counts().to_dict() if cmp is not None else {}
    sc = pt.supplier_check(cid, parsed)
    return {"file": token, "start": start, "parsed": parsed, "by_cat": by_cat, "summary": [(STATUS_TEXT[k], summary.get(k, 0)) for k in ("new", "changed", "same", "missing")],
            "n_rows": len(parsed["rows"]) if sc["level"] != "mismatch" else 0, "sc": sc, "mismatch": pt.mismatch_message(sc) if sc["level"] == "mismatch" else "",
            "brands": sorted(parsed.get("brands", {}).items(), key=lambda kv: -kv[1])}


def _discounts_ctx(request, cid: int) -> dict:
    from engines import discount_rules as dr
    from engines import product_catalog as pc
    rows = dr.list_discounts(cid).to_dict("records")
    for r in rows:
        r["brand"] = r["brand"] or "All brands"
    return {"discounts": rows, "brands": pc.brands()}


def _catalog_ctx(request, cid: int) -> dict:
    from engines import discount_rules as dr
    from engines import product_catalog as pc
    cat = request.GET.get("cat") if request.GET.get("cat") in pc.CATEGORIES else pc.CATEGORIES[0]
    one_too, old = request.GET.get("all") == "1", request.GET.get("old") == "1"
    side = pc.supplier_prices(category=cat)
    if not one_too and not side.empty:
        side = side[side.groupby("product_id")["supplier"].transform("count") > 1]
    side_rows = side.assign(discount=side["chain"].map(dr.chain_label)).to_dict("records") if not side.empty else []
    fields = pc.CATEGORY_FIELDS[cat]
    products = pc.list_products(cat, active_only=not old)
    prod_rows = pc.spec_columns(products, cat).to_dict("records") if not products.empty else []
    for p in prod_rows:
        p["spec_values"] = [p.get(f["key"], "") for f in fields]
    return {"categories": pc.CATEGORIES, "cat": cat, "one_too": one_too, "old": old, "side": side_rows, "fields": fields, "products": prod_rows, "brands": pc.brands(cat)}


# --- saving -------------------------------------------------------------------------------------------------------------------------------------------

def _upload_path(token: str) -> Path | None:
    if not token or not token.isalnum():
        return None
    p = _uploads() / f"{token}.xlsx"
    return p if p.exists() else None


def _keep_upload(data: bytes) -> str:
    folder = _uploads()
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.xlsx"):                                    # a file left from a check never imported: gone after a day
        if time.time() - old.stat().st_mtime > 86400:
            old.unlink(missing_ok=True)
    token = secrets.token_hex(16)
    (folder / f"{token}.xlsx").write_bytes(data)
    return token


def _post(request, cid: int, tab: str):
    from engines import discount_rules as dr
    from engines import product_catalog as pc
    from engines.clock import manila_today
    from engines.price_list_service import add_price, set_price_active
    from engines.price_reconcile import update_to_invoice
    p, action = request.POST, request.POST.get("action", "")
    say = lambda ok, msg: (messages.success if ok else messages.error)(request, msg)      # noqa: E731
    if action == "add_price":
        item = p.get("item", "").strip()
        ok, msg = add_price(cid, item, p.get("unit", ""), p.get("price", "").replace(",", ""), _date(p.get("start"), manila_today()), p.get("code", ""),
                            replace=p.get("replace") == "1")
        catalog = dict(zip(pc.list_products()["name"], pc.list_products()["product_id"]))
        if ok and item in catalog:
            pc.link_price_rows(cid, item, int(catalog[item]))                 # the price belongs to the catalog product
        say(ok, msg)
        return redirect(_back(cid, "prices" if ok else "add"))
    if action == "price_on_off":
        ok, msg = set_price_active(int(p.get("price_id", 0)), p.get("on") == "1")
        say(ok, msg)
        return redirect(_back(cid, "prices", item=p.get("item")))
    if action == "update_to_invoice":
        ok, msg = update_to_invoice(cid, p.get("item", ""))
        say(ok, msg)
        return redirect(_back(cid, "prices"))
    if action == "check_file":
        f = request.FILES.get("file")
        if not f or not f.name.lower().endswith(".xlsx"):
            say(False, "Choose the filled template (.xlsx).")
            return redirect(_back(cid, "import"))
        if f.size > 10 * 1024 * 1024:
            say(False, "The file is larger than 10 MB. Is it the right file?")
            return redirect(_back(cid, "import"))
        return redirect(_back(cid, "import", file=_keep_upload(f.read()), start=p.get("start")))
    if action == "import_file":
        from engines import price_template_service as pt
        path = _upload_path(p.get("file", ""))
        if not path:
            say(False, "The checked file is gone (older than a day?). Upload it again.")
            return redirect(_back(cid, "import"))
        parsed = pt.parse_workbook(path.read_bytes(), default_date=_date(p.get("start")))
        ok, msg, _ = pt.import_workbook(cid, parsed, "replace" if p.get("dup") == "replace" else "skip")
        if ok:
            path.unlink(missing_ok=True)
        say(ok, msg)
        return redirect(_back(cid, "prices" if ok else "import"))
    if action == "set_discount":
        ok, msg = dr.set_discount(cid, p.get("brand", "").strip(), p.get("chain", ""), _date(p.get("start"), manila_today()))
        say(ok, msg)
        return redirect(_back(cid, "discounts"))
    if action == "product_on_off":
        ok, msg = pc.set_product_active(int(p.get("product_id", 0)), p.get("on") == "1")
        say(ok, msg)
        return redirect(_back(cid, "catalog", cat=p.get("cat"), old="1"))
    if action == "add_product":
        cat = p.get("cat", "")
        if cat not in pc.CATEGORIES:
            say(False, "Pick a category.")
            return redirect(_back(cid, "catalog"))
        values = {f["key"]: p.get(f["key"], "").strip() for f in pc.CATEGORY_FIELDS[cat]}
        brand = p.get("brand", "").strip()
        missing = [f["label"] for f in pc.CATEGORY_FIELDS[cat] if f["required"] and not values[f["key"]]]
        if not brand or missing:
            say(False, "Fill in: " + ", ".join((["Brand"] if not brand else []) + missing) + ".")
            return redirect(_back(cid, "catalog", cat=cat))
        _, name, created = pc.get_or_create_product(cat, brand, **values)
        say(bool(name), f"{name} added." if created else (f"{name} is already in the catalog." if name else "The product could not be made from those fields."))
        return redirect(_back(cid, "catalog", cat=cat))
    say(False, "Unknown action.")
    return redirect(_back(cid, tab))


@page_needed("price-lists")
def template(request):
    """The price template to fill: blank, or with this supplier's current prices to change and upload back."""
    from engines import price_template_service as pt
    cid = int(request.GET.get("s") or 0)
    data = pt.build_template(cid) if cid else pt.build_template()
    name = "price_list_template.xlsx"
    if cid:
        name = f"price_list_{dict(_suppliers()).get(cid, cid)}.xlsx".replace(" ", "_")
    resp = HttpResponse(data, content_type=XLSX)
    resp["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp
