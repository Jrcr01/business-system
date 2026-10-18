"""Google Authenticator proves it is really them (owner, 2026-10-11: "the risk is my family's email will be hack" ... "google authenticator because it
ensures the user only holds his phone"): the 6-digit code is asked of everyone, once a day per device; an authenticator is set up or replaced only while
the System admin allows it; every sign-in is kept and a device not used before goes on the Review list; "Sign out everywhere"."""
from django.test import Client

from tests.conftest import fetchone
from tests.test_web_d0 import PW, _admin, _person, code_for, fetchone_uid, login

FAMILY = "family@slicks.test"


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _n(sql, params=()):
    return fetchone(_db(), sql, params)[0]


def _run(sql, params=()):
    from engines.common import get_conn
    with get_conn() as conn:
        conn.execute(sql, params)


def _new(email=FAMILY):
    from engines.auth_service import change_password
    temp = _person(email, "owner")
    assert change_password(fetchone_uid(email), temp, PW)[0]
    return fetchone_uid(email)


def _password(c, email=FAMILY, password=PW):
    return c.post("/login/", {"email": email, "password": password})


def test_the_code_is_asked_once_a_day_on_each_device(web, monkeypatch):
    _admin(web)
    _new()
    phone, laptop = Client(), Client()
    assert login(phone, FAMILY)["Location"] == "/"
    phone.post("/logout/")
    r = _password(phone)
    assert r["Location"] == "/" and phone.get("/").status_code == 200           # this device gave the code today: the password is enough
    assert _password(laptop)["Location"] == "/2fa/"                             # another device: its own code
    assert laptop.get("/")["Location"].startswith("/login/")
    phone.post("/logout/")
    import core.views as v
    monkeypatch.setattr(v, "_today", lambda: "2999-01-01")                      # the next day
    assert _password(phone)["Location"] == "/2fa/"
    assert phone.post("/2fa/", {"code": code_for(FAMILY)})["Location"] == "/"
    phone.post("/logout/")
    assert _password(phone)["Location"] == "/"
    assert _n("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'USER_SIGNED_IN' AND new_values LIKE '%gave the code earlier today%'") == 2


def test_a_wrong_password_never_gets_in_on_a_remembered_device(web):
    _admin(web)
    _new()
    c = Client()
    login(c, FAMILY)
    c.post("/logout/")
    r = _password(c, password="Wrong-one-77")
    assert r.status_code == 200 and c.get("/")["Location"].startswith("/login/")
    assert _n("SELECT COUNT(*) FROM sign_in_log WHERE result = 'PASSWORD_REFUSED'") == 1


def test_a_changed_password_or_sign_out_everywhere_asks_for_the_code_again(web):
    from engines.auth_service import change_password
    _admin(web)
    uid = _new()
    c = Client()
    login(c, FAMILY)
    assert change_password(uid, PW, "Another456x")[0]
    assert c.get("/")["Location"].startswith("/login/")                         # the old sign-in ended with the old password
    assert _password(c, password="Another456x")["Location"] == "/2fa/"          # and the device is asked again
    assert c.post("/2fa/", {"code": code_for(FAMILY)})["Location"] == "/"
    r = web.post(f"/settings/users/{uid}/", {"action": "signout_all"}, follow=True)
    assert b"signed out everywhere" in r.content
    assert c.get("/")["Location"].startswith("/login/")                         # signed out at once
    assert _password(c, password="Another456x")["Location"] == "/2fa/"          # and not remembered
    assert _n("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'USER_SIGNED_OUT_EVERYWHERE'") == 1


def test_only_the_system_admin_signs_someone_out_everywhere_or_allows_a_set_up(web):
    _admin(web)
    uid = _new()
    other = _new("brother@slicks.test")
    c = Client()
    login(c, FAMILY)
    assert c.post(f"/settings/users/{other}/", {"action": "signout_all"}).status_code == 403
    assert c.post(f"/settings/users/{other}/", {"action": "allow_code"}).status_code == 403
    assert _n("SELECT device_epoch FROM app_users WHERE user_id = ?", (other,)) == 0 and uid != other


def test_a_copied_or_changed_device_cookie_is_worth_nothing(web):
    from django.conf import settings
    _admin(web)
    _new()
    _new("brother@slicks.test")
    c = Client()
    login(c, FAMILY)
    c.post("/logout/")
    assert _password(c, "brother@slicks.test")["Location"] == "/2fa/setup/"     # the same browser, another person: their own code
    c.post("/2fa/setup/", {"cancel": "1"})
    c.cookies[settings.DEVICE_COOKIE_NAME] = c.cookies[settings.DEVICE_COOKIE_NAME].value[:-3] + "abc"
    assert _password(c)["Location"] == "/2fa/"                                  # tampered with: asked again


