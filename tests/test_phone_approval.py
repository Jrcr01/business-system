"""Approve a computer's sign-in on your phone (owner, 2026-10-15: "drop scan qr but push notification", "comes after it", "1 phone only"): after the
right password on a computer, a notification goes to the person's phone; its link opens the approve page (no sign-in needed there), where the number
the computer shows and the phone's fingerprint approve it; that computer, and only it, is then signed in. Nobody without a phone set up is held back."""
import json
import re
import sqlite3

import pytest
from django.test import Client

from tests.test_fingerprint_signin import Device
from tests.test_web_d0 import PW, _admin, _sign_in_new, fetchone_uid

PHONE_UA = "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Mobile Safari/537.36"


def _db():
    import engines.db_engine as d
    return d.DB_PATH


@pytest.fixture
def pushed(monkeypatch):
    """Every notification the system sends, read as the phone would: [message]."""
    from engines import web_push
    out, real = [], web_push.encrypt
    monkeypatch.setattr(web_push, "encrypt", lambda message, p256dh, auth: out.append(json.loads(message)) or real(message, p256dh, auth))
    monkeypatch.setattr(web_push, "_post", lambda endpoint, body, headers: 201)
    return out


def _switch(value="1"):
    from engines import settings_registry as sr
    from engines.acting_user import acting_as
    with acting_as("admin@slicks.test", "admin"):
        sr.save("signin_push_approval", value)


LAPTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"


def _sub(email, n=1, ua=PHONE_UA):
    """Notifications allowed on a device of that person (a real key pair, so the message can be encrypted for it)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from engines import web_push
    raw = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    ok, msg = web_push.subscribe(email, {"endpoint": f"https://push.example/{email}/{n}", "keys": {"p256dh": web_push._b64(raw), "auth": web_push._b64(b"0123456789abcdef")}}, ua)
    assert ok, msg


def _fp_on(web, ua=PHONE_UA):
    """Fingerprint turned on, on a phone (or the laptop: ua)."""
    device = Device()
    o = web.get("/fingerprint/options/", HTTP_USER_AGENT=ua).json()
    r = web.post("/fingerprint/add/", json.dumps(device.create(o["publicKey"]["challenge"])), content_type="application/json", HTTP_USER_AGENT=ua)
    assert r.status_code == 200 and r.json()["ok"], r.content
    return device


def _ready(web, email="admin@slicks.test"):
    """The person's phone: fingerprint turned on and notifications allowed, on the phone."""
    device = _fp_on(web)
    _sub(email)
    return device


def _computer_signs_in(email="admin@slicks.test", nxt="/expenses/"):
    computer = Client()
    r = computer.post("/login/", {"email": email, "password": PW, "next": nxt})
    return computer, r


def _status(computer):
    return computer.post("/login/push/status/").json()


def _approve(token, device, number):
    phone = Client(HTTP_USER_AGENT=PHONE_UA)                                    # not signed in: the notification's link alone
    page = phone.get(f"/approve/{token}/").content.decode()
    o = phone.get(f"/approve/{token}/options/").json()
    if not o.get("ok"):
        return page, o
    return page, phone.post(f"/approve/{token}/", json.dumps({**device.get(o["publicKey"]["challenge"]), "number": str(number)}),
                            content_type="application/json").json()


def _token():
    with sqlite3.connect(_db()) as conn:
        return conn.execute("SELECT token, number FROM signin_requests ORDER BY request_id DESC LIMIT 1").fetchone()


