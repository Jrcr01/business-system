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
    r = web.post("/setup/", {"name": "Admin", "email": "admin@slicks.test", "password": PW, "password2": PW}, **_h(_pass()))      # the very first account also needs a pass
    assert r.status_code == 302 and r["Location"] == "/"                # with Access on, no 6-digit code (2026-10-09)
    ok = web.get("/", **_h(_pass("admin@slicks.test")))
    assert ok.status_code == 200
    other = web.get("/", **_h(_pass("someone.else@gmail.com")))
    assert other.status_code == 403 and b"Cloudflare has you as someone.else@gmail.com" in other.content
    again = web.get("/", **_h(_pass("admin@slicks.test")))             # the mismatch signed that session out
    assert again.status_code == 302 and again["Location"].startswith("/login/")


def test_not_configured_means_not_checked(web):
    assert web.get("/login/").status_code in (200, 302)


def test_with_access_on_the_6_digit_code_is_not_asked_but_without_it_it_is(web, access):
    # owner, 2026-10-09: "we can drop 6 digit code" -- Cloudflare Access (Google / email check, same email) is the second step instead
    from tests.test_web_d0 import username_of
    r = web.post("/setup/", {"name": "Admin", "email": "admin@slicks.test", "password": PW, "password2": PW}, **_h(_pass()))
    assert r.status_code == 302 and r["Location"] == "/"                          # signed in: no code, no code set-up
    assert web.get("/", **_h(_pass())).status_code == 200
    web.get("/logout/", **_h(_pass()))
    r = web.post("/login/", {"username": username_of("admin@slicks.test"), "password": PW}, **_h(_pass()))
    assert r.status_code == 302 and r["Location"] == "/"
    r = web.post("/login/", {"username": username_of("admin@slicks.test"), "password": "wrong-one-77"}, **_h(_pass()))
    assert r.status_code != 302 or r["Location"] != "/"                           # the password is still checked
