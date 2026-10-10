"""Stock > Stock counts (the revenue cycle, stage A; owner, 2026-10-11): start a count for a part of the stock, the blind sheet (typed or scanned; a
blank one to print for the employee who counts), and the differences the System admin approves or sends back. Rules: engines/count_rules.py."""
from __future__ import annotations

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render

from masters.views import page_needed


def _say(request, ok: bool, msg: str) -> None:
    (messages.success if ok else messages.error)(request, msg)


def _num(text):
    text = str(text or "").replace(",", "").strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return "bad"


@page_needed("stock-counts")
def counts(request):
    from engines import count_rules as cr
    from engines.product_catalog import brands
    from engines.stock_rules import CATEGORY_LABEL
    if request.method == "POST" and request.POST.get("action") == "start":
        p = request.POST
        ok, msg, cid = cr.start_count(p.get("type", ""), p.get("brand", ""), p.get("uncounted") == "1", p.get("q", ""), p.get("counted_by", ""))
        _say(request, ok, msg)
        return redirect(f"/stock-counts/{cid}/" if ok else "/stock-counts/")
    return render(request, "selling/counts.html", {"page_id": "stock-counts", "rows": cr.listing(), "types": list(CATEGORY_LABEL.items()), "brands": brands()})


@page_needed("stock-counts")
def count(request, count_id: int):
    from engines import count_rules as cr
    from engines.permissions import can
    c = cr.get_count(count_id)
    if not c:
        raise Http404
    if request.method == "POST":
        p, what = request.POST, request.POST.get("action", "")
        if what in ("save", "finish"):
            typed, bad = {}, None
            for row in cr.sheet_rows(count_id):
                n = _num(p.get(f"c_{row['line_id']}"))
                if n == "bad":
                    bad = f"{row['name']}: '{p.get('c_' + str(row['line_id']))}' is not a quantity."
                    break
                typed[row["line_id"]] = (n, p.get(f"n_{row['line_id']}", ""))
            ok, msg = (False, bad) if bad else cr.save_counted(count_id, typed, p.get("counted_by", ""))
            if ok and what == "finish":
                ok, msg = cr.finish(count_id)
        elif what == "cancel":
            ok, msg = cr.cancel(count_id)
        elif what == "approve":
            ok, msg = cr.approve(int(p.get("line_id") or 0), p.get("reason", ""))
        elif what == "approve_all":
            ok, msg = cr.approve_all(count_id, p.get("reason", ""))
        elif what == "recount":
            ok, msg = cr.recount(int(p.get("line_id") or 0))
        else:
            ok, msg = False, "Unknown action."
        _say(request, ok, msg)
        return redirect("/stock-counts/" if what == "cancel" and ok else f"/stock-counts/{count_id}/")
    ctx = {"page_id": "stock-counts", "c": c, "may_approve": can(request.slicks_user["role"], "inventory.approve")}
    if c["status"] == "COUNTING":
        ctx["sheet"] = cr.sheet_rows(count_id)
        if request.GET.get("print") == "1":                      # the blank sheet for the employee who counts: items only
            return render(request, "selling/count_sheet.html", ctx)
        return render(request, "selling/count.html", ctx)
    rows = cr.differences(count_id)
    ctx.update({"rows": rows, "matched": sum(1 for r in rows if abs(r["difference"]) < 1e-9),
                "short": [r for r in rows if r["difference"] < -1e-9], "over": [r for r in rows if r["difference"] > 1e-9]})
    ctx["short_pcs"], ctx["short_value"] = -sum(r["difference"] for r in ctx["short"]), -sum(r["value"] for r in ctx["short"])
    ctx["over_pcs"], ctx["over_value"] = sum(r["difference"] for r in ctx["over"]), sum(r["value"] for r in ctx["over"])
    return render(request, "selling/count_differences.html", ctx)
