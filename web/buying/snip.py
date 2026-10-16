"""Snip from a document, on the Price lists page (owner, 2026-10-16; engines/snip.py; mock-ups docs/mockups/aa_snip.html, ab_snip_demo.html). The tab
shows the supplier's documents, the page picture and the box; what is read comes back as JSON for the grid; "Check these rows" turns the grid into the
category's own template sheet and hands it to the Excel import's check screen, so the snipped rows get the same validation (owner: "if you can make
validation in our own snipper, it is better") and the same product key as a file. A price saved this way carries its snip (the "source" mark)."""
from __future__ import annotations

import json

from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from masters.views import page_needed


def _may_write(request) -> bool:
    from engines.permissions import can
    return can(request.slicks_user["role"], "price.write")


def snip_ctx(request, cid: int) -> dict:
    """The Snip tab's context: the supplier's documents, and the open one (?f=<token>&p=<page>)."""
    from engines import product_catalog as pc
    from engines import snip
    from engines.clock import manila_today
    token = request.GET.get("f", "")
    info = snip.file_info(token) if token else None
    if info and (info["kind"] != "price" or (info["owner_id"] and int(info["owner_id"]) != int(cid))):
        info = None
    page = max(1, min(int(request.GET.get("p") or 1), info["pages"] if info else 1))
    out = {"files": snip.files_for("price", cid), "doc": None, "categories": pc.CATEGORIES, "col_keys": {}, "today": manila_today()}
    if info:
        from engines import price_template_service as pt
        W, H = snip.page_size(token, page)
        out["doc"] = {"token": token, "filename": info["filename"], "pages": info["pages"], "page": page, "has_text": bool(info["has_text"]), "w": W, "h": H,
                      "words": json.dumps(snip.words(token, page)) if info["has_text"] else "[]"}
        out["col_keys"] = {c: [(k, label) for k, label, _ in pt._headers(c)] for c in pc.CATEGORIES}
    return out


@page_needed("price-lists")
@require_POST
def upload(request):
    from django.contrib import messages
    from engines import snip
    cid = int(request.POST.get("s") or 0)
    if not _may_write(request):
        return HttpResponse(status=403)
    f = request.FILES.get("file")
    ok, msg, token = snip.keep("price", cid, f.name if f else "", f.read() if f else b"")
    (messages.success if ok else messages.error)(request, msg)
    return redirect(f"/price-lists/?s={cid}&tab=snip" + (f"&f={token}" if ok else ""))


@page_needed("price-lists")
def page_image(request, token: str, page: int):
    from engines import snip
    path = snip.page_image(token, page)
    if not path:
        raise Http404
    resp = FileResponse(open(path, "rb"), content_type="image/jpeg")
    resp["Cache-Control"] = "private, max-age=86400"
    return resp


@page_needed("price-lists")
@require_POST
def read(request, token: str):
    """The box → what is inside it, plus the guessed column names for the category (JSON)."""
    from engines import snip
    try:
        d = json.loads(request.body or b"{}")
        box = [float(d["box"][k]) for k in ("x0", "y0", "x1", "y1")]
        page = int(d.get("page") or 1)
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"ok": False, "msg": "No box."}, status=400)
    mode = "text" if d.get("mode") == "text" else "table"
    try:
        r = snip.read_box(token, page, box, mode)
    except Exception as e:                                   # noqa: BLE001 -- the OCR program missing, a broken file: say so, never a blank page
        return JsonResponse({"ok": False, "msg": f"Could not read the box: {e}"}, status=500)
    from engines import product_catalog as pc
    category = d.get("category") if d.get("category") in pc.CATEGORIES else "Tires"
    r["ok"] = r["how"] != "none"
    r["msg"] = "" if r["ok"] else "Nothing readable inside the box. Draw it over the table or the figure."
    if r["ok"] and mode == "table":
        r["header"] = snip.has_header(r["rows"])
        r["colmap"] = snip.guess_colmap(category, r["rows"])
    return JsonResponse(r)


@page_needed("price-lists")
@require_POST
def check(request, token: str):
    """"Check these rows": the grid (as corrected on the screen) becomes the category's template sheet, kept like an uploaded file, and the Excel
    import's own check screen opens on it. The snip is saved so the prices can be traced back after the import."""
    from buying.views import _keep_upload, _date
    from engines import snip
    cid = int(request.POST.get("s") or 0)
    if not _may_write(request):
        return HttpResponse(status=403)
    try:
        rows = json.loads(request.POST.get("rows") or "[]")
        colmap = json.loads(request.POST.get("colmap") or "[]")
        box = [float(request.POST.get(k) or 0) for k in ("x0", "y0", "x1", "y1")]
    except ValueError:
        rows, colmap, box = [], [], [0, 0, 0, 0]
    category, brand = request.POST.get("category", ""), request.POST.get("brand", "").strip()
    start = _date(request.POST.get("start"))
    page = int(request.POST.get("page") or 1)
    header = request.POST.get("header") == "1"
    if not rows or not any(colmap):
        from django.contrib import messages
        messages.error(request, "Snip a table first, and name at least one column.")
        return redirect(f"/price-lists/?s={cid}&tab=snip&f={token}&p={page}")
    data = snip.price_workbook(cid, category, brand, rows, colmap, start.isoformat() if start else "", header_row=header)
    snip_id = snip.save_snip(token, page, box, "table", rows, "price_import", cid)
    return redirect(f"/price-lists/?s={cid}&tab=import&file={_keep_upload(data)}&start={start.isoformat() if start else ''}&snip={snip_id}")


@page_needed("price-lists")
def source(request, snip_id: int):
    """The source mark: the page with the box drawn on it, read only."""
    from engines import snip
    info = snip.snip_info(snip_id)
    if not info:
        raise Http404
    W, H = snip.page_size(info["token"], info["page"])
    return render(request, "buying/snip_source.html", {"page_id": "price-lists", "s": info, "w": W, "h": H,
                                                       "box": {"l": info["x0"] / W * 100, "t": info["y0"] / H * 100, "w": (info["x1"] - info["x0"]) / W * 100, "h": (info["y1"] - info["y0"]) / H * 100}})
