"""Fingerprint sign-in (owner, 2026-10-13): the device's own fingerprint stands in for typing the password on that device. The "device" here is a
software one: a key pair that signs what a phone would sign."""
import hashlib
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from tests.test_web_d0 import PW, _admin, _person, fetchone_uid, login

ORIGIN, RP = "http://testserver", "testserver"


def b64(data):
    from engines.fingerprint_signin import b64 as f
    return f(data)


class Device:
    def __init__(self):
        self.key, self.cred, self.count = ec.generate_private_key(ec.SECP256R1()), b"cred-" + hashlib.sha256(str(id(self)).encode()).digest()[:11], 0

    def _auth(self, flags, with_key=False, rp=RP):
        self.count += 1
        data = hashlib.sha256(rp.encode()).digest() + bytes([flags]) + self.count.to_bytes(4, "big")
        return data + (b"\0" * 16 + len(self.cred).to_bytes(2, "big") + self.cred + b"cose" if with_key else b"")

    def _client(self, kind, challenge, origin=ORIGIN):
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin}).encode()

    def create(self, challenge, flags=0x45, origin=ORIGIN):
        spki = self.key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        return {"id": b64(self.cred), "clientDataJSON": b64(self._client("webauthn.create", challenge, origin)),
                "authenticatorData": b64(self._auth(flags, True)), "publicKey": b64(spki), "alg": -7}

    def get(self, challenge, flags=0x05, origin=ORIGIN, key=None, rp=RP):
        client, auth = self._client("webauthn.get", challenge, origin), self._auth(flags, rp=rp)
        sig = (key or self.key).sign(auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": b64(self.cred), "clientDataJSON": b64(client), "authenticatorData": b64(auth), "signature": b64(sig), "next": "/expenses/"}


def _post(web, url, data):
    return web.post(url, json.dumps(data), content_type="application/json")


def _turn_on(web, device=None):
    device = device or Device()
    o = web.get("/fingerprint/options/").json()
    assert o["ok"] and o["publicKey"]["authenticatorSelection"]["userVerification"] == "required"
    r = _post(web, "/fingerprint/add/", device.create(o["publicKey"]["challenge"]))
    assert r.status_code == 200 and r.json()["ok"], r.content
    return device


def _fingerprint(web, device, **kw):
    o = web.get("/login/fingerprint/options/")
    if o.status_code != 200:
        return o
    return _post(web, "/login/fingerprint/", device.get(o.json()["publicKey"]["challenge"], **kw))


def test_turn_it_on_then_sign_in_with_the_fingerprint(web):
    _admin(web)
    assert b"Sign in faster on this device" in web.get("/").content            # offered after a password sign-in
    device = _turn_on(web)
    assert b"Sign in faster on this device" not in web.get("/").content
    assert b"this device" in web.get("/fingerprint/").content
    web.post("/logout/")
    assert b"Sign in using fingerprint" in web.get("/login/").content
    r = _fingerprint(web, device)
    assert r.status_code == 200 and r.json() == {"ok": True, "next": "/expenses/"}
    assert web.get("/").status_code == 200
    from engines.auth_service import recent_sign_ins
    assert recent_sign_ins(fetchone_uid("admin@slicks.test"))[0]["result"] == "FINGERPRINT"


def test_a_browser_where_it_was_never_turned_on_gets_no_link_and_no_way_in(web):
    from django.test import Client
    _admin(web)
    device = _turn_on(web)
    other = Client()
    assert b"Sign in using fingerprint" not in other.get("/login/").content
    assert other.get("/login/fingerprint/options/").status_code == 400
    assert _post(other, "/login/fingerprint/", device.get("anything")).status_code == 400
    assert other.get("/")["Location"].startswith("/login/")


def test_what_is_not_the_right_device_is_refused(web):
    _admin(web)
    device = _turn_on(web)
    web.post("/logout/")
    wrong_key = ec.generate_private_key(ec.SECP256R1())
    for kw in ({"key": wrong_key}, {"flags": 0x01}, {"origin": "https://evil.example"}, {"rp": "evil.example"}):
        r = _fingerprint(web, device, **kw)
        assert r.status_code == 400 and not r.json()["ok"], kw
        assert web.get("/")["Location"].startswith("/login/")
    o = web.get("/login/fingerprint/options/").json()                          # an answer is good once only
    answer = device.get(o["publicKey"]["challenge"])
    assert _post(web, "/login/fingerprint/", answer).status_code == 200
    web.post("/logout/")
    assert _post(web, "/login/fingerprint/", answer).status_code == 400
    from engines.auth_service import recent_sign_ins
    assert "FINGERPRINT_REFUSED" in [s["result"] for s in recent_sign_ins(fetchone_uid("admin@slicks.test"))]


def test_turning_it_on_needs_a_verified_person_and_our_own_challenge(web):
    _admin(web)
    d = Device()
    o = web.get("/fingerprint/options/").json()
    assert _post(web, "/fingerprint/add/", d.create(o["publicKey"]["challenge"], flags=0x41)).status_code == 400      # present, not verified
    assert _post(web, "/fingerprint/add/", d.create(o["publicKey"]["challenge"])).status_code == 400                  # that challenge is spent
    o = web.get("/fingerprint/options/").json()
    assert _post(web, "/fingerprint/add/", d.create(o["publicKey"]["challenge"], origin="https://evil.example")).status_code == 400
    from engines.fingerprint_signin import devices
    assert devices() == []


def test_a_changed_password_and_remove_end_it(web):
    from engines.auth_service import change_password
    from engines.fingerprint_signin import devices
    _admin(web)
    device = _turn_on(web)
    uid = fetchone_uid("admin@slicks.test")
    ok, msg = change_password(uid, PW, PW + "-new1")
    assert ok, msg
    assert devices(uid) == []
    assert b"Sign in using fingerprint" not in web.get("/login/").content
    assert _fingerprint(web, device).status_code == 400
    assert login(web, "admin@slicks.test", PW + "-new1").status_code == 302
    device = _turn_on(web)
    key_id = devices(uid)[0]["key_id"]
    assert web.post("/fingerprint/", {"key_id": key_id}).status_code == 302
    assert devices(uid) == []
    web.post("/logout/")
    assert b"Sign in using fingerprint" not in web.get("/login/").content


def test_the_admin_removes_a_lost_device_and_the_switch_turns_it_all_off(web):
    from django.test import Client
    from engines import settings_registry as sr
    from engines.acting_user import acting_as
    from engines.auth_service import change_password
    from engines.fingerprint_signin import devices
    _admin(web)
    temp = _person("ana@slicks.test", "owner")
    uid = fetchone_uid("ana@slicks.test")
    assert change_password(uid, temp, PW)[0]
    ana = Client()
    assert login(ana, "ana@slicks.test").status_code == 302
    device = _turn_on(ana)
    assert b"Fingerprint sign-in" in web.get(f"/settings/users/{uid}/").content
    assert web.post(f"/settings/users/{uid}/", {"action": "fp_remove", "key_id": devices(uid)[0]["key_id"]}).status_code == 302
    assert devices(uid) == []
    device = _turn_on(ana)
    ana.post("/logout/")
    with acting_as("admin@slicks.test", "admin"):
        sr.save("signin_fingerprint", "0")
    assert b"Sign in using fingerprint" not in ana.get("/login/").content
    assert _fingerprint(ana, device).status_code == 400
    assert web.get("/fingerprint/").status_code == 302 and b"Sign in faster" not in web.get("/").content


def test_the_scanner_account_never_uses_it(web):
    from django.test import Client
    from engines.auth_service import change_password
    _admin(web)
    temp = _person("rack@slicks.test", "scanner")
    assert change_password(fetchone_uid("rack@slicks.test"), temp, PW)[0]
    c = Client()
    login(c, "rack@slicks.test")
    assert c.get("/fingerprint/options/").status_code == 400


def test_the_idle_page_really_signs_out_and_a_signed_in_person_skips_the_form(web):
    # owner, 2026-10-14: the page's countdown showed "You were signed out" while the menu and every page still opened
    _admin(web)
    r = web.get("/login/?next=/expenses/")
    assert r.status_code == 302 and r["Location"] == "/expenses/"
    page = web.get("/login/?idle=1&next=/expenses/").content.decode()
    assert "You were signed out" in page and 'class="tb-user' not in page and "Change password" not in page
    assert web.get("/")["Location"].startswith("/login/")
