"""Stage D0 screens: first-run set-up, sign in, change password, sign out, Home, and the 'coming in stage Dx' page for screens still in Streamlit."""
from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.menu import BUILT, PAGES, STAGE_NAME, TILE_QUERY, menu_for, pages_for
from core.middleware import MFA_MARK, password_stamp


def page_frame(request) -> dict:
    """What every page's frame needs: the top bar for this person's role, who they are, and the SANDBOX tag."""
    from engines.permissions import ROLE_LABEL
    user = getattr(request, "slicks_user", None)
    return {
        "me": user,
        "theme": (user or {}).get("theme") or "light",          # dark mode is each person's own choice; nobody signed in = the light look (2026-10-13)
        "idle_seconds": _idle_seconds() if user else 0,
        "phone_on": __import__("engines.settings_registry", fromlist=["phone_on"]).phone_on() if user else False,
        "role_label": ROLE_LABEL.get(user["role"], user["role"]) if user else "",
        "topbar": menu_for(user["role"]) if user else [],
        "is_sandbox": settings.IS_SANDBOX,
        "revenue_on": __import__("engines.features", fromlist=["revenue_on"]).revenue_on(),
        "may_search": bool(user) and __import__("engines.permissions", fromlist=["sees_search"]).sees_search(user["role"]),
        "search_q": request.GET.get("q", "") if request.path == "/search/" else "",
        "page_notice": _page_notice() if user else "",
        "over_confirm": _over_confirm(request) if user else None,
        **_fp_frame(request, user),
    }


FP_SALT = "slicks.fingerprint"


def _fp_cookie() -> str:
    return settings.DEVICE_COOKIE_NAME + "_fp"


def _fp_here(request) -> dict:
    """Whose fingerprint sign-in was turned on in this browser: {"u": user id, "k": the key's id}, or {} (a signed cookie; it opens nothing by itself)."""
    from django.core import signing
    try:
        got = signing.loads(request.COOKIES.get(_fp_cookie(), ""), salt=FP_SALT)
    except signing.BadSignature:
        return {}
    return got if isinstance(got, dict) else {}


def _fp_frame(request, user) -> dict:
    """For every page: may this person use fingerprint sign-in, and is it already on in this browser (owner, 2026-10-13)."""
    try:
        from engines import fingerprint_signin as fp
        if not fp.allowed(user):
            return {"fp_may": False, "fp_offer": False}
        here = _fp_here(request)
        mine = here.get("u") == int(user["user_id"]) and here.get("k") in fp.key_ids(int(user["user_id"]))
        return {"fp_may": True, "fp_offer": not mine and request.path in ("/", "/home/", "/phone/")}
    except Exception:                                   # noqa: BLE001 -- never break a page
        return {"fp_may": False, "fp_offer": False}


def _idle_seconds() -> int:
    """For the page's own countdown: 0 on the laptop sandbox, where nobody is signed out."""
    if settings.SANDBOX_NO_SIGNIN:
        return 0
    from engines.settings_registry import idle_minutes
    return idle_minutes() * 60


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


def _sign_in(request, user: dict, code: bool = True) -> None:
    """Signed in: only after the password AND the 6-digit code (owner, 2026-10-08) -- or, while the code is switched off in Settings (owner,
    2026-10-12), after the password of a person Cloudflare already checked. Such a sign-in is marked, and ends when the code is switched back on."""
    request.session.cycle_key()
    request.session.pop("pending", None)
    request.session["uid"] = int(user["user_id"])
    request.session["pw"] = password_stamp(int(user["user_id"]))
    request.session["ep"] = int(user.get("device_epoch") or 0)
    request.session["mfa"] = MFA_MARK
    request.session["code"] = 1 if code else 0


# --- this device (owner, 2026-10-11): the 6-digit code is asked once a day per device. A signed cookie keeps the browser's own number (so a device not used
# before can be told) and whose code it gave on which day. A changed password or "Sign out everywhere" cancels it; a copied cookie is worth one day at most.
DEVICE_SALT = "slicks.device"


