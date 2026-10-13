"""Sign-in for every request (stage D0).

  * Nobody has an account yet: every page leads to the first-run set-up (the System admin makes their own account).
  * Not signed in: every page leads to Sign in, and back to the page asked for afterwards.
  * Signed in: the person is read from app_users on EVERY request, so switching someone off, or changing their password, signs them out everywhere at once.
  * Signing in takes the password AND the 6-digit code from the person's authenticator app (owner, 2026-10-08): after the password, only /2fa/ is open.
    The code is asked once a day per device, of everyone, whether or not Cloudflare Access is in front (owner, 2026-10-11).
  * "Sign out everywhere" (Settings > Users) ends every sign-in of a person at once.
  * A temporary password must be changed before anything else.
  * Everything saved while the request runs is recorded as done by this person, with their role for the permission guards (engines/acting_user.py).
    The value is per request, so two people saving at the same moment never get each other's name."""
from __future__ import annotations

import hashlib
import time

from django.conf import settings
from urllib.parse import quote

from django.http import HttpResponse
from django.shortcuts import redirect

OPEN = ("/static/", "/health/", "/s/")           # /s/<code>/ = the supplier's QR link to one statement (engines/statement_links.py): its code is the key
MFA_MARK = 2                                 # what a sign-in carries since 2026-10-11; an older one (1: Cloudflare stood in for the code) is ended
_SEEN: dict[int, float] = {}                 # user id -> when this worker last wrote last_seen_at (at most once a minute: no extra save on every click)


def note_seen(user_id: int, path: str, now: float | None = None) -> bool:
    """Writes when this person was last active (for the deploy's who-is-active check, 2026-10-09). Returns True when it wrote."""
    now = time.time() if now is None else now
    if now - _SEEN.get(int(user_id), 0.0) < 60:
        return False
    _SEEN[int(user_id)] = now
    try:
        from engines.common import get_conn
        with get_conn() as conn:
            conn.execute("UPDATE app_users SET last_seen_at = CURRENT_TIMESTAMP, last_seen_path = ? WHERE user_id = ?", (path[:120], int(user_id)))
    except Exception:                                   # never let this stop a page (an older database without the column)
        return False
    return True


def password_stamp(user_id: int) -> str | None:
    """A short fingerprint of the person's stored password: kept in the sign-in cookie, so a password change ends every other sign-in."""
    from engines.common import get_conn
    with get_conn() as conn:
        row = conn.execute("SELECT password_hash FROM app_users WHERE user_id = ?", (int(user_id),)).fetchone()
    return hashlib.sha256(row[0].encode()).hexdigest()[:16] if row else None


def current_user(request) -> dict | None:
    from engines.auth_service import get_user
    uid = request.session.get("uid")
    if not uid:
        return None
    if request.session.get("mfa") != MFA_MARK:                  # a sign-in from before the code was asked of everyone (2026-10-11): sign in again, with it
        request.session.flush()
        return None
    if not settings.SANDBOX_NO_SIGNIN:                          # no activity for the time set in Settings (owner, 2026-10-12): signed out
        import time
        from engines.settings_registry import idle_minutes
        now, seen = int(time.time()), request.session.get("seen")
        if seen and now - int(seen) > idle_minutes() * 60:
            request.session.flush()
            return None
        request.session["seen"] = now
    if request.session.get("code") == 0:                        # signed in while the code was switched off: over once it is asked again
        from engines.settings_registry import authenticator_on
        if authenticator_on():
            request.session.flush()
            return None
    user = get_user(int(uid))
    if (not user or not user["is_active"] or request.session.get("pw") != password_stamp(int(uid))
            or int(request.session.get("ep") or 0) != int(user.get("device_epoch") or 0)):      # "Sign out everywhere" was pressed since
        request.session.flush()
        return None
    return user


class SignInMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from engines.acting_user import acting_as
        from engines.auth_service import needs_setup

        path = request.path
        request.slicks_user = None
        if path.startswith(OPEN):
            return self.get_response(request)
        if needs_setup():
            return self.get_response(request) if path == "/setup/" else redirect("/setup/")
        user = current_user(request)
        if user is None:
            if path in ("/sw.js", "/phone/manifest.webmanifest", "/phone/manifest-ops.webmanifest"):   # what Chrome reads to install the phone app, from the sign-in page too (2026-10-12)
                return self.get_response(request)
            if path in ("/login/", "/login/fingerprint/", "/login/fingerprint/options/") or (path in ("/2fa/", "/2fa/setup/") and request.session.get("pending")):     # the code step, after a right password
                return self.get_response(request)
            return redirect("/login/" + (f"?next={quote(path)}" if path not in ("/", "/logout/") else ""))
        request.slicks_user = user
        cf = getattr(request, "cf_email", None)
        if cf and cf != user["email"].strip().lower():          # Cloudflare knows this person as someone else: sign this account out, never mix two people
            request.session.flush()
            from django.utils.html import escape
            return HttpResponse(f"Cloudflare has you as {escape(cf)}, but this account is {escape(user['email'])}. Sign in with your own email. <a href='/login/'>Sign in</a>", status=403)
        if user["must_change_password"] and path not in ("/password/", "/logout/"):
            return redirect("/password/")
        note_seen(int(user["user_id"]), path)
        started = time.perf_counter()
        with acting_as(user["email"], user["role"]):
            response = self.get_response(request)
        response["Server-Timing"] = f"app;dur={(time.perf_counter() - started) * 1000:.0f}"     # how long the page took on the server (browser tools and the load test read it)
        return response
