"""Sign-in (B8 phase 1): the Users list, scrambled passwords, a temporary password that must be changed, a lockout after wrong tries, and the owner-only guards.
Phase 2 (at hosting) adds Google 2-step on top of the same list: only HOW a person proves who they are changes."""
import sqlite3
from datetime import datetime, timedelta

import pytest

from conftest import fetchone
from engines import auth_service as au
from engines.acting_user import acting_as
from engines.clock import set_fake_now

OWNER = "owner@example.com"
T0 = datetime(2026, 10, 2, 9, 0)


@pytest.fixture(autouse=True)
def _clock():
    set_fake_now(T0)
    yield
    set_fake_now(None)


def _owner(db, password="Owner123x"):
    ok, msg, uid = au.create_owner(OWNER, "The Owner", password)
    assert ok, msg
    return uid


def _audit(db, action):
    return fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = ?", (action,))[0]


def test_the_first_run_has_no_users_and_the_first_owner_sets_their_own_password(db):
    assert au.needs_setup() is True
    assert not au.create_owner(OWNER, "x", "short")[0]                      # the password rules apply to the owner too
    uid = _owner(db)
    assert au.needs_setup() is False and au.get_user(uid)["role"] == "admin" and au.get_user(uid)["must_change_password"] == 0
    assert not au.create_owner("other@example.com", "Other", "Another123")[0]   # only while there is nobody


def test_passwords_are_stored_scrambled_and_never_in_the_clear(db):
    _owner(db, "Owner123x")
    stored = fetchone(db, "SELECT password_hash FROM app_users")[0]
    assert stored.startswith("scrypt$") and "Owner123x" not in stored
    assert au.verify_password("Owner123x", stored) and not au.verify_password("owner123x", stored) and not au.verify_password("", stored)
    assert au.hash_password("Owner123x") != au.hash_password("Owner123x")    # a fresh salt every time


def test_signing_in_with_the_right_and_wrong_password(db):
    _owner(db)
    ok, user, msg = au.authenticate(" Owner@Example.com ", "Owner123x")
    assert ok and user["email"] == OWNER and user["role"] == "admin"          # the email is matched ignoring case and spaces
    ok, user, msg = au.authenticate(OWNER, "wrong")
    assert not ok and user is None and msg == "Wrong username or password."
    assert au.authenticate("nobody@example.com", "whatever")[2] == "Wrong username or password."       # the same words: it never says which part was wrong
    assert _audit(db, "USER_SIGNED_IN") == 1


def test_five_wrong_tries_lock_the_account_for_fifteen_minutes(db):
    _owner(db)
    for _ in range(4):
        assert au.authenticate(OWNER, "nope")[2] == "Wrong username or password."
    ok, _, msg = au.authenticate(OWNER, "nope")
    assert not ok and "15 minutes" in msg and _audit(db, "USER_LOCKED") == 1
    ok, _, msg = au.authenticate(OWNER, "Owner123x")                          # even the right password is refused while locked
    assert not ok and "locked" in msg.lower()
    set_fake_now(T0 + timedelta(minutes=16))
    assert au.authenticate(OWNER, "Owner123x")[0]                              # and works again afterwards
    assert fetchone(db, "SELECT failed_attempts FROM app_users")[0] == 0


def test_a_new_user_gets_a_temporary_password_that_must_be_changed(db):
    _owner(db)
    ok, msg, uid, temp = au.create_user("Brother@Example.com", "The Brother", "purchasing")
    assert ok and uid and len(temp) >= 10 and au.get_user(uid)["must_change_password"] == 1 and au.get_user(uid)["email"] == "brother@example.com"
    assert not au.create_user("brother@example.com", "Again", "purchasing")[0]          # an email is one person
    assert not au.create_user("x@example.com", "X", "janitor")[0]                        # only known roles
    ok, user, _ = au.authenticate("brother@example.com", temp)
    assert ok and user["must_change_password"] == 1
    assert not au.change_password(uid, temp, "short")[0]
    assert not au.change_password(uid, temp, "abcdefghij")[0]                            # needs a digit
    assert not au.change_password(uid, temp, "1234567890")[0]                            # needs a letter
    assert not au.change_password(uid, temp, temp)[0]                                    # not the temporary one again
    assert not au.change_password(uid, "wrong-old", "Brother123")[0]
    assert au.change_password(uid, temp, "Brother123")[0]
    assert au.get_user(uid)["must_change_password"] == 0 and au.authenticate("brother@example.com", "Brother123")[0]
    assert not au.authenticate("brother@example.com", temp)[0]
    assert _audit(db, "USER_CREATED") == 2 and _audit(db, "PASSWORD_CHANGED") == 1       # the owner and the brother


def test_the_owner_can_reset_a_password_and_switch_a_person_off_and_on(db):
    _owner(db)
    _, _, uid, temp = au.create_user("brother@example.com", "The Brother", "purchasing")
    au.change_password(uid, temp, "Brother123")
    ok, msg, temp2 = au.reset_password(uid)
    assert ok and au.get_user(uid)["must_change_password"] == 1 and not au.authenticate("brother@example.com", "Brother123")[0]
    assert au.authenticate("brother@example.com", temp2)[0]
    assert au.set_active(uid, False)[0]
    ok, _, msg = au.authenticate("brother@example.com", temp2)
    assert not ok and "switched off" in msg.lower()
    assert au.set_active(uid, True)[0] and au.authenticate("brother@example.com", temp2)[0]


def test_the_last_system_admin_cannot_be_switched_off_or_demoted(db):
    owner = _owner(db)
    assert not au.set_active(owner, False)[0] and not au.set_role(owner, "purchasing")[0]
    _, _, second, temp = au.create_user("second@example.com", "Second", "admin")
    assert au.set_role(owner, "purchasing")[0]                                           # fine once there is another System admin
    assert not au.set_active(second, False)[0]                                           # ... which is now the last one


def test_audit_rows_carry_the_person_who_acted(db):
    _owner(db)
    with acting_as(OWNER):
        au.create_user("brother@example.com", "The Brother", "purchasing")
    assert fetchone(db, "SELECT user_email FROM audit_logs WHERE user_action = 'USER_CREATED'")[0] == OWNER


def test_users_are_listed_without_their_password_hashes(db):
    _owner(db)
    au.create_user("brother@example.com", "The Brother", "purchasing")
    df = au.list_users()
    assert list(df["email"]) == [OWNER, "brother@example.com"] and "password_hash" not in df.columns


def test_the_sign_in_can_be_skipped_only_on_a_test_database(monkeypatch):
    from views import login
    monkeypatch.setenv("AP_SKIP_LOGIN", "1")
    monkeypatch.setenv("AP_DB_PATH", "data/ap_disbursement_TEST.db")
    assert login._sign_in_off() is True
    monkeypatch.setenv("AP_DB_PATH", "data/ap_disbursement.db")
    assert login._sign_in_off() is False                              # never on the real database
    monkeypatch.setenv("AP_DB_PATH", "data/ap_disbursement_TEST.db")
    monkeypatch.delenv("AP_SKIP_LOGIN")
    assert login._sign_in_off() is False