def _device(request) -> dict:
    from django.core import signing
    try:
        got = signing.loads(request.COOKIES.get(settings.DEVICE_COOKIE_NAME, ""), salt=DEVICE_SALT)
    except signing.BadSignature:
        return {}
    return got if isinstance(got, dict) else {}


def _device_id(request) -> str:
    if not getattr(request, "_device_id", None):
        import secrets
        request._device_id = str(_device(request).get("d") or "") or secrets.token_hex(8)
    return request._device_id


def _today() -> str:
    from engines.clock import manila_now
    return manila_now().strftime("%Y-%m-%d")


def _code_given_today(request, user: dict) -> bool:
    d, uid = _device(request), int(user["user_id"])
    return (d.get("u") == uid and d.get("day") == _today() and d.get("pw") == password_stamp(uid) and d.get("ep") == int(user.get("device_epoch") or 0))


def _remember_device(request, response, user: dict):
    """After a correct code: this device is not asked again today for this person."""
    from django.core import signing
    uid = int(user["user_id"])
    value = signing.dumps({"d": _device_id(request), "u": uid, "day": _today(), "pw": password_stamp(uid), "ep": int(user.get("device_epoch") or 0)}, salt=DEVICE_SALT)
    response.set_cookie(settings.DEVICE_COOKIE_NAME, value, max_age=400 * 86400, httponly=True, secure=settings.SESSION_COOKIE_SECURE, samesite="Lax")
    return response


