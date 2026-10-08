"""Sign-in for every request (stage D0).

  * Nobody has an account yet: every page leads to the first-run set-up (the System admin makes their own account).
  * Not signed in: every page leads to Sign in, and back to the page asked for afterwards.
  * Signed in: the person is read from app_users on EVERY request, so switching someone off, or changing their password, signs them out everywhere at once.
  * A temporary password must be changed before anything else.
  * Everything saved while the request runs is recorded as done by this person, with their role for the permission guards (engines/acting_user.py).
    The value is per request, so two people saving at the same moment never get each other's name."""
from __future__ import annotations

import hashlib
import time
from urllib.parse import quote

from django.http import HttpResponse
from django.shortcuts import redirect

OPEN = ("/static/", "/health/")


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
    user = get_user(int(uid))
    if not user or not user["is_active"] or request.session.get("pw") != password_stamp(int(uid)):
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
            if path == "/login/":
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
        started = time.perf_counter()
        with acting_as(user["email"], user["role"]):
            response = self.get_response(request)
        response["Server-Timing"] = f"app;dur={(time.perf_counter() - started) * 1000:.0f}"     # how long the page took on the server (browser tools and the load test read it)
        return response
