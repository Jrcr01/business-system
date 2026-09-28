"""Slicks ERP core step C1 (2026-09-28): users, roles and sign-in. Owner's choices: password sign-in for now (Google later), roles
Owner / Sales / Viewer, signed out after 2 hours idle, 5 wrong tries = locked 15 minutes, audit log for the owner only.
  * every page needs a sign-in, and every page (except home / password / sign-out) says which roles may open it;
  * Sales and Viewer are refused the Users and Audit log pages, and don't see them in the menu;
  * sign-in, wrong tries, the lock, sign-out and every user change land in audit_logs with WHO did it;
  * a temporary password (new user, reset) must be replaced before anything else;
  * the system can never be left without an active owner; a deactivated user is signed out at once."""
from datetime import datetime

import pytest

pytest.importorskip("django", reason="Django is installed only in the ERP environment (.venv)")

from engines.clock import set_fake_now  # noqa: E402

PASSWORD = "sandbox-Test-pass-42"


@pytest.fixture
def c1(erp):
    """A clean set of users for each test: one owner, one sales, one viewer (password PASSWORD, no temporary password)."""
    from django.contrib.sessions.models import Session

    from core.models import LoginAttempt, Role, User

    set_fake_now(None)                      # another test file may have left the clock pinned
    Session.objects.all().delete()
    LoginAttempt.objects.all().delete()
    User.objects.all().delete()
    users = {
        role: User.objects.create_user(f"{role}@example.com", f"{role.title()} person", role, PASSWORD, must_change_password=False)
        for role in (Role.OWNER, Role.SALES, Role.VIEWER)
    }
    yield users
    set_fake_now(None)


def _client(user=None):
    from django.test import Client

    client = Client()
    if user is not None:
        client.force_login(user)
    return client


def _audit(action, **where):
    from django.db import connection

    sql = "SELECT user_email, entity_id, old_values, new_values FROM audit_logs WHERE user_action = %s"
    params = [action]
    for col, val in where.items():
        sql += f" AND {col} = %s"
        params.append(val)
    with connection.cursor() as cur:
        cur.execute(sql + " ORDER BY log_id", params)
        return cur.fetchall()


def _clear_audit():
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute("DELETE FROM audit_logs")


def _sign_in(client, email, password=PASSWORD):
    return client.post("/sign-in/", {"email": email, "password": password})


# ---- every page is protected -------------------------------------------------------------------------------------------------

def test_every_page_needs_a_sign_in(c1):
    from django.urls import get_resolver

    client = _client()
    for pattern in get_resolver().url_patterns[0].url_patterns if hasattr(get_resolver().url_patterns[0], "url_patterns") else []:
        if pattern.name == "login":
            continue
        path = "/" + str(pattern.pattern).replace("<int:pk>", str(c1["owner"].pk))
        resp = client.get(path)
        assert resp.status_code in (302, 405), path
        if resp.status_code == 302:
            assert resp["Location"].startswith("/sign-in/"), path


def test_every_page_declares_its_roles():
    from core import urls, views

    open_to_all = {"home", "login", "logout", "password_change"}
    for pattern in urls.urlpatterns:
        if pattern.name in open_to_all:
            continue
        assert getattr(pattern.callback, "allowed_roles", None), f"{pattern.name} has no role_required"


@pytest.mark.parametrize("role", ["sales", "viewer"])
def test_sales_and_viewer_cannot_open_users_or_audit_log(c1, role):
    client = _client(c1[role])
    for path in ("/users/", "/users/add/", f"/users/{c1['owner'].pk}/", "/audit/"):
        assert client.get(path).status_code == 403, path
    assert client.post(f"/users/{c1['owner'].pk}/role/", {"role": "sales"}).status_code == 403
    assert client.post(f"/users/{c1['owner'].pk}/active/", {"active": "0"}).status_code == 403
    html = client.get("/").content.decode()
    assert "/users/" not in html and "/audit/" not in html              # not in the menu either


def test_owner_sees_users_and_audit_log(c1):
    client = _client(c1["owner"])
    assert client.get("/users/").status_code == 200
    assert client.get("/audit/").status_code == 200
    html = client.get("/").content.decode()
    assert "/users/" in html and "/audit/" in html


# ---- sign-in, lockout, sign-out ---------------------------------------------------------------------------------------------