def _browser(ua: str) -> str:
    system = next((n for k, n in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"), ("Mac OS X", "Mac"), ("Linux", "Linux")) if k in ua), "")
    name = next((n for k, n in (("Edg/", "Edge"), ("OPR/", "Opera"), ("SamsungBrowser", "Samsung Internet"), ("CriOS", "Chrome"), ("FxiOS", "Firefox"),
                                ("Chrome/", "Chrome"), ("Firefox/", "Firefox"), ("Safari/", "Safari")) if k in ua), "")
    return " on ".join(x for x in (name, system) if x) or ua[:40]


def _log(request, user_id: int, result: str) -> bool:
    """One line in the sign-in record; True when the person signed in on a device they had not used before."""
    from engines.auth_service import log_sign_in
    m = request.META
    return log_sign_in(int(user_id), result, device_id=_device_id(request), browser=_browser(m.get("HTTP_USER_AGENT", "")),
                       ip=(m.get("HTTP_CF_CONNECTING_IP") or m.get("REMOTE_ADDR") or "")[:45], country=m.get("HTTP_CF_IPCOUNTRY", "")[:2],
                       city=m.get("HTTP_CF_IPCITY", "")[:60])


def _half_signed_in(request, user: dict, nxt: str = "/", fingerprint: bool = False, via: str = ""):
    """The password was right: next comes the code (or setting it up, the first time). Nothing else is open until then. Cloudflare Access in front does
    not stand in for the code any more (owner, 2026-10-11: a hacked inbox passes Cloudflare's email check; the phone is what proves it is them). The
    code is asked once a day per device: a device that gave this person's code today signs in with the password."""
    from engines.auth_service import note_password_sign_in, totp_enabled_at
    from engines.settings_registry import authenticator_on
    cf = getattr(request, "cf_email", None)
    if not authenticator_on() and cf and cf == str(user.get("email") or "").strip().lower():
        # the code is switched off (owner, 2026-10-12): Cloudflare's check of this very email is the second step
        note_password_sign_in(int(user["user_id"]), *((("approved by their phone" if via else "fingerprint on this device"),) if fingerprint else ()))
        _sign_in(request, user, code=False)
        _log(request, int(user["user_id"]), via or ("FINGERPRINT" if fingerprint else "CLOUDFLARE"))
        return redirect(nxt or "/")
    if _code_given_today(request, user):
        note_password_sign_in(int(user["user_id"]), *(("fingerprint; this device gave the code earlier today",) if fingerprint else ()))
        _sign_in(request, user)
        _log(request, int(user["user_id"]), via or ("FINGERPRINT" if fingerprint else "REMEMBERED"))
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


def service_worker(request):
    """The phone app's service worker, at the site's root so its scope is the whole site (phone/static/phone/sw.js; it keeps nothing offline)."""
    from django.contrib.staticfiles import finders
    from django.http import FileResponse
    path = finders.find("phone/sw.js")
    if not path:
        raise Http404
    resp = FileResponse(open(path, "rb"), content_type="application/javascript")
    resp["Service-Worker-Allowed"] = "/"
    resp["Cache-Control"] = "no-cache"                       # a phone must never keep an old one: the push handler lives here (2026-10-13)
    resp["Cache-Control"] = "no-cache"
    return resp


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
    if getattr(request, "slicks_user", None) and not settings.SANDBOX_NO_SIGNIN:
        # Owner, 2026-10-14: "when logout, system can be still accessed". The page's own countdown brought the person here while the server still
        # held the sign-in (another tab or a background request had kept it fresh): the menu stayed and every page still opened. Now the sign-in
        # really ends here; and a signed-in person who simply opens /login/ goes on to their page instead of seeing a sign-in form.
        if request.GET.get("idle") == "1" and request.method == "GET":
            request.session.flush()
            request.slicks_user = None
        elif request.method == "GET":
            return redirect(nxt)
    if request.method == "GET" and request.GET.get("idle") == "1":
        from engines.settings_registry import idle_minutes
        messages.info(request, f"You were signed out after {idle_minutes()} minutes of no activity. Sign in to carry on where you were.")
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
        uid = _account_id(email)
        if uid:
            _log(request, uid, "PASSWORD_REFUSED")
        if cf and not uid:
            msg = f"No account in the system uses {cf}. Ask the System admin."
        messages.error(request, msg)
    return render(request, "core/login.html", {"email": email, "cf_email": cf, "next": nxt, "fp_here": bool(_fp_login_user(request)), "phone_way": _phone_way(request)})


# --- fingerprint sign-in (owner, 2026-10-13; engines/fingerprint_signin.py): the device's own fingerprint or face unlock in place of typing the password.
def _site(request) -> tuple[str, str]:
    """(origin, id) of this site as the browser knows it."""
    host = request.get_host()
    return f"{request.scheme}://{host}", host.split(":")[0]


def _fp_login_user(request) -> int | None:
    """The account this browser may sign in to by fingerprint: the one that turned it on here, and only while it is the email Cloudflare verified."""
    from engines import fingerprint_signin as fp
    here = _fp_here(request)
    uid = here.get("u")
    if not uid or settings.SANDBOX_NO_SIGNIN or not fp.on() or here.get("k") not in fp.key_ids(int(uid)):
        return None
    cf = getattr(request, "cf_email", None)
    if cf and _account_id(cf) != int(uid):
        return None
    return int(uid)


def fingerprint_login_options(request):
    from engines import fingerprint_signin as fp
    uid = _fp_login_user(request)
    if uid is None:
        return JsonResponse({"ok": False, "msg": fp.NOT_VERIFIED}, status=400)
    challenge = fp.new_challenge()
    request.session["fp"] = {"c": challenge, "u": uid}
    return JsonResponse({"ok": True, "publicKey": {"challenge": challenge, "rpId": _site(request)[1], "timeout": 60000, "userVerification": "required",
                                                   "allowCredentials": [{"type": "public-key", "id": k} for k in fp.key_ids(uid)]}})


@require_POST
def fingerprint_login(request):
    import json
    from engines import fingerprint_signin as fp
    asked, uid = request.session.pop("fp", None) or {}, _fp_login_user(request)
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        payload = {}
    if uid is None or asked.get("u") != uid or not isinstance(payload, dict):
        return JsonResponse({"ok": False, "msg": fp.NOT_VERIFIED}, status=400)
    origin, rp_id = _site(request)
    ok, user, msg = fp.verify(uid, payload, asked.get("c", ""), origin, rp_id)
    if not ok:
        _log(request, uid, "FINGERPRINT_REFUSED")
        return JsonResponse({"ok": False, "msg": msg}, status=400)
    nxt = str(payload.get("next") or "/")
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        nxt = "/"
    return JsonResponse({"ok": True, "next": _half_signed_in(request, user, nxt, fingerprint=True)["Location"]})


# --- sign in on a computer with your phone (owner, 2026-10-14; engines/phone_approval.py): the site's own QR code, approved on the person's phone.
def _phone_way(request) -> bool:
    """Is "Sign in with your phone" offered on this sign-in page: the switch is on and, where Cloudflare says who it is, that person may use it."""
    from engines import phone_approval as pa
    if settings.SANDBOX_NO_SIGNIN or not pa.on():
        return False
    ua = request.META.get("HTTP_USER_AGENT", "")
    if any(k in ua for k in ("Android", "iPhone", "iPad", "Mobile")):      # owner, 2026-10-14: on the phone itself the phone is the key (its fingerprint)
        return False
    cf = getattr(request, "cf_email", None)
    return pa.may_use(_account_id(cf)) if cf else True


def _qr_svg(text: str):
    import qrcode
    import qrcode.image.svg
    from django.utils.safestring import mark_safe
    svg = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=9, border=2).to_string(encoding="unicode")
    return mark_safe(svg[svg.find("<svg"):])


@require_POST
def login_phone(request):
    """The computer asks: a QR code and a number, good for two minutes."""
    from engines import phone_approval as pa
    nxt = request.POST.get("next") or "/"
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        nxt = "/"
    if not _phone_way(request):
        return redirect("/login/")
    cf = getattr(request, "cf_email", None)
    m = request.META
    made = pa.create(_account_id(cf) if cf else None, _browser(m.get("HTTP_USER_AGENT", "")), (m.get("HTTP_CF_CONNECTING_IP") or m.get("REMOTE_ADDR") or ""), nxt)
    request.session["phone_req"] = made["request_id"]
    return render(request, "core/login_phone.html", {"qr": _qr_svg(f"{_site(request)[0]}/approve/{made['token']}/"), "number": made["number"], "seconds": made["seconds"], "next": nxt})


@require_POST
def login_phone_status(request):
    """The waiting computer asks every two seconds; once the phone approved, this very browser (and only it) is signed in."""
    from engines import phone_approval as pa
    from engines.auth_service import get_user
    rid = request.session.get("phone_req")
    if not rid:
        return JsonResponse({"state": "gone"})
    s = pa.state(int(rid))
    if s["state"] != "approved":
        if s["state"] != "pending":
            request.session.pop("phone_req", None)
        return JsonResponse({"state": s["state"], "left": s.get("left", 0)})
    uid, nxt = pa.collect(int(rid))
    request.session.pop("phone_req", None)
    user = get_user(uid) if uid else None
    cf = getattr(request, "cf_email", None)
    if not user or not user["is_active"] or (cf and cf != str(user["email"]).strip().lower()):
        return JsonResponse({"state": "gone"})
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        nxt = "/"
    return JsonResponse({"state": "approved", "next": _half_signed_in(request, user, nxt, fingerprint=True, via="PHONE")["Location"]})


def approve(request, token: str):
    """The phone's side, after scanning the computer's code. The phone is signed in already (the sign-in page sent it back here)."""
    from engines import phone_approval as pa
    me = request.slicks_user
    if request.method == "POST":
        yes = request.POST.get("answer") == "yes"
        ok, msg = pa.answer(token, me, request.POST.get("number"), yes)
        return render(request, "core/approve.html", {"done": msg, "ok": yes} if ok else {"why": msg})
    info, why = pa.for_phone(token, me)
    return render(request, "core/approve.html", {"info": info, "why": why})


def fingerprint(request):
    """The person's own devices: turn fingerprint sign-in on for this one, remove any."""
    from engines import fingerprint_signin as fp
    user = request.slicks_user
    if not fp.allowed(user):
        return redirect("/")
    here = _fp_here(request)
    if request.method == "POST":
        mine = {d["key_id"]: d for d in fp.devices(int(user["user_id"]))}
        key_id = int(request.POST.get("key_id") or 0)
        ok, msg = fp.remove(key_id, int(user["user_id"]))
        (messages.success if ok else messages.error)(request, msg)
        resp = redirect("/fingerprint/")
        if ok and mine.get(key_id, {}).get("credential_id") == here.get("k"):
            resp.delete_cookie(_fp_cookie())
        return resp
    rows = fp.devices(int(user["user_id"]))
    for r in rows:
        r["here"] = here.get("u") == int(user["user_id"]) and r["credential_id"] == here.get("k")
    return render(request, "core/fingerprint.html", {"rows": rows, "on_here": any(r["here"] for r in rows)})


def fingerprint_options(request):
    from engines import fingerprint_signin as fp
    user = request.slicks_user
    if not fp.allowed(user):
        return JsonResponse({"ok": False, "msg": "Fingerprint sign-in is not on for this account."}, status=400)
    challenge = fp.new_challenge()
    request.session["fp_new"] = challenge
    return JsonResponse({"ok": True, "publicKey": {
        "challenge": challenge, "rp": {"name": "Slick's ERP", "id": _site(request)[1]},
        "user": {"id": fp.b64(f"slicks-{int(user['user_id'])}".encode()), "name": user["email"], "displayName": user["name"]},
        "pubKeyCredParams": [{"type": "public-key", "alg": a} for a in fp.ALGS], "timeout": 60000, "attestation": "none",
        "authenticatorSelection": {"authenticatorAttachment": "platform", "userVerification": "required", "residentKey": "preferred"},     # Android makes only passkeys (owner, 2026-10-14: the phone cancelled at once)
        "excludeCredentials": [{"type": "public-key", "id": k} for k in fp.key_ids(int(user["user_id"]))]}})


@require_POST
def fingerprint_add(request):
    import json
    from django.core import signing
    from engines import fingerprint_signin as fp
    user = request.slicks_user
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        payload = {}
    origin, rp_id = _site(request)
    ok, msg, key = fp.register(user, payload if isinstance(payload, dict) else {}, request.session.pop("fp_new", ""), origin, rp_id,
                               device=_browser(request.META.get("HTTP_USER_AGENT", "")))
    resp = JsonResponse({"ok": ok, "msg": msg}, status=200 if ok else 400)
    if ok:
        resp.set_cookie(_fp_cookie(), signing.dumps({"u": int(user["user_id"]), "k": key}, salt=FP_SALT), max_age=400 * 86400, httponly=True,
                        secure=settings.SESSION_COOKIE_SECURE, samesite="Lax")
    return resp


def _account_id(email: str) -> int | None:
    from engines.common import get_conn
    with get_conn() as conn:
        row = conn.execute("SELECT user_id FROM app_users WHERE lower(trim(email)) = ?", ((email or "").strip().lower(),)).fetchone()
    return int(row[0]) if row else None


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
            _log(request, int(user["user_id"]), "CODE")
            return _remember_device(request, redirect(nxt), user)
        _log(request, int(user["user_id"]), "WRONG_CODE")
        messages.error(request, msg)
    return render(request, "core/code.html", {})


def code_setup(request):
    """The first sign-in (or after the System admin reset the code): scan the QR code into an authenticator app, then confirm with its first code.
    Only while the System admin allows it (owner, 2026-10-11): otherwise no QR code and no key are shown, only whom to ask."""
    from engines.auth_service import totp_enabled_at, totp_setup_secret, totp_setup_until, totp_uri, verify_totp
    user = _pending_user(request)
    if user is None:
        return redirect("/login/")
    if totp_enabled_at(int(user["user_id"])):                # already set up: the ordinary code screen
        return redirect("/2fa/")
    if request.method == "POST" and request.POST.get("cancel"):
        request.session.flush()
        return redirect("/login/")
    if not totp_setup_until(int(user["user_id"])):
        if request.method == "GET":
            _log(request, int(user["user_id"]), "SETUP_REFUSED")
        return render(request, "core/code_wait.html", {"email": user["email"]})
    secret = totp_setup_secret(int(user["user_id"]))
    if secret is None:
        return redirect("/2fa/")
    if request.method == "POST":
        ok, msg = verify_totp(int(user["user_id"]), request.POST.get("code", ""))
        if ok:
            nxt = request.session["pending"].get("next") or "/"
            _sign_in(request, user)
            _log(request, int(user["user_id"]), "SET_UP")
            messages.success(request, "Your authenticator is set up. Sign-in asks for its 6-digit code after your password, once a day on each device.")
            return _remember_device(request, redirect(nxt), user)
        _log(request, int(user["user_id"]), "WRONG_CODE")
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
    ("pay", "Record an invoice", "receipt_long", "/order-statements/invoices/", "order-statements", None),
    ("bank", "Import bank statement", "upload_file", "/bank-cash/?tab=import", "bank-cash", None),
    ("other", "New loan note", "note_add", "/lenders/?new=1#new", "lenders", "loans.use"),
]
MY_HOME = ["purchase-orders", "order-statements", "checks", "calendar", "review"]     # the everyday cards, first row
MY_HOME_CREATE = ["New order", "Record an invoice"]

