"""Reports -> PO follow-through and Purchases with and without a PO (owner, 2026-10-12: "the reports first" -> "go"). Read-only pages over
engines/po_reports.py; both live inside Reports, so whoever sees Reports sees them."""
from __future__ import annotations

from django.http import HttpResponse
from django.shortcuts import render

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx(data: bytes, name: str) -> HttpResponse:
    resp = HttpResponse(data, content_type=XLSX)
    resp["Content-Disposition"] = f'attachment; filename="{name}.xlsx"'
    return resp


def follow_through(request):
    from engines import po_reports
    supplier = int(request.GET.get("supplier")) if (request.GET.get("supplier") or "").isdigit() else None
    data = po_reports.follow_through(request.GET.get("show", "open"), supplier)
    if request.GET.get("download") == "xlsx":
        return _xlsx(po_reports.follow_through_xlsx(data), "po_follow_through")
    return render(request, "payables/report_po.html", {"page_id": "reports", "r": "po", "supplier": supplier, **data})


def with_and_without(request):
    from engines import po_reports
    data = po_reports.with_and_without(request.GET.get("by", "month"), request.GET.get("m"))
    if request.GET.get("download") == "xlsx":
        return _xlsx(po_reports.with_and_without_xlsx(data), "purchases_with_and_without_a_po")
    return render(request, "payables/report_nopo.html", {"page_id": "reports", "r": "nopo", **data})