def test_sign_in_and_out_are_audited_with_who(c1):
    _clear_audit()
    client = _client()
    resp = _sign_in(client, "SALES@example.com ")                         # case and spaces don't matter
    assert resp.status_code == 302 and resp["Location"] == "/"
    assert _audit("LOGIN")[0][0] == "sales@example.com"
    client.post("/sign-out/")
    assert _audit("LOGOUT")[0][0] == "sales@example.com"
    assert client.get("/").status_code == 302


def test_wrong_tries_show_tries_left_and_are_audited_as_anonymous(c1):
    _clear_audit()
    resp = _sign_in(_client(), "sales@example.com", "wrong")
    assert "4 tries left" in resp.content.decode()
    who, entity_id, _, new = _audit("LOGIN_FAILED")[0]
    assert who == "anonymous" and entity_id == c1["sales"].pk and "wrong password" in new
    resp = _sign_in(_client(), "nobody@example.com", "wrong")
    assert "Wrong email or password" in resp.content.decode()            # same message: doesn't reveal who has an account


def test_five_wrong_tries_lock_for_15_minutes_even_with_the_right_password(c1):
    _clear_audit()
    set_fake_now(datetime(2030, 1, 15, 9, 0))
    for _ in range(5):
        resp = _sign_in(_client(), "sales@example.com", "wrong")
    assert "Locked for 15 minutes" in resp.content.decode()
    assert len(_audit("LOGIN_LOCKED")) == 1
    resp = _sign_in(_client(), "sales@example.com")                       # right password, still locked
    assert resp.status_code == 200 and "Try again after 9:15 AM" in resp.content.decode()
    set_fake_now(datetime(2030, 1, 15, 9, 16))
    assert _sign_in(_client(), "sales@example.com").status_code == 302   # lock over


def test_a_right_password_resets_the_count(c1):
    for _ in range(4):
        _sign_in(_client(), "viewer@example.com", "wrong")
    assert _sign_in(_client(), "viewer@example.com").status_code == 302
    resp = _sign_in(_client(), "viewer@example.com", "wrong")
    assert "4 tries left" in resp.content.decode()


def test_signed_out_after_two_hours_idle():
    from django.conf import settings

    assert settings.SESSION_COOKIE_AGE == 2 * 60 * 60 and settings.SESSION_SAVE_EVERY_REQUEST


def test_next_link_cannot_send_you_to_another_site(c1):
    resp = _client().post("/sign-in/?next=https://evil.example.com/", {"email": "owner@example.com", "password": PASSWORD})
    assert resp["Location"] == "/"


# ---- users page ---------------------------------------------------------------------------------------------------------------

def test_owner_adds_a_user_who_must_choose_a_password(c1):
    from core.models import User

    _clear_audit()
    owner = _client(c1["owner"])
    resp = owner.post("/users/add/", {"name": "Brother", "email": "Brother@Example.com", "role": "sales"})
    assert resp.status_code == 200
    temp = resp.context["temp"]
    new = User.objects.get(email="brother@example.com")
    assert new.role == "sales" and new.must_change_password and new.check_password(temp)
    assert _audit("USER_ADDED")[0][0] == "owner@example.com"

    brother = _client()
    assert _sign_in(brother, "brother@example.com", temp).status_code == 302
    assert brother.get("/")["Location"] == "/password/"                  # must choose a password first
    resp = brother.post("/password/", {"new_password1": "1234567890", "new_password2": "1234567890"})
    assert resp.status_code == 200                                         # all numbers: refused
    resp = brother.post("/password/", {"new_password1": "Counter-Sales-2030", "new_password2": "Counter-Sales-2030"})
    assert resp.status_code == 302
    assert brother.get("/").status_code == 200
    assert _audit("PASSWORD_CHANGED")[0][0] == "brother@example.com"


def test_adding_an_email_twice_is_refused(c1):
    resp = _client(c1["owner"]).post("/users/add/", {"name": "Again", "email": "sales@example.com", "role": "viewer"})
    assert "already has a user" in resp.content.decode()