# The order of the cards in each row (owner, 2026-10-11: "organize the tiles have a logical sequence"): left to right in the order the work is done.
# A card that STARTS something ("New order", "Import ...") sits just before the page it lands on. Named by a tile's id (its key, else its page id) or
# by a "New ..." card's title; a card not named here goes after the others, in its old order. The rows themselves follow the goods, then the money:
# Buy -> Stock -> Sell -> Pay suppliers -> Bank -> Other payables -> Records -> Analytics -> Set-up (engines/home_tiles.GROUPS).
ROW_ORDER = {
    "mine": ["New order", "purchase-orders", "Record an invoice", "order-statements", "calendar", "checks", "review"],
    "buy": ["Import a price list", "price-lists", "New order", "purchase-orders", "receive-goods"],
    "stock": ["stock", "stock-counts", "affiliates"],
    "sell": ["scan-out", "sales", "sales-today", "collect", "customers", "si-booklets"],
    "pay": ["Record an invoice", "order-statements", "payment-run", "calendar", "over-limit-days", "payables-due", "checks", "release"],
    "bank": ["Import bank statement", "bank-cash", "booklets", "expenses"],
    "other": ["New loan note", "lenders", "bank-loans", "credit-cards"],
    "analytics": ["an-purchasing", "an-payables", "an-bank", "an-lenders"],
    "system": ["creditors", "settings", "review"],
}


