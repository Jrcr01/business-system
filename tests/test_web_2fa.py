"""The 6-digit sign-in code (owner, 2026-10-08): username, password, then the code from an authenticator app. And the per-person switch that keeps someone out
of the old screens (/more/)."""
from django.test import Client

from tests.conftest import fetchone
from tests.test_web_d0 import PW, _admin, _person, code_for, fetchone_uid, login


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _new(email="family@slicks.test", role="owner"):
    from engines.auth_service import change_password
    temp = _person(email, role)
    assert change_password(fetchone_uid(email), temp, PW)[0]
    return fetchone_uid(email)


def test_the_password_alone_does_not_sign_anyone_in(web):
    _admin(web)
    _new()
    c = Client()
    r = c.post("/login/", {"email": "family@slicks.test", "password": PW})
    assert r["Location"] == "/2fa/setup/"                                    # first time: set the code up
    for page in ("/", "/calendar/", "/settings/users/"):
        assert c.get(page)["Location"].startswith("/login/")                # nothing else is open
    page = c.get("/2fa/setup/").content.decode()
    assert "<svg" in page and "Set up your sign-in code" in page
    r = c.post("/2fa/setup/", {"code": code_for("family@slicks.test")})
    assert r["Location"] == "/" and c.get("/").status_code == 200
    c.post("/logout/")
    r = c.post("/login/", {"email": "family@slicks.test", "password": PW})
    assert r["Location"] == "/2fa/"                                          # from now on: the code
    assert c.get("/").status_code == 302


def test_a_wrong_code_counts_toward_the_lock_and_a_code_works_once(web):
    from engines.auth_service import MAX_TRIES
    _admin(web)
    _new()
    c = Client()
    assert login(c, "family@slicks.test").status_code == 302
    used = fetchone(_db(), "SELECT totp_last_step FROM app_users WHERE email = 'family@slicks.test'")[0]
    c.post("/logout/")
    c.post("/login/", {"email": "family@slicks.test", "password": PW})
    from engines.auth_service import _totp_at
    secret = fetchone(_db(), "SELECT totp_secret FROM app_users WHERE email = 'family@slicks.test'")[0]
    r = c.post("/2fa/", {"code": _totp_at(secret, used)})                    # the code already used
    assert r.status_code == 200 and b"didn" in r.content
    for _ in range(MAX_TRIES - 2):
        assert c.post("/2fa/", {"code": "000000"}).status_code == 200
    r = c.post("/2fa/", {"code": "000000"})
    assert b"locked" in r.content
    assert c.post("/2fa/", {"code": code_for("family@slicks.test")}).status_code == 200       # even the right code waits out the lock
    assert c.get("/").status_code == 302
    assert fetchone(_db(), "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'USER_LOCKED'")[0] == 1


def test_signing_in_again_with_the_password_does_not_clear_wrong_codes(web):
    from engines.auth_service import MAX_TRIES
    _admin(web)
    _new()
    login(Client(), "family@slicks.test")
    for _ in range(MAX_TRIES):
        c = Client()
        c.post("/login/", {"email": "family@slicks.test", "password": PW})
        c.post("/2fa/", {"code": "000000"})
    assert fetchone(_db(), "SELECT locked_until FROM app_users WHERE email = 'family@slicks.test'")[0]


def test_only_the_system_admin_resets_a_code(web):
    _admin(web)
    uid = _new()
    family = Client()
    login(family, "family@slicks.test")
    assert "Set up 20" in web.get(f"/settings/users/{uid}/").content.decode()
    assert family.post(f"/settings/users/{uid}/", {"action": "reset_code"}).status_code == 403
    web.post(f"/settings/users/{uid}/", {"action": "reset_code"})
    assert fetchone(_db(), "SELECT totp_secret, totp_enabled_at FROM app_users WHERE user_id = ?", (uid,)) == (None, None)
    assert fetchone(_db(), "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'USER_2FA_RESET'")[0] == 1
    family.post("/logout/")
    r = family.post("/login/", {"email": "family@slicks.test", "password": PW})
    assert r["Location"] == "/2fa/setup/"                                    # a new phone: set it up again


def test_the_server_command_resets_the_admins_own_code(web):
    from django.core.management import call_command
    _admin(web)
    call_command("reset_2fa", "admin@slicks.test")
    assert fetchone(_db(), "SELECT totp_enabled_at FROM app_users WHERE email = 'admin@slicks.test'")[0] is None


