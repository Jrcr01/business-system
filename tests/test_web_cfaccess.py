"""Zero trust in the app (owner, 2026-10-08): with Cloudflare Access configured, every request needs a valid pass, and the pass's email must be the account used."""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.test_web_d0 import PW, code_for

TEAM, AUD = "slicks", "aud-123"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _pass(email="admin@slicks.test", aud=AUD, exp=3600, key=KEY, team=TEAM):
    return jwt.encode({"email": email, "aud": [aud], "iss": f"https://{team}.cloudflareaccess.com", "exp": int(time.time()) + exp}, key, algorithm="RS256")


@pytest.fixture
def access(web_setup, monkeypatch):
    from django.conf import settings
    from core import cfaccess
    monkeypatch.setattr(settings, "CF_ACCESS_TEAM", TEAM)
    monkeypatch.setattr(settings, "CF_ACCESS_AUD", AUD)
    monkeypatch.setattr(cfaccess, "_signing_key", lambda token, team: KEY.public_key())


def _h(token):
    return {"HTTP_CF_ACCESS_JWT_ASSERTION": token}


def _first_admin(web):
    """The very first account, made behind Cloudflare Access: it needs a pass, and its own authenticator all the same."""
    r = web.post("/setup/", {"name": "Admin", "email": "admin@slicks.test", "password": PW, "password2": PW}, **_h(_pass()))
    assert r.status_code == 302 and r["Location"] == "/2fa/setup/"       # Cloudflare in front does not stand in for the code (owner, 2026-10-11)
    assert web.get("/2fa/setup/", **_h(_pass())).status_code == 200
    r = web.post("/2fa/setup/", {"code": code_for("admin@slicks.test")}, **_h(_pass()))
    assert r.status_code == 302 and r["Location"] == "/"


def test_no_pass_no_page(web, access):
    assert web.get("/login/").status_code == 403
    assert web.get("/login/", **_h("garbage")).status_code == 403


def test_a_valid_pass_opens_the_site(web, access):
    assert web.get("/setup/", **_h(_pass())).status_code == 200


@pytest.mark.parametrize("bad", ["wrong_aud", "expired", "wrong_key", "wrong_team"])
def test_a_pass_that_is_not_right_is_refused(web, access, bad):
    token = {"wrong_aud": lambda: _pass(aud="another-app"), "expired": lambda: _pass(exp=-60),
             "wrong_key": lambda: _pass(key=rsa.generate_private_key(public_exponent=65537, key_size=2048)), "wrong_team": lambda: _pass(team="other")}[bad]()
    assert web.get("/setup/", **_h(token)).status_code == 403


def test_the_servers_own_health_check_is_exempt_but_the_internet_is_not(web, access):
    assert web.get("/health/").content == b"ok"                        # from the server itself: no forwarded address
    assert web.get("/health/", HTTP_X_FORWARDED_FOR="203.0.113.9").status_code == 403
    assert web.get("/health/", HTTP_X_FORWARDED_FOR="203.0.113.9", **_h(_pass())).status_code == 200


def test_the_pass_email_must_be_the_account(web, access):
    _first_admin(web)
    ok = web.get("/", **_h(_pass("admin@slicks.test")))
    assert ok.status_code == 200
    other = web.get("/", **_h(_pass("someone.else@gmail.com")))
    assert other.status_code == 403 and b"Cloudflare has you as someone.else@gmail.com" in other.content
    again = web.get("/", **_h(_pass("admin@slicks.test")))             # the mismatch signed that session out
    assert again.status_code == 302 and again["Location"].startswith("/login/")


def test_not_configured_means_not_checked(web):
    assert web.get("/login/").status_code in (200, 302)


def test_cloudflare_in_front_does_not_stand_in_for_the_code(web, access):
    # owner, 2026-10-11: "the risk is my family's email will be hack" -- a hacked inbox passes Cloudflare's email check, so the 6-digit code from the
    # person's own phone is asked all the same (2026-10-09's "we can drop 6 digit code" is reversed)
    from django.test import Client
    _first_admin(web)
    assert web.get("/", **_h(_pass())).status_code == 200
    web.post("/logout/", **_h(_pass()))
    page = web.get("/login/", **_h(_pass())).content.decode()
    assert "Verified by Cloudflare as <b>admin@slicks.test</b>" in page and 'type="email"' not in page    # only the password is typed (owner, 2026-10-09)
    other = Client()                                                              # another device holding the same Cloudflare pass
    r = other.post("/login/", {"password": PW}, **_h(_pass()))
    assert r.status_code == 302 and r["Location"] == "/2fa/"                      # the code is asked
    assert other.get("/", **_h(_pass()))["Location"].startswith("/login/")        # and nothing is open without it
    r = other.post("/login/", {"email": "someone.else@x.test", "password": PW}, **_h(_pass()))
    assert r.status_code == 302 and r["Location"] == "/2fa/"                      # the typed email is ignored: Cloudflare's is used
    r = other.post("/login/", {"password": "wrong-one-77"}, **_h(_pass()))
    assert r.status_code != 302                                                   # the password is still checked
    other.post("/login/", {"password": PW}, **_h(_pass()))
    r = other.post("/2fa/", {"code": code_for("admin@slicks.test")}, **_h(_pass()))
    assert r.status_code == 302 and r["Location"] == "/" and other.get("/", **_h(_pass())).status_code == 200