def _in_order(row_key: str, cards: list[dict]) -> list[dict]:
    order = {k: n for n, k in enumerate(ROW_ORDER.get(row_key, []))}
    return sorted(cards, key=lambda c: order.get(c.get("tid") or c["title"], len(order)))      # sorted() is stable: unnamed cards keep their order


def home(request):
    """The launchpad (SAP Business One style, owner 2026-10-09): a "My home" row of the everyday cards, then a row per section; each card its live figure
    (engines/home_tiles.py), plus the "New ..." cards the person may use."""
    from engines.home_tiles import GROUPS, evaluate, layout
    from engines.permissions import can
    user = request.slicks_user
    if user["role"] == "scanner":
        return redirect("/phone/")
    mine = pages_for(user["role"])
    titles = {g.key: g for g in GROUPS}
    rows, by_id = [], {}
    from engines.permissions import analytics_tabs
    an_tabs = analytics_tabs()
    for key, tiles in layout(mine):
        cards = []
        for t in tiles:
            if t.page_id == "analytics" and dict(t.state or ()).get("analytics_tab") not in an_tabs:     # its page is hidden from this person: so is its card
                continue
            v = evaluate(t)
            icon = (t.icon or "").removeprefix(":material/").removesuffix(":") or PAGES[t.page_id].icon
            card = {"title": t.title, "icon": icon, "url": _tile_url(t), "figure": v["figure"], "unit": v["unit"], "level": v["level"],
                    "later": t.page_id not in BUILT, "tid": t.tid}
            cards.append(card)
            by_id.setdefault(t.tid, card)
        rows.append({"key": key, "title": titles[key].title, "hint": titles[key].hint, "tiles": cards})
    creates = [{"title": title, "icon": icon, "url": url, "create": True, "group": g} for g, title, icon, url, pid, needs in CREATE_CARDS
               if pid in mine and pid in BUILT and (needs is None or can(user["role"], needs))]
    for r in rows:
        r["tiles"] = _in_order(r["key"], r["tiles"] + [c for c in creates if c["group"] == r["key"]])
    top = _in_order("mine", [by_id[k] for k in MY_HOME if k in by_id] + [c for c in creates if c["title"] in MY_HOME_CREATE])
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
    try:
        from engines.doc_trail import trail_summary
        summary = trail_summary(kind, cols)
    except Exception:                                             # noqa: BLE001 -- the line on top must never break the map
        summary = ""
    return render(request, "core/_related.html", {"related": cols, "summary": summary})


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


