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
        "may_search": bool(user) and __import__("engines.permissions", fromlist=["sees_search"]).sees_search(user["role"]),
        "search_q": request.GET.get("q", "") if request.path == "/search/" else "",
        "page_notice": _page_notice() if user else "",
        "over_confirm": _over_confirm(request) if user else None,
    }


def _over_confirm(request):
    try:
        from payables.day_limit import pending
        return pending(request)
    except Exception:                                   # noqa: BLE001 -- never break a page
        return None


def _page_notice() -> str:
    try:
        from engines.settings_registry import page_notice
        return page_notice()
    except Exception:                                   # noqa: BLE001 -- a notice must never break a page
        return ""


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
    cf = getattr(request, "cf_email", None)
    if cf and cf == str(user.get("email") or "").strip().lower():
        # Cloudflare Access already checked this person (owner, 2026-10-09: "we can drop 6 digit code" -- once Access is on): its sign-in, for this very
        # email, is the second step, so the code is not asked. Without Access (not set, or a different email) the code stays.
        _sign_in(request, user)
        return redirect(nxt or "/")
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
    form = {"email": "", "name": "", "username": ""}
    if request.method == "POST":
        form = {"email": request.POST.get("email", ""), "name": request.POST.get("name", ""), "username": request.POST.get("username", "")}
        if request.POST.get("password", "") != request.POST.get("password2", ""):
            messages.error(request, "The two passwords are not the same.")
        else:
            ok, msg, uid = create_owner(form["email"], form["name"], request.POST.get("password", ""), form["username"])
            if ok:
                return _half_signed_in(request, get_user(uid))
            messages.error(request, msg)
    return render(request, "core/setup.html", {"form": form})


def login(request):
    from engines.auth_service import authenticate
    nxt = request.GET.get("next") or request.POST.get("next") or "/"
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        nxt = "/"
    email, cf = "", getattr(request, "cf_email", None)
    if settings.SANDBOX_NO_SIGNIN:                              # the laptop sandbox: pick a person, no password, no code
        from engines.auth_service import list_users, get_user
        if request.method == "POST" and request.POST.get("uid"):
            user = get_user(int(request.POST["uid"]))
            if user and user["is_active"]:
                _sign_in(request, user)
                return redirect(nxt)
        people = [u for u in list_users().to_dict("records") if u["is_active"]]
        return render(request, "core/login.html", {"sandbox_people": people, "next": nxt})
    if request.method == "POST":
        # owner, 2026-10-09: the email, not the username -- the one Cloudflare Access verified (only the password is typed), else the one typed
        email = cf or request.POST.get("email", "")
        ok, user, msg = authenticate(email, request.POST.get("password", ""), where="web", by_email=True)
        if ok:
            return _half_signed_in(request, user, nxt)
        if cf and not _has_account(cf):
            msg = f"No account in the system uses {cf}. Ask the System admin."
        messages.error(request, msg)
    return render(request, "core/login.html", {"email": email, "cf_email": cf, "next": nxt})


def _has_account(email: str) -> bool:
    from engines.common import get_conn
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM app_users WHERE lower(trim(email)) = ?", (email.strip().lower(),)).fetchone() is not None


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


# The "New ..." / "Import ..." cards (SAP Business One style Home, owner 2026-10-09): (group, title, icon, url, page id, needed action or None)
CREATE_CARDS = [
    ("buy", "New order", "note_add", "/purchase-orders/new/", "purchase-orders", None),
    ("buy", "Import a price list", "upload_file", "/price-lists/", "price-lists", None),
    ("pay", "New batch", "note_add", "/order-statements/", "order-statements", None),
    ("bank", "Import bank statement", "upload_file", "/bank-cash/?tab=import", "bank-cash", None),
    ("other", "New loan note", "note_add", "/lenders/?new=1#new", "lenders", "loans.use"),
]
MY_HOME = ["purchase-orders", "order-statements", "checks", "calendar", "review"]     # the everyday cards, first row
MY_HOME_CREATE = ["New order", "New batch"]