def test_set_up_only_while_the_system_admin_allows_it(web):
    _admin(web)
    uid = _new()
    _run("UPDATE app_users SET totp_setup_until = NULL WHERE user_id = ?", (uid,))     # e.g. an account from before, or the day has passed
    c = Client()
    assert _password(c)["Location"] == "/2fa/setup/"
    page = c.get("/2fa/setup/").content.decode()
    assert "Ask the System admin to allow your authenticator set-up" in page and "<svg" not in page and "Type this key" not in page
    assert fetchone(_db(), "SELECT totp_secret FROM app_users WHERE user_id = ?", (uid,))[0] is None     # no key is even made
    assert "Ask the System admin" in c.post("/2fa/setup/", {"code": "123456"}).content.decode()
    assert c.get("/")["Location"].startswith("/login/")
    page = web.get(f"/settings/users/{uid}/").content.decode()
    assert "They cannot sign in until you allow it" in page
    r = web.post(f"/settings/users/{uid}/", {"action": "allow_code"}, follow=True)
    assert "may set up an authenticator" in r.content.decode() and "Set-up allowed until" in r.content.decode()
    assert _n("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'USER_2FA_SETUP_ALLOWED'") == 1
    assert "<svg" in c.get("/2fa/setup/").content.decode()
    assert c.post("/2fa/setup/", {"code": code_for(FAMILY)})["Location"] == "/"
    assert fetchone(_db(), "SELECT totp_setup_until FROM app_users WHERE user_id = ?", (uid,))[0] is None       # used up
    assert _n("SELECT COUNT(*) FROM review_flags WHERE kind = 'AUTHENTICATOR_SET_UP' AND ref_id = ?", (uid,)) == 1
    assert _n("SELECT COUNT(*) FROM sign_in_log WHERE user_id = ? AND result = 'SETUP_REFUSED'", (uid,)) == 1
    assert _n("SELECT COUNT(*) FROM sign_in_log WHERE user_id = ? AND result = 'SET_UP'", (uid,)) == 1


def test_the_allowance_lasts_a_day_and_the_rule_is_in_the_engine_too(web):
    from engines.auth_service import SETUP_NOT_ALLOWED, totp_setup_secret, totp_setup_until, verify_totp
    _admin(web)
    uid = _new()
    assert totp_setup_until(uid)                                                # adding a person is the go-ahead, for a day
    _run("UPDATE app_users SET totp_setup_until = '2020-01-01 00:00:00' WHERE user_id = ?", (uid,))
    assert totp_setup_until(uid) is None
    c = Client()
    _password(c)
    assert "Ask the System admin" in c.get("/2fa/setup/").content.decode()
    totp_setup_secret(uid)                                                      # even with a key made some other way,
    assert verify_totp(uid, code_for(FAMILY)) == (False, SETUP_NOT_ALLOWED)     # the first code is refused
    assert fetchone(_db(), "SELECT totp_enabled_at FROM app_users WHERE user_id = ?", (uid,))[0] is None


def test_allow_is_for_a_person_without_an_authenticator(web):
    _admin(web)
    uid = _new()
    login(Client(), FAMILY)
    r = web.post(f"/settings/users/{uid}/", {"action": "allow_code"}, follow=True)
    assert "already has an authenticator" in r.content.decode()
    assert fetchone(_db(), "SELECT totp_setup_until FROM app_users WHERE user_id = ?", (uid,))[0] is None


def test_reset_code_forgets_the_devices_and_waits_for_allow(web):
    _admin(web)
    uid = _new()
    c = Client()
    login(c, FAMILY)
    r = web.post(f"/settings/users/{uid}/", {"action": "reset_code"}, follow=True)
    assert "Allow authenticator set-up" in r.content.decode()
    assert c.get("/")["Location"].startswith("/login/")                         # signed out, and the device no longer counts
    assert _password(c)["Location"] == "/2fa/setup/"
    assert "Ask the System admin" in c.get("/2fa/setup/").content.decode()
    web.post(f"/settings/users/{uid}/", {"action": "allow_code"})
    assert "<svg" in c.get("/2fa/setup/").content.decode()


def test_the_system_admin_resetting_their_own_code_is_not_locked_out(web):
    _admin(web)
    uid = fetchone_uid("admin@slicks.test")
    web.post(f"/settings/users/{uid}/", {"action": "reset_code"})
    assert web.get("/")["Location"].startswith("/login/")
    assert _password(web, "admin@slicks.test")["Location"] == "/2fa/setup/"
    assert "<svg" in web.get("/2fa/setup/").content.decode()
    assert web.post("/2fa/setup/", {"code": code_for("admin@slicks.test")})["Location"] == "/"


