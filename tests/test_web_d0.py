"""Stage D0 of the Django rebuild (docs/DJANGO_REBUILD_PROPOSAL.md): first-run set-up, sign-in that survives a refresh, the forced password change, roles in the
top bar and on every page, Home's tiles, and who every save is recorded as."""
import pytest

from tests.conftest import fetchone

PW = "Sandbox123"


def code_for(email):
    """The 6-digit code the person's authenticator app would show, never one already used (the system accepts one step either side of now)."""
    from engines.auth_service import _now_step, _totp_at
    from engines.common import get_conn
    with get_conn() as conn:
        secret, last = conn.execute("SELECT totp_secret, totp_last_step FROM app_users WHERE email = ?", (email,)).fetchone()
    step = max(_now_step() - 1, (last or 0) + 1)
    assert step <= _now_step() + 1, "no unused code left in this half-minute"
    return _totp_at(secret, step)


def _code_step(c, r, email):
    """After a right password: set up the code (first time) or enter it. Returns the response of the code step."""
    assert r.status_code == 302 and r["Location"] in ("/2fa/", "/2fa/setup/"), getattr(r, "content", b"")[:300]
    if r["Location"] == "/2fa/setup/":
        assert c.get("/2fa/setup/").status_code == 200                      # makes the key
    return c.post(r["Location"], {"code": code_for(email)})


def username_of(email):
    from engines.common import get_conn
    with get_conn() as conn:
        return conn.execute("SELECT username FROM app_users WHERE email = ?", (email,)).fetchone()[0]


def login(c, email, password=PW, nxt=None):
    """Sign in the whole way: the person's email (owner, 2026-10-09), password, then the 6-digit code."""
    r = c.post("/login/", {"email": email, "password": password, **({"next": nxt} if nxt else {})})
    return _code_step(c, r, email)


def _admin(web):
    r = web.post("/setup/", {"name": "Admin", "email": "admin@slicks.test", "password": PW, "password2": PW})
    r = _code_step(web, r, "admin@slicks.test")
    assert r.status_code == 302 and r["Location"] == "/"
    return web


def _person(email, role):
    """A person added by the System admin; returns the temporary password."""
    from engines.auth_service import create_user
    ok, msg, uid, temp = create_user(email, email.split("@")[0].title(), role)
    assert ok, msg
    return temp


def _sign_in_new(email, role):
    """A second browser for a new person, signed in with their own password already chosen."""
    from django.test import Client
    from engines.auth_service import change_password, get_user
    temp = _person(email, role)
    uid = fetchone_uid(email)
    ok, msg = change_password(uid, temp, PW)
    assert ok, msg
    c = Client()
    r = login(c, email)
    assert r.status_code == 302, r.content
    assert get_user(uid)["role"] == role
    return c


def fetchone_uid(email):
    from engines.common import get_conn
    with get_conn() as conn:
        return conn.execute("SELECT user_id FROM app_users WHERE email = ?", (email,)).fetchone()[0]


def test_first_run_asks_for_the_admin_account_then_is_gone(web):
    r = web.get("/")
    assert r.status_code == 302 and r["Location"] == "/setup/"
    assert b"Create the System admin account" in web.get("/setup/").content
    _admin(web)
    assert web.get("/").status_code == 200
    assert web.get("/setup/")["Location"] == "/login/"                      # only while nobody has an account


def test_setup_refuses_two_different_passwords(web):
    r = web.post("/setup/", {"name": "A", "email": "a@slicks.test", "password": PW, "password2": PW + "x"})
    assert r.status_code == 200 and b"not the same" in r.content


def test_sign_in_survives_a_refresh_and_signs_out(web):
    _admin(web)
    for _ in range(3):                                                       # a refresh is just the same page again
        assert web.get("/").status_code == 200
    web.post("/logout/")
    r = web.get("/")
    assert r.status_code == 302 and r["Location"].startswith("/login/")


def test_wrong_password_and_the_way_back_to_the_page_asked_for(web):
    _admin(web)
    web.post("/logout/")
    r = web.get("/lenders/")
    assert r["Location"] == "/login/?next=/lenders/"
    r = web.post("/login/", {"email": "admin@slicks.test", "password": "nope12345", "next": "/lenders/"})
    assert r.status_code == 200 and b"Wrong email or password." in r.content
    r = login(web, "admin@slicks.test", nxt="/lenders/")
    assert r["Location"] == "/lenders/"
    web.post("/logout/")
    r = login(web, "admin@slicks.test", nxt="https://evil.example/")
    assert r["Location"] == "/"                                              # never sent to another site