def home(request):
    """The launchpad (SAP Business One style, owner 2026-10-09): a "My home" row of the everyday cards, then a row per section; each card its live figure
    (engines/home_tiles.py), plus the "New ..." cards the person may use."""
    from engines.home_tiles import GROUPS, evaluate, layout
    from engines.permissions import can
    user = request.slicks_user
    mine = pages_for(user["role"])
    titles = {g.key: g for g in GROUPS}
    rows, by_id = [], {}
    for key, tiles in layout(mine):
        cards = []
        for t in tiles:
            v = evaluate(t)
            icon = (t.icon or "").removeprefix(":material/").removesuffix(":") or PAGES[t.page_id].icon
            card = {"title": t.title, "icon": icon, "url": _tile_url(t), "figure": v["figure"], "unit": v["unit"], "level": v["level"],
                    "later": t.page_id not in BUILT}
            cards.append(card)
            by_id.setdefault(t.tid, card)
        rows.append({"key": key, "title": titles[key].title, "hint": titles[key].hint, "tiles": cards})
    creates = [{"title": title, "icon": icon, "url": url, "create": True, "group": g} for g, title, icon, url, pid, needs in CREATE_CARDS
               if pid in mine and pid in BUILT and (needs is None or can(user["role"], needs))]
    for r in rows:
        r["tiles"] += [c for c in creates if c["group"] == r["key"]]
    top = [by_id[k] for k in MY_HOME if k in by_id] + [c for c in creates if c["title"] in MY_HOME_CREATE]
    if top:
        rows.insert(0, {"key": "mine", "title": "My home", "hint": "", "tiles": top})
    from engines.settings_registry import NOTICE_KEY
    from engines.settings_store import get_setting
    from engines.whats_new import unseen
    from engines.common import get_conn
    with get_conn() as conn:
        seen = (conn.execute("SELECT whats_new_seen FROM app_users WHERE user_id = ?", (int(user["user_id"]),)).fetchone() or [None])[0]
    return render(request, "core/home.html", {"rows": rows, "page_id": "home", "notice": get_setting(NOTICE_KEY, "") or "", "whats_new": unseen(seen)})


@require_POST
def whats_new_seen(request):
    """'Got it' on Home's What's new card: it does not show again until the next note."""
    from engines.whats_new import latest_id
    from engines.common import get_conn
    with get_conn() as conn:
        conn.execute("UPDATE app_users SET whats_new_seen = ? WHERE user_id = ?", (latest_id(), int(request.slicks_user["user_id"])))
    return redirect("/")


RELATED_PAGE = {"po": "purchase-orders", "statement": "order-statements", "loan": "lenders"}


def related(request, kind: str, doc_id: int):
    """The relationship map of one document, for the pop-up its page's "Relationship map" button opens (SAP Business One style, owner 2026-10-09).
    Worked out only when asked, so the pages stay fast; needs the right to that document's page."""
    if kind not in RELATED_PAGE:
        raise Http404
    role = request.slicks_user["role"]
    if RELATED_PAGE[kind] not in pages_for(role):
        return HttpResponse("You don't have access to this document.", status=403)
    from engines.doc_trail import trail_for
    cols, mine = trail_for(role, kind, int(doc_id)), pages_for(role)
    for col in cols:
        for d in col.get("docs", []):
            if d.get("url") and _page_of(d["url"]) not in mine:
                d["url"] = None                                   # a document on a page hidden from the person: shown, not opened
    return render(request, "core/_related.html", {"related": cols})


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


def _page_of(url: str) -> str:
    """The page id a link opens ('/order-statements/12/' -> 'order-statements')."""
    return (url or "").strip("/").split("/")[0].split("?")[0]


def _search_url(row: dict) -> str:
    """Where a search result opens in the main system (the engine still carries the old screens' page + state)."""
    if row.get("url"):
        return row["url"]
    st, page = row.get("state") or {}, row.get("page", "")
    if st.get("selected_statement_id"):
        tab = {"Checks": "?tab=checks", "Invoices": ""}.get(st.get("os_subtab", ""), "")
        return f"/order-statements/{st['selected_statement_id']}/{tab}"
    if st.get("selected_loan_note_id"):
        return f"/lenders/{st['selected_loan_note_id']}/" + ("?tab=checks" if st.get("ln_subtab") == "Checks" else "")
    if st.get("selected_bank_loan_id"):
        return f"/bank-loans/{st['selected_bank_loan_id']}/"
    return f"/{page}/" if page else "/"


def search(request):
    """Search everything (owner, 2026-10-09: "how about search bar?"): the old screens' search -- checks, invoices, batches, loan notes, bank loans,
    creditors, cards, spoiled checks -- by number, reference, name or an exact amount; each result opens its page here."""
    from engines.permissions import can
    from engines.permissions import sees_search
    if not sees_search(request.slicks_user["role"]):
        return render(request, "core/not_allowed.html", {"page": PAGES["home"]}, status=403)
    from engines.global_search_service import search_everything
    q = (request.GET.get("q") or "").strip()
    groups = search_everything(q, limit=25) if q else []
    mine = pages_for(request.slicks_user["role"])
    for g in groups:
        for r in g["rows"]:
            r["href"] = _search_url(r)
        kept = [r for r in g["rows"] if _page_of(r["href"]) in mine or _page_of(r["href"]) in ("", "home")]     # a page hidden from the person: no result
        g["total"] -= len(g["rows"]) - len(kept)
        g["rows"] = kept
    groups = [g for g in groups if g["rows"]]
    return render(request, "core/search.html", {"page_id": "home", "q": q, "groups": groups, "total": sum(g["total"] for g in groups)})


def whoami(request):
    """For the load test: who the business rules think is acting during this request (what every save made now would be recorded as)."""
    from engines.acting_user import acting_email, acting_role
    return JsonResponse({"email": acting_email(), "role": acting_role()})