def test_every_sign_in_is_kept_and_a_new_device_goes_on_the_review_list(web):
    _admin(web)
    uid = _new()
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
    pc = Client(HTTP_USER_AGENT=ua, HTTP_CF_IPCOUNTRY="PH", HTTP_CF_IPCITY="Baliuag", HTTP_CF_CONNECTING_IP="203.0.113.7")
    login(pc, FAMILY)
    assert _n("SELECT COUNT(*) FROM review_flags WHERE kind = 'NEW_DEVICE_SIGN_IN' AND ref_id = ?", (uid,)) == 1
    detail = fetchone(_db(), "SELECT detail FROM review_flags WHERE kind = 'NEW_DEVICE_SIGN_IN' AND ref_id = ?", (uid,))[0]
    assert "Chrome on Windows" in detail and "Baliuag, PH" in detail
    pc.post("/logout/")
    _password(pc)                                                               # the same device again: nothing new
    assert _n("SELECT COUNT(*) FROM review_flags WHERE kind = 'NEW_DEVICE_SIGN_IN' AND ref_id = ?", (uid,)) == 1
    phone = Client(HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1")
    _password(phone)
    phone.post("/2fa/", {"code": "000000"})
    assert phone.post("/2fa/", {"code": code_for(FAMILY)})["Location"] == "/"
    assert _n("SELECT COUNT(*) FROM review_flags WHERE kind = 'NEW_DEVICE_SIGN_IN' AND ref_id = ?", (uid,)) == 2
    got = [r[0] for r in __import__("sqlite3").connect(_db()).execute("SELECT result FROM sign_in_log WHERE user_id = ? ORDER BY log_id", (uid,))]
    assert got == ["SET_UP", "REMEMBERED", "WRONG_CODE", "CODE"]
    page = web.get(f"/settings/users/{uid}/").content.decode()
    assert "Signed in: password and code" in page and "Not signed in: wrong code" in page and "new device" in page
    assert "Safari on iPhone" in page and "Baliuag, PH" in page and "203.0.113.7" in page
    assert "Signed in on a device not used before" in web.get("/review/?tab=signins").content.decode()


def test_the_code_can_be_switched_off_and_then_cloudflare_stands_in(web, monkeypatch):
    # owner, 2026-10-12: "turnoff google authenticator but instead i will lower session duration" -> "for now disable google authenticator"
    from django.conf import settings
    from core import cfaccess
    from engines.common import get_conn
    _admin(web)
    _new()

    def switch(v):
        with get_conn() as conn:
            conn.execute("DELETE FROM settings WHERE key = 'signin_authenticator'")
            conn.execute("INSERT INTO settings (key, value, category, description) VALUES ('signin_authenticator', ?, 'Sign-in', '')", (str(v),))
            conn.commit()

    switch(0)
    alone = Client()
    assert _password(alone)["Location"] .startswith("/2fa/")                              # no Cloudflare in front: the code is still asked
    monkeypatch.setattr(settings, "CF_ACCESS_TEAM", "team")
    monkeypatch.setattr(settings, "CF_ACCESS_AUD", "aud")
    monkeypatch.setattr(cfaccess, "verify", lambda token, team, aud: "someone.else@example.com")
    other = _password(Client())                                                 # Cloudflare checked another email: never signed in by the password alone
    assert other.status_code != 302 or other["Location"].startswith("/2fa/")
    monkeypatch.setattr(cfaccess, "verify", lambda token, team, aud: FAMILY)
    c = Client()
    assert _password(c)["Location"] == "/" and c.get("/").status_code == 200    # Cloudflare checked this very person: the password is enough
    assert _n("SELECT COUNT(*) FROM sign_in_log WHERE result = 'CLOUDFLARE'") == 1
    switch(1)
    assert c.get("/")["Location"].startswith("/login/")                         # the code is asked again: that sign-in is over
    assert _password(c)["Location"] .startswith("/2fa/")


def test_no_activity_signs_a_person_out_after_the_minutes_in_settings(web, monkeypatch):
    # owner, 2026-10-12: "is it possible to auto logout for inactivity?" -> "30 minutes with the warning, go"
    import time
    from engines.settings_registry import idle_minutes
    _admin(web)
    assert idle_minutes() == 30
    page = web.get("/").content.decode()
    assert 'id="idle-warn"' in page and "total = 1800" in page                  # the page counts down from the same 30 minutes
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 29 * 60)
    assert web.get("/").status_code == 200                                      # 29 minutes: still in, and the clock starts again
    monkeypatch.setattr(time, "time", lambda: real() + 29 * 60 + 31 * 60)
    r = web.get("/creditors/")
    assert r.status_code == 302 and r["Location"].startswith("/login/")         # 31 minutes with nothing touched: signed out
    assert "signed out after 30 minutes" in web.get("/login/?idle=1").content.decode()
