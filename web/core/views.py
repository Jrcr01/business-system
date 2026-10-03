"""Stage D0 screens: first-run set-up, sign in, change password, sign out, Home, and the 'coming in stage Dx' page for screens still in Streamlit."""
from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.menu import BUILT, PAGES, STAGE_NAME, TILE_QUERY, menu_for, pages_for
from core.middleware import password_stamp


def page_frame(request) -> dict:
    """What every page's frame needs: the top bar for this person's role, who they are, and the SANDBOX tag."""
    from engines.permissions import ROLE_LABEL
    user = getattr(request, "slicks_user", None)
    return {
        "me": user,
        "role_label": ROLE_LABEL.get(user["role"], user["role"]) if user else "",
        "topbar": menu_for(user["role"]) if user else [],
        "is_sandbox": settings.IS_SANDBOX,
    }


def _sign_in(request, user: dict) -> None:
    request.session.cycle_key()
    request.session["uid"] = int(user["user_id"])
    request.session["pw"] = password_stamp(int(user["user_id"]))


def health(request):
    """For the server's watchdog and the load test: the database answers."""
    from engines.common import get_conn
    with get_conn() as conn:
        conn.execute("SELECT 1").fetchone()
    return HttpResponse("ok", content_type="text/plain")


def setup(request):
    """The very first account (the System admin), made by the person themselves. Gone once any account exists."""
    from engines.auth_service import create_owner, get_user, needs_setup
    if not needs_setup():
        return redirect("/login/")
    form = {"email": "", "name": ""}
    if request.method == "POST":
        form = {"email": request.POST.get("email", ""), "name": request.POST.get("name", "")}
        if request.POST.get("password", "") != request.POST.get("password2", ""):
            messages.error(request, "The two passwords are not the same.")
        else:
            ok, msg, uid = create_owner(form["email"], form["name"], request.POST.get("password", ""))
            if ok:
                _sign_in(request, get_user(uid))
                return redirect("/")
            messages.error(request, msg)
    return render(request, "core/setup.html", {"form": form})


def login(request):
    from engines.auth_service import authenticate
    nxt = request.GET.get("next") or request.POST.get("next") or "/"
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        nxt = "/"
    email = ""
    if request.method == "POST":
        email = request.POST.get("email", "")
        ok, user, msg = authenticate(email, request.POST.get("password", ""))
        if ok:
            _sign_in(request, user)
            return redirect(nxt)
        messages.error(request, msg)
    return render(request, "core/login.html", {"email": email, "next": nxt})


@require_POST
def logout(request):
    request.session.flush()
    return redirect("/login/")


def change_password(request):
    from engines.auth_service import change_password as do_change
    user = request.slicks_user
    if request.method == "POST":
        if request.POST.get("new", "") != request.POST.get("new2", ""):
            messages.error(request, "The two new passwords are not the same.")
        else:
            ok, msg = do_change(int(user["user_id"]), request.POST.get("old", ""), request.POST.get("new", ""))
            if ok:
                request.session["pw"] = password_stamp(int(user["user_id"]))     # this sign-in stays; every other one ends
                messages.success(request, msg)
                return redirect("/")
            messages.error(request, msg)
    return render(request, "core/password.html", {"forced": bool(user["must_change_password"])})


def _tile_url(tile) -> str:
    query = "&".join(TILE_QUERY[s] for s in tile.state if s in TILE_QUERY)
    return f"/{tile.page_id}/" + (f"?{query}" if query else "")


def home(request):
    """The launchpad: a row of tiles per group, each with its live figure (engines/home_tiles.py, the same tiles and figures as Streamlit)."""
    from engines.home_tiles import GROUPS, evaluate, layout
    user = request.slicks_user
    titles = {g.key: g for g in GROUPS}
    rows = []
    for key, tiles in layout(pages_for(user["role"])):
        cards = []
        for t in tiles:
            v = evaluate(t)
            icon = (t.icon or "").removeprefix(":material/").removesuffix(":") or PAGES[t.page_id].icon
            cards.append({"title": t.title, "icon": icon, "url": _tile_url(t), "figure": v["figure"], "unit": v["unit"], "level": v["level"],
                          "later": t.page_id not in BUILT})
        rows.append({"key": key, "title": titles[key].title, "hint": titles[key].hint, "tiles": cards})
    from engines.settings_registry import NOTICE_KEY
    from engines.settings_store import get_setting
    return render(request, "core/home.html", {"rows": rows, "page_id": "home", "notice": get_setting(NOTICE_KEY, "") or ""})


def page(request, page_id: str):
    """A screen that has not moved to Django yet: says which stage brings it, and that Streamlit still has it."""
    if page_id not in PAGES:
        raise Http404
    if page_id not in pages_for(request.slicks_user["role"]):
        return render(request, "core/not_allowed.html", {"page": PAGES[page_id]}, status=403)
    p = PAGES[page_id]
    return render(request, "core/later.html", {"page": p, "page_id": page_id, "stage_name": STAGE_NAME.get(p.stage, "")})


def whoami(request):
    """For the load test: who the business rules think is acting during this request (what every save made now would be recorded as)."""
    from engines.acting_user import acting_email, acting_role
    return JsonResponse({"email": acting_email(), "role": acting_role()})
