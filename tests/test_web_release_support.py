"""Releasing while the family works in the system (owner, 2026-10-09): who is active before a deploy, an advance notice on every page, and a
"What's new" note shown once to each person."""
import pytest
from django.core.management import call_command

from tests.conftest import fetchone
from tests.test_web_d0 import _admin, _sign_in_new, fetchone_uid


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def test_last_seen_is_written_at_most_once_a_minute(web):
    from core.middleware import _SEEN, note_seen
    _admin(web)
    uid = fetchone_uid("admin@slicks.test")
    _SEEN.clear()
    assert note_seen(uid, "/checks/", now=1000.0) is True
    assert note_seen(uid, "/bank-cash/", now=1030.0) is False                      # within the minute: no write
    assert fetchone(_db(), "SELECT last_seen_path FROM app_users WHERE user_id = ?", (uid,))[0] == "/checks/"
    assert note_seen(uid, "/bank-cash/", now=1061.0) is True


def test_who_is_active_lists_a_recent_person_only(web, capsys):
    import sqlite3
    _admin(web)
    _sign_in_new("family@slicks.test", "owner")
    with sqlite3.connect(_db()) as conn:
        conn.execute("UPDATE app_users SET last_seen_at = datetime('now', '-2 minutes'), last_seen_path = '/checks/' WHERE email = 'family@slicks.test'")
        conn.execute("UPDATE app_users SET last_seen_at = datetime('now', '-3 hours') WHERE email = 'admin@slicks.test'")
    with pytest.raises(SystemExit) as stop:
        call_command("who_is_active", minutes=10)
    out = capsys.readouterr().out
    assert stop.value.code == 3 and "family@slicks.test" in out and "/checks/" in out and "admin@slicks.test" not in out
    with sqlite3.connect(_db()) as conn:
        conn.execute("UPDATE app_users SET last_seen_at = datetime('now', '-3 hours')")
    call_command("who_is_active", minutes=10)
    assert "Nobody active" in capsys.readouterr().out


def test_a_notice_with_a_time_shows_on_every_page_until_then(web):
    from engines.clock import manila_now
    from datetime import timedelta
    _admin(web)
    family = _sign_in_new("family@slicks.test", "owner")
    soon = (manila_now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    web.post("/settings/", {"action": "notice", "notice": "System update tonight at 12 AM", "until": soon})
    for url in ("/", "/checks/", "/reports/"):
        assert "System update tonight at 12 AM" in family.get(url).content.decode(), url
    past = (manila_now() - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")
    web.post("/settings/", {"action": "notice", "notice": "System update tonight at 12 AM", "until": past})
    assert "System update tonight" not in family.get("/checks/").content.decode()        # gone by itself
    family.post("/settings/", {"action": "notice", "notice": "hijack", "until": soon})
    assert "hijack" not in family.get("/checks/").content.decode()                        # only the System admin sets it


def test_whats_new_shows_once_and_a_new_person_starts_seen(web):
    _admin(web)
    home = web.get("/").content.decode()
    assert "What's new" in home                                                           # an account from before the notes
    web.post("/whats-new/seen/")
    assert "What's new" not in web.get("/").content.decode()
    family = _sign_in_new("family@slicks.test", "owner")
    assert "What's new" not in family.get("/").content.decode()                          # a brand-new person: nothing "new"