def test_after_the_password_the_phone_approves_and_only_that_computer_is_signed_in(web, db, pushed):
    from engines.auth_service import recent_sign_ins
    device = _ready(_admin(web))
    _switch()
    computer, r = _computer_signs_in()
    page = r.content.decode()
    assert r.status_code == 200 and "Check your phone" in page
    token, number = _token()
    assert f'class="login-num" aria-label="The number to pick">{number}<' in page
    assert pushed[-1]["title"] == "Sign-in request" and pushed[-1]["url"] == f"/approve/{token}/"
    assert computer.get("/")["Location"].startswith("/login/") and _status(computer)["state"] == "pending"     # nothing open on the password alone
    shown, res = _approve(token, device, number)
    assert "Approve this sign-in?" in shown and shown.count('name="number"') == 3 and f'value="{number}"' in shown
    assert res["ok"] and "Approved" in res["msg"]
    thief = Client()                                                             # another browser has no claim on it
    assert _status(thief)["state"] == "gone"
    s = _status(computer)
    assert s == {"state": "approved", "next": "/expenses/"} and computer.get("/").status_code == 200
    assert recent_sign_ins(fetchone_uid("admin@slicks.test"))[0]["result"] == "PUSH"
    assert _status(computer)["state"] == "gone"                                  # used once
    assert b"no longer good" in Client().get(f"/approve/{token}/").content
    assert computer.post("/login/push/again/").json()["ok"] is False