def notifications(request):
    """Notifications on this phone (owner, 2026-10-13: credit card reminders): the page where a person allows them on the phone in their hand,
    switches them off again, or sends themselves a test. The phone's own Allow box does the allowing; this keeps the address it gives."""
    import json

    from django.http import JsonResponse

    from engines import card_reminders as cr, web_push
    me = request.slicks_user
    if request.method == "POST":
        try:
            data = json.loads(request.body or b"{}")
        except ValueError:
            data = {}
        what = data.get("action")
        if what == "on":
            ok, msg = web_push.subscribe(me["email"], data.get("subscription") or {}, request.META.get("HTTP_USER_AGENT", ""))
        elif what == "off":
            web_push.unsubscribe(me["email"], str(data.get("endpoint") or ""))
            ok, msg = True, "Notifications are off for this device."
        elif what == "test":
            n = web_push.send(me["email"], "Slick's ERP · test", "Notifications work on this device.", "/notifications/", tag="test")
            ok, msg = (True, f"Sent to {n} device(s). It should appear in a few seconds.") if n else (False, "No device took it. Press Allow on this device first.")
        else:
            ok, msg = False, "Unknown action."
        return JsonResponse({"ok": ok, "msg": msg})
    mine = next((p for p in cr.people() if p["email"].lower() == me["email"].lower()), None)
    return render(request, "core/notifications.html", {"key": web_push.public_key(), "phones": web_push.phones(me["email"]), "mine": mine})


def theme(request):
    """The Look switch in the person menu (owner, 2026-10-13): light, dark, or auto. Saved on the person's account; back to the page they were on."""
    from engines.auth_service import set_theme
    if request.method == "POST" and getattr(request, "slicks_user", None):
        set_theme(request.slicks_user["user_id"], request.POST.get("theme", ""))
    back = request.POST.get("next") or "/"
    return redirect(back if back.startswith("/") and not back.startswith("//") else "/")
