"""System health (owner, 2026-10-20): the System admin's read-only look at the whole system, the Home line, the Monday email."""
from datetime import datetime

from conftest import fetchone
from tests.test_web_d0 import _admin, _sign_in_new


def test_the_page_for_the_system_admin_only(web, db):
    _admin(web)
    page = web.get("/system-health/").content.decode()
    for title in ("Server", "Backups", "Data safety", "Messages", "People today", "Speed", "Waiting on people"):
        assert title in page
    assert "Could not check" not in page
    owner = _sign_in_new("owner1@slicks.test", "owner")
    assert owner.get("/system-health/").status_code == 403                          # the System admin's alone


def test_pages_are_timed_and_the_slowest_shows(web, db):
    from engines import system_health as sh
    _admin(web)
    web.get("/review/")
    sh.record_page_time("/purchase-orders/12/edit/", 4.2)
    lines = (sh._times_dir() / f"{sh._now():%Y%m%d}.log").read_text(encoding="utf-8")
    assert "/review/" in lines and "/purchase-orders/#/edit/" in lines                # numbers left out: the page, not the record
    card = next(c for c in sh.cards() if c["key"] == "speed")
    assert card["state"] == "yellow" and "/purchase-orders/#/edit/" in card["rows"][0][1]


def test_home_shows_one_line_for_the_system_admin(web, db):
    from engines import system_health as sh
    _admin(web)
    sh._SUMMARY.clear()
    assert "System health:" in web.get("/").content.decode()
    owner = _sign_in_new("owner2@slicks.test", "owner")
    assert "System health:" not in owner.get("/").content.decode()


def test_monday_email_only_when_something_is_not_green(web, db, monkeypatch):
    from engines import mail_out, system_health as sh
    from engines.clock import set_fake_now
    sent = []
    monkeypatch.setattr(mail_out, "ready", lambda: True)
    monkeypatch.setattr(mail_out, "send", lambda to, subject, html, text, reply_to="": sent.append((to, subject)) or (True, ""))
    _admin(web)
    try:
        set_fake_now(datetime(2026, 10, 27, 9, 0))                                  # a Tuesday: not due
        assert "not due" in sh.weekly_email()
        set_fake_now(datetime(2026, 10, 26, 9, 0))                                  # Monday 9 AM: the backups are not reported yet -> yellow -> sent
        msg = sh.weekly_email(site="https://erp.example")
        assert "sent to 1" in msg and sent and "System health" in sent[0][1]
        assert "not due" in sh.weekly_email()                                         # once a Monday
    finally:
        set_fake_now(None)
