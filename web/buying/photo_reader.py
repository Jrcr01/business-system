"""Price lists > Read a photo (owner, 2026-10-20; mock-up price_photo_reader.html). The rules are in engines/price_photo_reader.py; this only draws
the two steps (photos, then check and save) and passes on what was typed."""
from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect

from masters.views import page_needed


def ctx(request, cid: int) -> dict:
    from engines import ai_client
    from engines import price_photo_reader as ppr
    out = {"ai": ai_client.status()}
    token = request.GET.get("r", "")
    if token:
        try:
            col = int(request.GET.get("col", "-1"))
        except ValueError:
            col = -1
        rv = ppr.review(token, col if col >= 0 else 0, request.GET.get("brand", ""), request.GET.get("tube", "TL"))
        if rv and col < 0:                                   # first look: the net column when there is one, else the first
            nets = [i for i, c in enumerate(rv["columns"]) if "NET" in c.upper()]
            if nets:
                rv = ppr.review(token, nets[0], request.GET.get("brand", ""), request.GET.get("tube", "TL"))
        out["rv"] = rv
        out["tube"] = request.GET.get("tube", "TL")
    return out


def post(request, cid: int):
    from engines import price_photo_reader as ppr
    p, action = request.POST, request.POST.get("action", "")
    if action == "photo_read":
        files = [(f.name, f.read()) for f in request.FILES.getlist("photos")]
        ok, msg, token = ppr.read_photos(cid, files)
        (messages.success if ok else messages.error)(request, msg)
        return redirect(f"/price-lists/?s={cid}&tab=photo" + (f"&r={token}" if ok else ""))
    token = p.get("r", "")
    n = int(p.get("n") or 0)
    rows = [{"size": p.get(f"size_{i}", ""), "ply": p.get(f"ply_{i}", ""), "pattern": p.get(f"pattern_{i}", ""), "price": p.get(f"price_{i}", "")}
            for i in range(n) if p.get(f"keep_{i}", "1") == "1"]
    ok, msg = ppr.save(token, cid, p.get("brand", ""), p.get("effective_date", ""), p.get("tube", ""), rows, p.get("discount", ""),
                       p.get("discount_scope", "brand") == "brand", p.get("replace") == "1")
    (messages.success if ok else messages.error)(request, msg)
    return redirect(f"/price-lists/?s={cid}&tab=prices" if ok else f"/price-lists/?s={cid}&tab=photo&r={token}")


@page_needed("price-lists")
def photo_image(request, token: str, i: int):
    from engines import price_photo_reader as ppr
    data = ppr.photo(token, i)
    if data is None:
        raise Http404
    return HttpResponse(data, content_type="image/jpeg")
