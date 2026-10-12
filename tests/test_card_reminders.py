"""Credit card reminders on the phone (owner, 2026-10-13: a daily payables message "will only stresses us. instead have credit cards only
incoming statement, due date, annual fee"): due date only, twice that day; the statement two days after its day; the fee a month before."""
import json
import sqlite3
from datetime import date, datetime

import pytest

from conftest import fetchone, make_creditor
from tests.test_phone import _db, _n
from tests.test_web_d0 import _admin, _sign_in_new


def _card(db, name="Platinum", last4="2409", holder="Admin Roxas", statement_day=12, fee_month=12):
    conn = sqlite3.connect(db)
    issuer = conn.execute("SELECT creditor_id FROM creditors WHERE creditor_name = 'BPI'").fetchone()
    conn.close()
    issuer = issuer[0] if issuer else make_creditor(db, name="BPI", creditor_type="CREDIT_CARD")
    conn = sqlite3.connect(db)
    hid = conn.execute("INSERT INTO cardholders (name) VALUES (?)", (holder,)).lastrowid
    cid = conn.execute("INSERT INTO credit_cards (creditor_id, cardholder_id, card_name, card_number_last4, statement_day, annual_fee_due_month) VALUES (?, ?, ?, ?, ?, ?)",
                       (issuer, hid, name, last4, statement_day, fee_month)).lastrowid
    conn.commit()
    conn.close()
    return cid, hid


def _statement(db, card_id, due, total, paid=0, status="Unpaid"):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO cc_statements (card_id, due_date, total_amount_due, amount_paid, status) VALUES (?, ?, ?, ?, ?)", (card_id, due, total, paid, status))
    conn.commit()
    conn.close()


@pytest.fixture
def sent(monkeypatch):
    """Every push the system tries to send, instead of the call out: [(endpoint, message as the phone would read it)]."""
    from engines import web_push
    out = []
    real = web_push.encrypt

    def keep(message, p256dh, auth):
        out.append(json.loads(message))
        return real(message, p256dh, auth)
    monkeypatch.setattr(web_push, "encrypt", keep)
    monkeypatch.setattr(web_push, "_post", lambda endpoint, body, headers: 201)
    return out


