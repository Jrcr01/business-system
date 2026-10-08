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
    """Signed in: only after the password AND the 6-digit code (owner, 2026-10-08)."""
    request.session.cycle_key()
    request.session.pop("pending", None)
    request.session["uid"] = int(user["user_id"])
    request.session["pw"] = password_stamp(int(user["user_id"]))
    request.session["mfa"] = 1


def _half_signed_in(request, user: dict, nxt: str = "/"):
    """The password was right: next comes the code (or setting it up, the first time). Nothing else is open until then."""
    from engines.auth_service import totp_enabled_at
    request.session.cycle_key()
    request.session.pop("uid", None)
    request.session["pending"] = {"uid": int(user["user_id"]), "pw": password_stamp(int(user["user_id"])), "next": nxt}
    return redirect("/2fa/" if totp_enabled_at(int(user["user_id"])) else "/2fa/setup/")


def _pending_user(request) -> dict | None:
    """Whoever passed the password step on this browser, if their password and account are unchanged since."""
    from engines.auth_service import get_user
    p = request.session.get("pending") or {}
    user = get_user(int(p["uid"])) if p.get("uid") else None
    if not user or not user["is_active"] or p.get("pw") != password_stamp(int(user["user_id"])):
        request.session.pop("pending", None)
        return None
    return user


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
                return _half_signed_in(request, get_user(uid))
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
        ok, user, msg = authenticate(email, request.POST.get("password", ""), where="web")
        if ok:
            return _half_signed_in(request, user, nxt)
        messages.error(request, msg)
    return render(request, "core/login.html", {"email": email, "next": nxt})


def code(request):
    """Step 2 of every sign-in: the 6-digit code from the person's authenticator app."""
    from engines.auth_service import totp_enabled_at, verify_totp
    user = _pending_user(request)
    if user is None:
        return redirect("/login/")
    if not totp_enabled_at(int(user["user_id"])):
        return redirect("/2fa/setup/")
    if request.method == "POST":
        if request.POST.get("cancel"):
            request.session.flush()
            return redirect("/login/")
        ok, msg = verify_totp(int(user["user_id"]), request.POST.get("code", ""))
        if ok:
            nxt = request.session["pending"].get("next") or "/"
            _sign_in(request, user)
            return redirect(nxt)
        messages.error(request, msg)
    return render(request, "core/code.html", {})


def code_setup(request):
    """The first sign-in (or after the System admin reset the code): scan the QR code into an authenticator app, then confirm with its first code."""
    from engines.auth_service import totp_setup_secret, totp_uri, verify_totp
    user = _pending_user(request)
    if user is None:
        return redirect("/login/")
    secret = totp_setup_secret(int(user["user_id"]))
    if secret is None:                                       # already set up: the ordinary code screen
        return redirect("/2fa/")
    if request.method == "POST":
        if request.POST.get("cancel"):
            request.session.flush()
            return redirect("/login/")
        ok, msg = verify_totp(int(user["user_id"]), request.POST.get("code", ""))
        if ok:
            nxt = request.session["pending"].get("next") or "/"
            _sign_in(request, user)
            messages.success(request, "Your sign-in code is set up. From now on, sign-in asks for it after your password.")
            return redirect(nxt)
        messages.error(request, msg)
    import qrcode
    import qrcode.image.svg
    qr = qrcode.make(totp_uri(user["email"], secret), image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    svg = qr.to_string(encoding="unicode")
    svg = svg[svg.find("<svg"):]                             # inline in the page: no XML header
    key = " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))
    from django.utils.safestring import mark_safe
    return render(request, "core/code_setup.html", {"qr": mark_safe(svg), "key": key})


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
    from core.menu import page_url
    query = "&".join(TILE_QUERY[s] for s in tile.state if s in TILE_QUERY)
    return page_url(tile.page_id, query)


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
    from django.conf import settings
    base = settings.STREAMLIT_URL
    return render(request, "core/later.html", {"page": p, "page_id": page_id, "stage_name": STAGE_NAME.get(p.stage, ""),
                                               "streamlit": f"{base.rstrip('/')}/{page_id}" if base else ""})


def whoami(request):
    """For the load test: who the business rules think is acting during this request (what every save made now would be recorded as)."""
    from engines.acting_user import acting_email, acting_role
    return JsonResponse({"email": acting_email(), "role": acting_role()})
