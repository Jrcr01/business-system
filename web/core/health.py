"""Set-up > System health (owner, 2026-10-20; mock-up system_health.html): the System admin's read-only look at the whole system.
The checks are in engines/system_health.py."""
from __future__ import annotations

from django.shortcuts import render

from masters.views import page_needed


@page_needed("system-health")
def system_health(request):
    from engines import system_health as sh
    cs = sh.cards()
    s = sh.summary(cs)
    sh._SUMMARY.clear()                                   # Home's line follows what was just checked
    return render(request, "core/system_health.html", {"page_id": "system-health", "cards": cs, "sum": s, "releases": sh.recent_releases()})