def test_role_change_is_audited(c1):
    _clear_audit()
    _client(c1["owner"]).post(f"/users/{c1['viewer'].pk}/role/", {"role": "sales"})
    c1["viewer"].refresh_from_db()
    assert c1["viewer"].role == "sales"
    assert _audit("USER_ROLE_CHANGED")[0] == ("owner@example.com", c1["viewer"].pk, "Viewer", "Sales")


def test_deactivated_user_is_signed_out_at_once_and_cannot_sign_in(c1):
    sales = _client()
    _sign_in(sales, "sales@example.com")
    assert sales.get("/").status_code == 200
    _client(c1["owner"]).post(f"/users/{c1['sales'].pk}/active/", {"active": "0"})
    assert sales.get("/").status_code == 302                              # next click -> the sign-in page
    assert _sign_in(_client(), "sales@example.com").status_code == 200    # refused
    assert _audit("USER_DEACTIVATED")


def test_the_last_active_owner_cannot_be_deactivated_or_demoted(c1):
    owner = _client(c1["owner"])
    owner.post(f"/users/{c1['owner'].pk}/active/", {"active": "0"})
    owner.post(f"/users/{c1['owner'].pk}/role/", {"role": "viewer"})
    c1["owner"].refresh_from_db()
    assert c1["owner"].is_active and c1["owner"].role == "owner"
    owner.post(f"/users/{c1['sales'].pk}/role/", {"role": "owner"})       # with a second owner it is allowed
    owner.post(f"/users/{c1['owner'].pk}/role/", {"role": "viewer"})
    c1["owner"].refresh_from_db()
    assert c1["owner"].role == "viewer"


def test_reset_password_gives_a_new_temporary_password(c1):
    resp = _client(c1["owner"]).post(f"/users/{c1['sales'].pk}/reset-password/")
    temp = resp.context["temp"]
    c1["sales"].refresh_from_db()
    assert c1["sales"].must_change_password and c1["sales"].check_password(temp) and not c1["sales"].check_password(PASSWORD)
    assert _audit("PASSWORD_RESET")[0][0] == "owner@example.com"


def test_pages_that_change_things_refuse_a_plain_get(c1):
    owner = _client(c1["owner"])
    for path in (f"/users/{c1['sales'].pk}/role/", f"/users/{c1['sales'].pk}/active/", f"/users/{c1['sales'].pk}/reset-password/", "/sign-out/"):
        assert owner.get(path).status_code == 405, path


def test_forms_are_protected_against_forged_requests(c1):
    from django.test import Client

    client = Client(enforce_csrf_checks=True)
    client.force_login(c1["owner"])
    assert client.post(f"/users/{c1['sales'].pk}/active/", {"active": "0"}).status_code == 403


# ---- audit log page -----------------------------------------------------------------------------------------------------------

def test_audit_log_page_filters_by_who_and_action(c1):
    _clear_audit()
    _sign_in(_client(), "sales@example.com")
    _sign_in(_client(), "viewer@example.com", "wrong")
    owner = _client(c1["owner"])
    rows = owner.get("/audit/", {"who": "sales@example.com"}).context["rows"]
    assert [r[3] for r in rows] == ["LOGIN"]
    rows = owner.get("/audit/", {"action": "LOGIN_FAILED"}).context["rows"]
    assert len(rows) == 1 and rows[0][2] == "anonymous"


# ---- set-up commands ----------------------------------------------------------------------------------------------------------

def test_seed_sandbox_users_refuses_a_database_that_is_not_the_sandbox(c1):
    from django.core.management import CommandError, call_command

    with pytest.raises(CommandError, match="not a sandbox database"):
        call_command("seed_sandbox_users")                                 # the test file is erp_test.db (no _TEST)


def test_create_owner_refuses_when_an_owner_exists(c1):
    from django.core.management import CommandError, call_command

    with pytest.raises(CommandError, match="already exists"):
        call_command("create_owner", email="second@example.com", name="Second")


def test_create_owner_makes_the_first_owner(c1, monkeypatch):
    from django.core.management import call_command

    from core.models import User

    User.objects.all().delete()
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "Owner-First-Pass-2030")
    call_command("create_owner", email="First@Example.com", name="First owner")
    owner = User.objects.get(email="first@example.com")
    assert owner.role == "owner" and not owner.must_change_password and owner.check_password("Owner-First-Pass-2030")
    assert _audit("USER_ADDED", entity_id=owner.pk)[0][0] == "first@example.com"