def test_a_wrong_number_another_device_and_it_is_not_me_approve_nothing(web, db, pushed):
    device = _ready(_admin(web))
    _switch()
    computer, _ = _computer_signs_in()
    token, number = _token()
    _, res = _approve(token, Device(), number)                                   # a phone that is not hers: the fingerprint does not match
    assert not res["ok"] and _status(computer)["state"] == "pending"
    wrong = 10 + (number - 10 + 37) % 90
    _, res = _approve(token, device, wrong)
    assert not res["ok"] and "not the number" in res["msg"] and _status(computer)["state"] == "denied"
    computer, _ = _computer_signs_in()
    token, number = _token()
    assert computer.post("/login/push/again/").json()["ok"] and len(pushed) == 3                     # "Send again"
    r = Client().post(f"/approve/{token}/", {"answer": "no"})
    assert b"Refused. Nobody was signed in" in r.content and _status(computer)["state"] == "denied"
    with sqlite3.connect(_db()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM review_flags WHERE kind = 'PHONE_SIGN_IN_REFUSED'").fetchone()[0] == 1
        conn.execute("UPDATE signin_requests SET expires_at = '2026-01-01 00:00:00'")
    assert b"no longer good" in Client().get(f"/approve/{token}/").content


def test_required_on_a_computer_the_phone_still_signs_in_and_the_admin_keeps_a_backup_phone(web, db, pushed):
    # owner, 2026-10-15: "i want to require we have a push authentication", right away; the System admin: a second phone as a backup
    _admin(web)
    _switch()
    computer, r = _computer_signs_in()                                           # no phone set up: no computer sign-in
    assert b"Set up your phone first" in r.content and not pushed and computer.get("/")["Location"].startswith("/login/")
    _fp_on(web, LAPTOP_UA)
    _sub("admin@slicks.test", 9, LAPTOP_UA)
    computer, r = _computer_signs_in()                                           # the laptop's fingerprint and notifications are not a phone (live, 2026-10-15)
    assert b"Set up your phone first" in r.content
    assert b"Sign in using fingerprint" not in computer.get("/login/").content   # ... nor may the laptop's own fingerprint go around the phone
    _fp_on(web)
    computer, r = _computer_signs_in()                                           # fingerprint on the phone but no notifications there: still not set up
    assert b"Set up your phone first" in r.content
    phone = Client(HTTP_USER_AGENT=PHONE_UA)
    r = phone.post("/login/", {"email": "admin@slicks.test", "password": PW})
    assert b"Set up your phone first" not in r.content and b"Check your phone" not in r.content and r.status_code == 302     # the phone signs in, to set itself up
    _sub("admin@slicks.test", 1)
    computer, r = _computer_signs_in()
    assert b"Check your phone" in r.content and len(pushed) == 1
    _sub("admin@slicks.test", 2)                                                 # the System admin's second phone: both get it
    computer, r = _computer_signs_in()
    assert b"Check your phone" in r.content and len(pushed) == 3
    from engines.phone_approval import push_phone
    me = push_phone(fetchone_uid("admin@slicks.test"))
    assert len(me["subs"]) == 2 and me["backup"] == "Chrome on Android"
    _switch("0")
    family = _sign_in_new("family@slicks.test", "owner")                        # anyone else: one phone only
    _switch()
    _fp_on(family)
    _sub("family@slicks.test", 1)
    _sub("family@slicks.test", 2)
    assert len(push_phone(fetchone_uid("family@slicks.test"))["subs"]) == 1
    _switch("0")
    computer, r = _computer_signs_in()                                           # switched off: the password alone, as before
    assert b"Check your phone" not in r.content and b"Set up your phone first" not in r.content
    _sign_in_new("rack@slicks.test", "scanner")                                  # the Scanner account is never asked
    _switch()
    _sub("rack@slicks.test")
    assert push_phone(fetchone_uid("rack@slicks.test")) is None
    r = Client().post("/login/", {"email": "rack@slicks.test", "password": PW})
    assert b"Set up your phone first" not in r.content


def test_the_system_admin_clears_a_lost_phone(web, db, pushed):
    from engines.phone_approval import push_phone
    _ready(_admin(web))
    family = _sign_in_new("family@slicks.test", "owner")
    _ready(family, "family@slicks.test")
    uid = fetchone_uid("family@slicks.test")
    page = web.get(f"/settings/users/{uid}/").content.decode()
    assert "Phone for sign-in approval" in page and "Clear this phone" in page
    web.post(f"/settings/users/{uid}/", {"action": "clear_phone"})
    assert push_phone(uid) is None
    with sqlite3.connect(_db()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sign_in_keys WHERE user_id = ?", (uid,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM push_subscriptions WHERE user_email = 'family@slicks.test'").fetchone()[0] == 0
    _switch()
    r = Client().post("/login/", {"email": "family@slicks.test", "password": PW})
    assert b"Set up your phone first" in r.content                               # no computer until the new phone is set up (required, 2026-10-15)
    assert b"Scan with your phone" not in Client().get("/login/").content and b"Sign in with your phone" not in Client().get("/login/").content   # no QR any more


def test_the_set_up_this_phone_card_shows_on_a_phone_until_it_is_set_up(web, db, pushed):
    # owner, 2026-10-15: "can we push them to allow push notifications", "and even install the shortcut"
    _admin(web)
    assert b"Set up this phone" not in web.get("/", HTTP_USER_AGENT=PHONE_UA).content          # the switch is off: no card
    _switch()
    home = web.get("/", HTTP_USER_AGENT=PHONE_UA).content.decode()
    assert "Set up this phone" in home and "Install Slick's ERP" in home and "Allow notifications" in home and "Turn on fingerprint" in home
    assert 'id="fp_offer"' not in home                                                      # one card, not two
    from engines import settings_registry as sr
    from engines.acting_user import acting_as
    with acting_as("admin@slicks.test", "admin"):
        sr.save("phone_pages", "1")
    assert "Set up this phone" in web.get("/phone/", HTTP_USER_AGENT=PHONE_UA).content.decode()
    assert b"Set up this phone" not in web.get("/", HTTP_USER_AGENT=LAPTOP_UA).content       # never on a computer
    assert b"Set up this phone" not in web.get("/expenses/", HTTP_USER_AGENT=PHONE_UA).content   # Home only
    users = web.get("/settings/users/").content.decode()
    assert "<th>Phone</th>" in users and "Not set up" in users
    _ready(web)
    assert ">Ready<" in web.get("/settings/users/").content.decode()
    rack = _sign_in_new("rack@slicks.test", "scanner")
    assert b"Set up this phone" not in rack.get("/phone/", HTTP_USER_AGENT=PHONE_UA).content     # the Scanner never approves sign-ins