def _phone(email, n=1):
    """A phone allowed by that person (a real key pair, so the message can be encrypted for it)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from engines import web_push
    key = ec.generate_private_key(ec.SECP256R1())
    raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    ok, msg = web_push.subscribe(email, {"endpoint": f"https://push.example/{email}/{n}", "keys": {"p256dh": web_push._b64(raw), "auth": web_push._b64(b"0123456789abcdef")}})
    assert ok, msg
    return key


def test_the_message_is_one_only_that_phone_can_read(web, db):
    # RFC 8291, read back the way a phone does
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from engines import web_push
    _admin(web)
    key = _phone("admin@slicks.test")
    raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    body = web_push.encrypt(b'{"title": "x"}', web_push._b64(raw), web_push._b64(b"0123456789abcdef"))
    salt, size, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    theirs = body[21:21 + idlen]
    shared = key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), theirs))
    ikm = HKDF(hashes.SHA256(), 32, b"0123456789abcdef", b"WebPush: info\x00" + raw + theirs).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    assert size == 4096 and AESGCM(cek).decrypt(nonce, body[21 + idlen:], None) == b'{"title": "x"}\x02'
    head = web_push._vapid("https://fcm.googleapis.com/fcm/send/abc")
    assert head.startswith("vapid t=") and f"k={web_push.public_key()}" in head
    assert web_push.public_key() == web_push.public_key()                                  # the key pair is made once and kept


def test_due_today_is_said_twice_and_not_once_paid(web, db, sent):
    from engines import card_reminders as cr
    _admin(web)
    _phone("admin@slicks.test")
    card, _ = _card(db, statement_day=None)
    _statement(db, card, "2026-10-14", 25000, paid=1000, status="Partially Paid")
    assert cr.run(datetime(2026, 10, 14, 7, 5)) == "Not a reminder hour."
    assert cr.run(datetime(2026, 10, 13, 8, 0)) == "0 reminder(s) sent (am)."               # the day before: nothing ("Due Date only")
    assert cr.run(datetime(2026, 10, 14, 8, 2)) == "1 reminder(s) sent (am)."
    assert sent[-1]["title"] == "Platinum 2409 · due today" and "₱24,000.00 due today, 14 Oct" in sent[-1]["body"] and sent[-1]["url"] == "/checks/"
    assert cr.run(datetime(2026, 10, 14, 8, 40)) == "0 reminder(s) sent (am)."              # the timer again the same hour: not twice
    assert cr.run(datetime(2026, 10, 14, 14, 0)) == "1 reminder(s) sent (pm)." and "Second reminder" in sent[-1]["body"]
    assert len(sent) == 2
    assert _n("SELECT phones FROM card_reminders_sent WHERE slot = 'pm'") == 1
    # another card, paid by 2 PM: the morning one only
    other, _ = _card(db, "Gold", "7731", "Rhoda Cruz", statement_day=None)
    _statement(db, other, "2026-10-15", 9000)
    assert cr.run(datetime(2026, 10, 15, 8, 0)) == "1 reminder(s) sent (am)."
    sqlite3.connect(db).execute("UPDATE cc_statements SET status = 'Paid', amount_paid = 9000 WHERE card_id = ?", (other,)).connection.commit()
    assert cr.run(datetime(2026, 10, 15, 14, 0)) == "0 reminder(s) sent (pm)."


def test_the_statement_two_days_after_its_day_until_it_is_recorded(web, db, sent):
    from engines import card_reminders as cr
    _admin(web)
    _phone("admin@slicks.test")
    card, _ = _card(db, statement_day=12)
    assert cr.run(datetime(2026, 10, 12, 8, 0)) == "0 reminder(s) sent (am)."               # the statement day itself: the bank has not sent it yet
    assert cr.run(datetime(2026, 10, 14, 14, 0)) == "0 reminder(s) sent (pm)."              # mornings only
    assert cr.run(datetime(2026, 10, 14, 8, 0)) == "1 reminder(s) sent (am)."
    assert sent[-1]["title"] == "Platinum 2409 · statement is out" and "closed its statement on 12 Oct" in sent[-1]["body"]
    # a card whose October statement is already recorded says nothing
    done, _ = _card(db, "Gold", "7731", "Rhoda Cruz", statement_day=20)
    _statement(db, done, "2026-11-10", 5000)
    assert cr.run(datetime(2026, 10, 22, 8, 0)) == "0 reminder(s) sent (am)."
    # a statement day of 31 in a 30-day month closes on the 30th
    _card(db, "Visa", "1010", "Jun Reyes", statement_day=31)
    assert cr.run(datetime(2026, 12, 2, 8, 0)) == "1 reminder(s) sent (am)." and "30 Nov" in sent[-1]["body"]


def test_the_annual_fee_a_month_before_and_the_switches(web, db, sent):
    from engines import card_reminders as cr
    from engines.settings_store import set_setting
    _admin(web)
    _phone("admin@slicks.test")
    _card(db, fee_month=12, statement_day=None)
    _card(db, "Gold", "7731", "Rhoda Cruz", fee_month=1, statement_day=None)
    assert cr.run(datetime(2026, 11, 2, 8, 0)) == "0 reminder(s) sent (am)."                # only on the 1st
    assert cr.run(datetime(2026, 11, 1, 8, 0)) == "1 reminder(s) sent (am)." and sent[-1]["title"] == "Platinum 2409 · annual fee in December"
    assert cr.run(datetime(2026, 12, 1, 8, 0)) == "1 reminder(s) sent (am)." and sent[-1]["title"] == "Gold 7731 · annual fee in January"
    assert _n("SELECT key FROM card_reminders_sent WHERE kind = 'FEE' ORDER BY rowid DESC") == "2027-01"
    set_setting("notify_card_fee", "0")
    assert cr.run(datetime(2027, 11, 1, 8, 0)) == "0 reminder(s) sent (am)."
    set_setting("notify_hour", "9")                                                           # the hour is a setting
    assert cr.run(datetime(2027, 12, 1, 8, 0)) == "Not a reminder hour."


def test_the_admin_gets_every_card_a_cardholder_their_own_or_all(web, db, sent):
    from engines import card_reminders as cr
    _admin(web)
    _sign_in_new("rhoda@slicks.test", "owner")                                               # named "Rhoda": the cardholder Rhoda Cruz
    _sign_in_new("julian@slicks.test", "owner")
    for email in ("admin@slicks.test", "rhoda@slicks.test", "julian@slicks.test"):
        _phone(email)
    mine, _ = _card(db, "Platinum", "2409", "Admin Roxas", statement_day=None)
    hers, hid = _card(db, "Gold", "7731", "Rhoda Cruz", statement_day=None)
    got = {p["email"]: (p["scope"], p["cardholder_id"]) for p in cr.people()}
    assert got["admin@slicks.test"][0] == "all" and got["rhoda@slicks.test"] == ("own", hid) and got["julian@slicks.test"][0] == "off"
    _statement(db, hers, "2026-10-14", 5000)
    _statement(db, mine, "2026-10-14", 7000)
    cr.run(datetime(2026, 10, 14, 8, 0))
    assert sorted(m["title"] for m in sent) == ["Gold 7731 · due today", "Gold 7731 · due today", "Platinum 2409 · due today"]      # hers to her and the admin; the admin's to the admin
    # "have an option to all": the admin gives Julian every card
    page = web.get("/settings/notifications/").content.decode()
    assert "Who receives them" in page and "julian@slicks.test" in page and "not allowed yet" not in page.split("julian@slicks.test")[1][:300]
    assert web.post("/settings/notifications/", {"email": "julian@slicks.test", "scope": "all", "cardholder_id": ""}).status_code == 302
    del sent[:]
    cr.run(datetime(2026, 10, 14, 14, 0))
    assert len(sent) == 5                                                                     # 2 cards to the admin and Julian, hers to her as well
    # only the System admin chooses
    owner = _sign_in_new("pat@slicks.test", "owner")
    assert owner.get("/settings/notifications/").status_code == 403


def test_a_person_allows_a_phone_tests_it_and_a_gone_phone_is_forgotten(web, db, monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from engines import web_push
    _admin(web)
    page = web.get("/notifications/").content.decode()
    assert "Allow on this phone" in page and web_push.public_key() in page and 'href="/notifications/"' in web.get("/").content.decode()
    raw = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    sub = {"endpoint": "https://push.example/a", "keys": {"p256dh": web_push._b64(raw), "auth": web_push._b64(b"0123456789abcdef")}}
    r = web.post("/notifications/", json.dumps({"action": "on", "subscription": sub}), content_type="application/json")
    assert r.json()["ok"] and _n("SELECT COUNT(*) FROM push_subscriptions WHERE user_email = 'admin@slicks.test'") == 1
    web.post("/notifications/", json.dumps({"action": "on", "subscription": sub}), content_type="application/json")
    assert _n("SELECT COUNT(*) FROM push_subscriptions") == 1                                # the same phone again: still one
    assert not web.post("/notifications/", json.dumps({"action": "on", "subscription": {"endpoint": "http://x"}}), content_type="application/json").json()["ok"]
    calls = []
    monkeypatch.setattr(web_push, "_post", lambda endpoint, body, headers: calls.append((endpoint, headers)) or 201)
    assert web.post("/notifications/", json.dumps({"action": "test"}), content_type="application/json").json()["ok"]
    assert calls[0][0] == "https://push.example/a" and calls[0][1]["Content-Encoding"] == "aes128gcm" and calls[0][1]["Authorization"].startswith("vapid t=")
    monkeypatch.setattr(web_push, "_post", lambda endpoint, body, headers: 410)              # the push service: that phone is gone
    assert not web.post("/notifications/", json.dumps({"action": "test"}), content_type="application/json").json()["ok"]
    assert _n("SELECT COUNT(*) FROM push_subscriptions") == 0
    sw = web.get("/sw.js")
    assert b'addEventListener("push"' in b"".join(sw.streaming_content)


def test_the_hourly_command(web, db, sent):
    from io import StringIO

    from django.core.management import call_command
    _admin(web)
    _phone("admin@slicks.test")
    card, _ = _card(db, statement_day=None)
    _statement(db, card, str(date.today()), 100)
    from engines.clock import manila_today, set_fake_now
    set_fake_now(datetime(2026, 10, 14, 3, 0))
    try:
        out = StringIO()
        call_command("card_reminders", stdout=out)
        assert "Not a reminder hour" in out.getvalue()
        sqlite3.connect(db).execute("UPDATE cc_statements SET due_date = ?", (str(manila_today()),)).connection.commit()
        out = StringIO()
        call_command("card_reminders", "--slot", "am", stdout=out)
        assert "1 reminder(s) sent (am)" in out.getvalue() and sent[-1]["title"].endswith("due today")
    finally:
        set_fake_now(None)
