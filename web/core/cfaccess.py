"""Zero trust in the app (owner, 2026-10-08): Cloudflare Access sits in front of the site (Google sign-in with Google 2-Step Verification, only the family's
emails) and hands every request a signed pass in the header Cf-Access-Jwt-Assertion. When WEB_CF_TEAM and WEB_CF_AUD are set (the server's /etc/slicks-erp/env),
the app checks that pass itself on every request: the signature (against Cloudflare's published keys), the audience (this one application), and the expiry.
So even a request that somehow reaches the server around Cloudflare is refused. The pass's email must also be the account being used (core.middleware).

Not set (the laptop, the tests) = nothing is checked. The server's own health check (deploy/release.sh, from the server itself, no forwarded address) is exempt."""
from __future__ import annotations

import functools

from django.conf import settings
from django.http import HttpResponse

HEADER = "HTTP_CF_ACCESS_JWT_ASSERTION"
EXEMPT_HEALTH = "/health/"


@functools.lru_cache(maxsize=4)
def _jwks(team: str):
    import jwt
    return jwt.PyJWKClient(f"https://{team}.cloudflareaccess.com/cdn-cgi/access/certs", cache_keys=True, lifespan=3600)


def _signing_key(token: str, team: str):
    """Cloudflare's public key for this pass (fetched once an hour). A separate function so the tests can supply their own."""
    return _jwks(team).get_signing_key_from_jwt(token).key


def verify(token: str, team: str, aud: str) -> str | None:
    """The email in a valid pass, or None (bad signature, wrong application, expired, or no email)."""
    import jwt
    try:
        claims = jwt.decode(token, _signing_key(token, team), algorithms=["RS256"], audience=aud, issuer=f"https://{team}.cloudflareaccess.com",
                            options={"require": ["exp", "aud", "iss"]})
    except Exception:                                           # noqa: BLE001 -- any failure means "not a valid pass"
        return None
    return (claims.get("email") or "").strip().lower() or None


def _from_the_server_itself(request) -> bool:
    return request.META.get("REMOTE_ADDR") in ("127.0.0.1", "::1") and not request.META.get("HTTP_X_FORWARDED_FOR")


class AccessMiddleware:
    """First in line after the static files: no valid Cloudflare Access pass, no page."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        team, aud = getattr(settings, "CF_ACCESS_TEAM", ""), getattr(settings, "CF_ACCESS_AUD", "")
        request.cf_email = None
        if not (team and aud):
            return self.get_response(request)
        if request.path == EXEMPT_HEALTH and _from_the_server_itself(request):
            return self.get_response(request)
        email = verify(request.META.get(HEADER, ""), team, aud)
        if not email:
            return HttpResponse("Sign in through Cloudflare Access first.", status=403, content_type="text/plain")
        request.cf_email = email
        return self.get_response(request)
