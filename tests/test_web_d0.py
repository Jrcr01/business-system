"""Stage D0 of the Django rebuild (docs/DJANGO_REBUILD_PROPOSAL.md): first-run set-up, sign-in that survives a refresh, the forced password change, roles in the
top bar and on every page, Home's tiles, and who every save is recorded as."""
import pytest

from tests.conftest import fetchone

PW = "Sandbox123"


def _admin(web):
    r = web.post("/setup/", {"name": "Admin", "email": "admin@slicks.test", "password": PW, "password2": PW})
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
    r = c.post("/login/", {"email": email, "password": PW})
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
    r = web.post("/login/", {"email": "admin@slicks.test", "password": PW, "next": "/lenders/"})
    assert r["Location"] == "/lenders/"
    r = web.post("/login/", {"email": "admin@slicks.test", "password": PW, "next": "https://evil.example/"})
    assert r["Location"] == "/"                                              # never sent to another site


def test_temporary_password_must_be_changed_first(web):
    from django.test import Client
    _admin(web)
    temp = _person("mother@slicks.test", "releaser")
    c = Client()
    c.post("/login/", {"email": "mother@slicks.test", "password": temp})
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
    phone.post("/login/", {"email": "staff@slicks.test", "password": PW})
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
    for word in ("Buy", "Pay suppliers", "Bank", "Other payables", "Records", "Set-up", "Admin and audit"):
        assert word in admin_page
    mother = _sign_in_new("mother@slicks.test", "releaser").get("/").content.decode()
    assert "Release checks" in mother
    assert "Lenders" not in mother and "Purchase orders" not in mother and "Admin and audit" not in mother and "Settings" not in mother


def test_a_page_not_for_the_role_is_refused(web):
    _admin(web)
    mother = _sign_in_new("mother@slicks.test", "releaser")
    assert mother.get("/lenders/").status_code == 403
    assert mother.get("/release/").status_code == 200
    assert web.get("/no-such-page/").status_code == 404


def test_a_page_still_in_streamlit_says_which_stage_brings_it(web):
    _admin(web)
    r = web.get("/release/")
    assert r.status_code == 200 and b"stage D5" in r.content and b"Streamlit" in r.content


def test_home_shows_the_tiles_by_group_with_their_figures(web):
    _admin(web)
    page = web.get("/").content.decode()
    assert "Welcome, Admin" in page and 'class="home-top"' not in page                 # one header: the top bar (logo = Home, the person's name on the account button)
    assert 'alt="Slick\'s"' in page and "<span class=\"ms\">person</span> Admin" in page
    for word in ("Purchase orders", "Receive goods", "Payment run", "Release checks", "Lenders", "Data health"):
        assert word in page, word
    assert 'href="/purchase-orders/?mode=receive"' in page
    assert 'class="tb-label" href="/#g-buy"' in page and 'id="g-buy"' in page      # the top bar's section name goes to its tiles on Home
    assert "home-groups" not in page                                                # no second, blue group bar
    mother = _sign_in_new("mother@slicks.test", "releaser").get("/").content.decode()
    assert "Release checks" in mother and "Lenders" not in mother


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


def test_on_the_server_pages_not_moved_yet_open_in_the_streamlit_screens(web, settings=None):
    """Go-live side by side (2026-10-03): with WEB_STREAMLIT_URL set, the menu, the Home tiles and the 'later' page go straight to the Streamlit screens
    (same database); on the laptop (not set) they stay on the 'later' page."""
    from django.conf import settings as dj
    _admin(web)
    old = dj.STREAMLIT_URL
    try:
        dj.STREAMLIT_URL = "/more/"
        home = web.get("/").content.decode()
        assert 'href="/more/payment-run"' in home and 'href="/purchase-orders/"' in home      # built pages stay in Django
        later = web.get("/payment-run/").content.decode()
        assert 'href="/more/payment-run"' in later and "same email and password" in later
    finally:
        dj.STREAMLIT_URL = old
    assert 'href="/more/' not in web.get("/").content.decode()