def test_temporary_password_must_be_changed_first(web):
    from django.test import Client
    _admin(web)
    temp = _person("mother@slicks.test", "releaser")
    c = Client()
    login(c, "mother@slicks.test", temp)
    assert c.get("/")["Location"] == "/password/"
    r = c.post("/password/", {"old": temp, "new": PW, "new2": PW})
    assert r["Location"] == "/"
    assert c.get("/").status_code == 200


def test_switching_someone_off_or_changing_the_password_signs_them_out_everywhere(web):
    from django.test import Client
    from engines.auth_service import set_active
    _admin(web)
    laptop = _sign_in_new("staff@slicks.test", "payables")
    phone = Client()
    login(phone, "staff@slicks.test")
    assert phone.get("/").status_code == 200
    r = laptop.post("/password/", {"old": PW, "new": PW + "9", "new2": PW + "9"})
    assert r["Location"] == "/"
    assert laptop.get("/").status_code == 200                                # the device that changed it stays in
    assert phone.get("/")["Location"].startswith("/login/")                  # every other one is out
    ok, msg = set_active(fetchone_uid("staff@slicks.test"), False)
    assert ok, msg
    assert laptop.get("/")["Location"].startswith("/login/")


def test_the_top_bar_follows_the_role(web):
    _admin(web)
    admin_page = web.get("/").content.decode()
    for word in ("Buy", "Pay suppliers", "Bank", "Other payables", "Records", "Set-up", "Checks and review"):
        assert word in admin_page
    mother = _sign_in_new("mother@slicks.test", "releaser").get("/").content.decode()
    assert "Release checks" not in mother                         # the Release page is gone (2026-10-09): release is on each batch's Checks tab
    assert "Lenders" not in mother and "Purchase orders" not in mother and "Checks and review" not in mother and "Settings" not in mother


def test_a_page_not_for_the_role_is_refused(web):
    _admin(web)
    mother = _sign_in_new("mother@slicks.test", "releaser")
    assert mother.get("/lenders/").status_code == 403
    assert web.get("/no-such-page/").status_code == 404
    assert web.get("/release/").status_code == 404 and web.get("/payment-run/").status_code == 404     # removed (2026-10-09)


def test_the_planner_and_the_old_screens_are_gone(web):
    # owner, 2026-10-09: "the only thing left in /more is the check planner? if yes drop it"
    _admin(web)
    from engines.settings_store import set_setting
    set_setting("show_cash_forecast", "1")
    assert web.get("/planner/").status_code == 404
    home = web.get("/").content.decode()
    assert "Check-date planner" not in home and 'href="/more/' not in home


def test_home_shows_the_tiles_by_group_with_their_figures(web):
    _admin(web)
    page = web.get("/").content.decode()
    assert "Welcome, Admin" in page and 'class="home-top"' not in page                 # one header: the top bar (logo = Home, the person's name on the account button)
    assert 'alt="Slick\'s"' in page and "<span class=\"ms\">person</span> Admin" in page
    for word in ("Purchase orders", "Receive", "Daily payables", "Bank loans", "Credit cards", "Lenders", "Checks and review"):
        assert word in page, word
    assert 'href="/purchase-orders/?mode=receive"' in page
    assert 'class="tb-label" href="/#g-buy"' in page and 'id="g-buy"' in page      # the top bar's section name goes to its tiles on Home
    assert "home-groups" not in page                                                # no second, blue group bar
    mother = _sign_in_new("mother@slicks.test", "releaser").get("/").content.decode()
    assert "Release checks" not in mother and "Lenders" not in mother


def test_sandbox_tag(web):
    _admin(web)
    assert b"SANDBOX" in web.get("/").content                               # the test database's name has TEST in it


def test_every_save_is_recorded_as_the_person_signed_in(web):
    _admin(web)
    _sign_in_new("staff@slicks.test", "payables")
    row = fetchone(__import__("engines.db_engine", fromlist=["DB_PATH"]).DB_PATH,
                   "SELECT user_email FROM audit_logs WHERE user_action = 'USER_SIGNED_IN' ORDER BY log_id DESC LIMIT 1")
    assert row[0] == "staff@slicks.test"
    r = web.get("/whoami/")
    assert r.json() == {"email": "admin@slicks.test", "role": "admin"}


def test_health(web):
    assert web.get("/health/").content == b"ok"                             # no sign-in needed: the server's watchdog


def test_forms_need_the_page_token(web_setup, db):
    from django.test import Client
    strict = Client(enforce_csrf_checks=True)
    r = strict.post("/setup/", {"name": "A", "email": "a@slicks.test", "password": PW, "password2": PW})
    assert r.status_code == 403