def test_a_sign_in_from_before_the_code_is_ended(web):
    from django.conf import settings
    from django.contrib.sessions.backends.signed_cookies import SessionStore
    from core.middleware import password_stamp
    _admin(web)
    uid = fetchone_uid("admin@slicks.test")
    old = SessionStore()                                                     # what a browser signed in before the code still carries
    old["uid"], old["pw"] = uid, password_stamp(uid)
    old.save()
    c = Client()
    c.cookies[settings.SESSION_COOKIE_NAME] = old.session_key
    assert c.get("/")["Location"].startswith("/login/")


def test_the_old_screens_switch_keeps_a_person_out_of_more_but_not_the_main_system(web):
    from engines.auth_service import MORE_BLOCKED, authenticate, set_restrictions
    _admin(web)
    uid = _new()
    assert authenticate("family@slicks.test", PW)[0]                          # /more/ by default
    assert set_restrictions(uid, {"more.use"})[0]
    ok, _, msg = authenticate("family@slicks.test", PW)
    assert not ok and msg == MORE_BLOCKED
    assert login(Client(), "family@slicks.test")["Location"] == "/"          # the main system still lets them in
    assert authenticate("admin@slicks.test", PW)[0]                           # the System admin is never switched off


def test_sign_in_is_by_email_not_the_username(web):
    # owner, 2026-10-09: "disregard username but instead use their email address"
    from engines.auth_service import authenticate
    _admin(web)
    _new()
    c = Client()
    r = c.post("/login/", {"email": "family", "password": PW})
    assert r.status_code == 200 and b"Wrong email or password." in r.content                 # the username no longer signs in to the main system
    assert c.post("/login/", {"email": "FAMILY@Slicks.test", "password": PW})["Location"] == "/2fa/setup/"    # capitals don't matter
    assert authenticate("family@slicks.test", PW)[0] and authenticate("family", PW)[0]               # the old screens' sign-in takes either
    page = Client().get("/login/").content.decode()
    assert 'name="email"' in page and 'name="username"' not in page


def test_a_new_person_gets_the_username_typed_and_old_accounts_get_one_from_the_email(web):
    from engines.auth_service import create_user, fill_missing_usernames
    from engines.common import write_tx
    _admin(web)
    web.post("/settings/users/", {"name": "Co Owner", "username": "coowner", "email": "co@slicks.test", "role": "owner"})
    assert fetchone(_db(), "SELECT username FROM app_users WHERE email = 'co@slicks.test'")[0] == "coowner"
    assert not create_user("x@slicks.test", "X", "owner", "no spaces")[0]
    with write_tx() as conn:                                                   # an account from before usernames
        conn.execute("UPDATE app_users SET username = NULL WHERE email = 'co@slicks.test'")
    assert fill_missing_usernames() == [("co@slicks.test", "co0")] and fill_missing_usernames() == []


def test_the_sandbox_switch_skips_sign_in_and_is_off_unless_set(web):
    from django.conf import settings
    from django.test import override_settings
    _admin(web)
    _new()
    web.post("/logout/")
    assert settings.SANDBOX_NO_SIGNIN is False                                          # off unless run_web_sandbox.bat sets it (never on the server)
    assert "Sandbox: sign in as" not in web.get("/login/").content.decode()
    with override_settings(SANDBOX_NO_SIGNIN=True):
        page = web.get("/login/").content.decode()
        assert "Sandbox: sign in as" in page and "Family" in page
        r = web.post("/login/", {"uid": fetchone_uid("family@slicks.test")})
        assert r["Location"] == "/" and web.get("/").status_code == 200                # no password, no code


def test_the_admin_changes_a_persons_email_and_they_sign_in_with_the_new_one(web):
    # owner, 2026-10-09: "how about change of email?"
    _admin(web)
    uid = _new()
    r = web.post(f"/settings/users/{uid}/", {"action": "email", "email": "Family.New@Slicks.test "}, follow=True)
    assert b"The email is now family.new@slicks.test" in r.content
    assert fetchone(_db(), "SELECT email FROM app_users WHERE user_id = ?", (uid,))[0] == "family.new@slicks.test"
    assert fetchone(_db(), "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'USER_EMAIL_CHANGED'")[0] == 1
    assert Client().post("/login/", {"email": "family@slicks.test", "password": PW}).status_code == 200      # the old one no longer works
    assert Client().post("/login/", {"email": "family.new@slicks.test", "password": PW})["Location"] == "/2fa/setup/"
    r = web.post(f"/settings/users/{uid}/", {"action": "email", "email": "admin@slicks.test"}, follow=True)
    assert b"Another account already uses admin@slicks.test" in r.content
    r = web.post(f"/settings/users/{uid}/", {"action": "email", "email": "not-an-email"}, follow=True)
    assert b"Type a valid email address" in r.content
