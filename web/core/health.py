"""Set-up > System health (owner, 2026-10-20; mock-up system_health.html): the System admin's read-only look at the whole system.
The checks are in engines/system_health.py."""
from __future__ import annotations

from django.shortcuts import render

from masters.views import page_needed


def company_logo(request):
    """The company's own logo (Settings > Company), or 404 so the page falls back to the built-in one."""
    from django.http import Http404, HttpResponse
    from engines.company import LOGO_TYPES, logo_path
    p = logo_path()
    if p is None:
        raise Http404
    resp = HttpResponse(p.read_bytes(), content_type=LOGO_TYPES[p.suffix.lower()])       # read whole (under 2 MB): no open handle keeps a replace from working
    resp["Cache-Control"] = "public, max-age=3600"
    return resp


@page_needed("system-health")
def system_health(request):
    from engines import system_health as sh
    cs = sh.cards()
    s = sh.summary(cs)
    sh._SUMMARY.clear()                                   # Home's line follows what was just checked
    return render(request, "core/system_health.html", {"page_id": "system-health", "cards": cs, "sum": s, "releases": sh.recent_releases()})
