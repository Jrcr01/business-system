"""The daily payment rule at the moment a check is parked or moved (owner, 2026-10-09: "no AP above 350,000 to 400,000 per day"; over it: confirm
only, flagged for review). A save that would push a day above the ceiling is held: the page shows the days and their totals with "Confirm anyway",
which sends the same entry again with confirm_over=1. engines/day_cap.py has the rule."""
from __future__ import annotations

from django.shortcuts import redirect

KEY = "over_confirm"


def hold(request, back: str, over: list[dict], what: str):
    """Keep the entry and show the confirm box on `back`."""
    request.session[KEY] = {"back": back.split("#")[0], "what": what,
                            "fields": [[k, v] for k in request.POST if k != "csrfmiddlewaretoken" for v in request.POST.getlist(k)],
                            "over": [{**d, "day": d["day"].isoformat()} for d in over]}
    return redirect(back)


def pending(request) -> dict | None:
    """The held entry for this page, shown once (for the page frame)."""
    p = request.session.get(KEY)
    if not p or p.get("back", "").split("?")[0] != request.path:
        return None
    request.session.pop(KEY, None)
    from datetime import date
    from engines.day_cap import ceiling
    return {**p, "over": [{**d, "day": date.fromisoformat(d["day"])} for d in p["over"]], "ceiling": ceiling()}


def check(request, adds, back: str, what: str, minus=None):
    """None to go ahead (the day stays within the limit, or the person confirmed), else the redirect that holds the entry. Returns (redirect, over)."""
    from engines.day_cap import over_days
    over = over_days(adds, minus)
    if over and not request.POST.get("confirm_over"):
        return hold(request, back, over, what), over
    return None, over


def flag(over: list[dict], what: str) -> None:
    if not over:
        return
    from engines.common import write_tx
    from engines.day_cap import flag_over
    with write_tx() as conn:
        flag_over(conn, over, what)
