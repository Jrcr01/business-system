"""Sign in on a computer with your phone (owner, 2026-10-14): the computer shows a QR code and a number; the person's own phone, signed in to the
system, picks the number and approves; that computer, and only it, is then signed in."""
import re
import sqlite3

from tests.test_fingerprint_signin import _turn_on
from tests.test_web_d0 import _admin, _sign_in_new, fetchone_uid


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _ask(computer):
    """The computer presses "Sign in with your phone": (the code in the QR, the number it shows)."""
    r = computer.post("/login/phone/", {"next": "/expenses/"})
    assert r.status_code == 200 and b"Scan with your phone" in r.content
    number = int(re.search(r'class="login-num"[^>]*>(\d+)<', r.content.decode()).group(1))
    with sqlite3.connect(_db()) as conn:
        token, stored = conn.execute("SELECT token, number FROM signin_requests ORDER BY request_id DESC LIMIT 1").fetchone()
    assert stored == number and 10 <= number <= 99
    return token, number


def _status(computer):
    return computer.post("/login/phone/status/").json()


def test_the_phone_approves_and_only_that_computer_is_signed_in(web, db):
    from django.test import Client
    from engines.auth_service import recent_sign_ins
    phone = _admin(web)                                                          # the phone: signed in, fingerprint turned on
    computer = Client()
    assert b"Sign in with your phone" in computer.get("/login/").content
    _turn_on(phone)
    token, number = _ask(computer)
    assert _status(computer)["state"] == "pending"
    page = phone.get(f"/approve/{token}/").content.decode()
    assert "Sign in on a computer?" in page and f'value="{number}"' in page and page.count('name="number"') == 3
    r = phone.post(f"/approve/{token}/", {"answer": "yes", "number": str(number)})
    assert b"Approved. The computer is signed in." in r.content
    thief = Client()                                                             # another browser has no claim on it
    assert _status(thief)["state"] == "gone" and thief.get("/")["Location"].startswith("/login/")
    s = _status(computer)
    assert s["state"] == "approved" and s["next"] in ("/expenses/", "/2fa/")
    if s["next"] == "/2fa/":                                                     # the 6-digit code is on in the tests: the phone stood in for the password only
        from tests.test_web_d0 import code_for
        r = computer.post("/2fa/", {"code": code_for("admin@slicks.test")})
        assert r.status_code == 302 and r["Location"] == "/expenses/"
    assert computer.get("/").status_code == 200
    assert recent_sign_ins(fetchone_uid("admin@slicks.test"))[0]["result"] in ("PHONE", "CODE")
    assert _status(computer)["state"] == "gone"                                  # used once
    assert b"no longer good" in phone.get(f"/approve/{token}/").content


def test_a_wrong_number_and_it_is_not_me_cancel_the_code(web, db):
    from django.test import Client
    phone = _admin(web)
    _turn_on(phone)
    computer = Client()
    token, number = _ask(computer)
    wrong = 10 + (number - 10 + 37) % 90
    r = phone.post(f"/approve/{token}/", {"answer": "yes", "number": str(wrong)})
    assert b"not the number on the computer" in r.content
    assert _status(computer)["state"] == "denied" and computer.get("/")["Location"].startswith("/login/")
    assert b"no longer good" in phone.post(f"/approve/{token}/", {"answer": "yes", "number": str(number)}).content      # the right number comes too late
    token, number = _ask(computer)
    r = phone.post(f"/approve/{token}/", {"answer": "no"})
    assert b"Refused. Nobody was signed in" in r.content and _status(computer)["state"] == "denied"
    with sqlite3.connect(_db()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM review_flags WHERE kind = 'PHONE_SIGN_IN_REFUSED'").fetchone()[0] == 1


def test_it_needs_fingerprint_on_the_phone_a_live_code_and_the_switch(web, db):
    from django.test import Client
    from engines import settings_registry as sr
    from engines.acting_user import acting_as
    phone = _admin(web)
    computer = Client()
    token, number = _ask(computer)
    assert b"Turn on fingerprint sign-in on this phone first" in phone.get(f"/approve/{token}/").content           # no fingerprint yet: no approving
    assert b"Turn on fingerprint" in phone.post(f"/approve/{token}/", {"answer": "yes", "number": str(number)}).content
    assert _status(computer)["state"] == "pending"
    _turn_on(phone)
    with sqlite3.connect(_db()) as conn:                                                                             # two minutes passed
        conn.execute("UPDATE signin_requests SET expires_at = '2026-01-01 00:00:00'")
    assert b"no longer good" in phone.get(f"/approve/{token}/").content and _status(computer)["state"] == "gone"
    rack = _sign_in_new("rack@slicks.test", "scanner")                                                               # the Scanner account never approves
    token, number = _ask(computer)
    assert b"Approved" not in rack.post(f"/approve/{token}/", {"answer": "yes", "number": str(number)}, follow=True).content
    assert _status(computer)["state"] == "pending"
    with acting_as("admin@slicks.test", "admin"):
        sr.save("signin_phone_approval", "0")
    assert b"Sign in with your phone" not in Client().get("/login/").content
    assert Client().post("/login/phone/", {})["Location"] == "/login/"
    assert b"switched off" in phone.get(f"/approve/{token}/").content
