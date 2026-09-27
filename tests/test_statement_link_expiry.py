"""Thermal Statement Printing, step 2b (owner, 2026-09-27): the public QR link stops working 3 months after the batch's last check date
(no checks yet: last invoice due date + 3 months); a reprint after expiry re-opens it for 3 months; the slip prints the date."""
import sqlite3
from datetime import date

import app
from conftest import make_creditor, make_statement
from engines.drive_service import DriveError
from engines.statement_version_service import (
    current_version, daily_link_sweep, ensure_published, expire_public_links, expired_public_versions, link_expiry, revoke_older_versions,
)
from engines.thermal_statement import build_slip, render_text
from test_statement_versions_drive import FakeDrive


def _batch(db, name="ACME Tires", check_dates=(date(2026, 11, 30),)):
    creditor_id = make_creditor(db, name=name)
    with sqlite3.connect(db) as conn:                                              # make_statement always uses 'TEST-REF'
        conn.execute("UPDATE order_statements SET statement_ref = 'TEST-' || statement_id WHERE statement_ref = 'TEST-REF'")
    sid = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, f"INV-{name}", date(2026, 9, 1), date(2026, 10, 1), 100.0 * max(1, len(check_dates)), [], sid)
    for d in check_dates:
        app.issue_check(creditor_id, 100.0, d, statement_id=sid)
    return sid


def test_expiry_is_3_months_after_the_last_active_check_or_the_due_date(db):
    sid = _batch(db, check_dates=(date(2026, 10, 15), date(2026, 11, 30)))
    assert link_expiry(sid) == date(2027, 2, 28)                                  # Nov 30 + 3 months, month-end
    with sqlite3.connect(db) as conn:                                              # a cancelled check does not count
        conn.execute("UPDATE checks_issued SET status = 'Cancelled' WHERE statement_id = ? AND check_date = '2026-11-30'", (sid,))
    assert link_expiry(sid) == date(2027, 1, 15)
    no_checks = _batch(db, name="No Checks Co", check_dates=())
    assert link_expiry(no_checks) == date(2027, 1, 1)                             # no checks: due date Oct 1 + 3 months
    assert link_expiry(sid, {"reopened_until": "2027-05-01"}) == date(2027, 5, 1)  # a later re-open wins


def test_the_sweep_switches_off_only_expired_links_and_keeps_the_files(db):
    old = _batch(db, name="Old Co", check_dates=(date(2026, 6, 1),))               # expires Sep 1, 2026
    new = _batch(db, name="New Co", check_dates=(date(2026, 12, 1),))              # expires Mar 1, 2027
    drive = FakeDrive()
    today = date(2026, 8, 1)
    v_old, v_new = ensure_published(old, drive, True, today), ensure_published(new, drive, True, today)
    assert v_old["public_until"] == "2026-09-01" and v_new["public_until"] == "2027-03-01"
    result = expire_public_links(drive, date(2026, 9, 2))
    assert [(s, n) for s, n, _ in result["switched_off"]] == [(old, 1)] and result["failed"] == []
    assert drive.public == {v_new["drive_file_id"]} and v_old["drive_file_id"] in drive.files     # link off, file kept
    assert current_version(old)["drive_public"] == 0 and current_version(new)["drive_public"] == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'STATEMENT_PDF_EXPIRED'").fetchone()[0] == 1


def test_a_drive_failure_keeps_the_link_marked_public_and_is_reported(db):
    sid = _batch(db, check_dates=(date(2026, 6, 1),))
    ensure_published(sid, FakeDrive(), True, date(2026, 8, 1))
    result = expire_public_links(FakeDrive(fail_revoke=True), date(2026, 9, 2))
    assert result["switched_off"] == [] and len(result["failed"]) == 1
    assert current_version(sid)["drive_public"] == 1 and len(expired_public_versions(date(2026, 9, 2))) == 1


def test_a_reprint_after_expiry_reopens_the_same_file_for_3_months(db):
    sid = _batch(db, check_dates=(date(2026, 6, 1),))
    drive = FakeDrive()
    first = ensure_published(sid, drive, True, date(2026, 8, 1))
    expire_public_links(drive, date(2026, 9, 2))
    again = ensure_published(sid, drive, True, date(2026, 10, 10))                 # reprint on Oct 10
    assert drive.uploads == 1 and again["drive_file_id"] == first["drive_file_id"] and again["drive_link"] == first["drive_link"]
    assert again["drive_public"] == 1 and again["reopened_until"] == "2027-01-10" and again["public_until"] == "2027-01-10"
    assert link_expiry(sid, again) == date(2027, 1, 10)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'STATEMENT_PDF_REOPENED'").fetchone()[0] == 1


def test_a_revised_older_version_is_never_reopened(db):
    sid = _batch(db, check_dates=(date(2026, 12, 1),))
    drive = FakeDrive()
    v1 = ensure_published(sid, drive, True, date(2026, 8, 1))
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO statement_versions (statement_id, version_no) VALUES (?, 2)", (sid,))
    v2 = ensure_published(sid, drive, True, date(2026, 8, 1))
    revoke_older_versions(sid, v2["version_id"], drive)
    assert ensure_published(sid, drive, True, date(2026, 8, 2))["version_no"] == 2   # reprints print the current version
    assert v1["drive_file_id"] not in drive.public


def test_first_print_of_an_already_expired_batch_still_gets_a_working_qr(db):
    sid = _batch(db, check_dates=(date(2026, 1, 5),))                              # would have expired Apr 5, 2026
    v = ensure_published(sid, FakeDrive(), True, date(2026, 9, 27))
    assert v["reopened_until"] == "2026-12-27" and v["public_until"] == "2026-12-27"
    assert expire_public_links(FakeDrive(), date(2026, 12, 27))["switched_off"] == []


def test_the_daily_sweep_runs_once_a_day_and_only_calls_drive_when_needed(db):
    calls = []

    def make_drive():
        calls.append(1)
        return FakeDrive()

    assert daily_link_sweep(make_drive, date(2026, 9, 27)) == {"switched_off": [], "failed": [], "checked": 0} and calls == []
    assert daily_link_sweep(make_drive, date(2026, 9, 27)) is None                  # already ran today
    sid = _batch(db, check_dates=(date(2026, 6, 1),))
    ensure_published(sid, FakeDrive(), True, date(2026, 8, 1))

    def broken():
        raise DriveError("sign-in lapsed")

    assert daily_link_sweep(broken, date(2026, 9, 28))["due"] == 1                  # reported; the date is still recorded
    assert daily_link_sweep(broken, date(2026, 9, 28)) is None


def test_the_slip_prints_the_online_copy_date_under_the_qr(db):
    sid = _batch(db, check_dates=(date(2026, 11, 30),))
    lines = [ln.strip() for ln in render_text(build_slip(sid, "SUPPLIER", qr_link="https://x/y", link_until=date(2027, 2, 28))).splitlines()]
    assert lines.index("[ QR ]") + 1 == lines.index("Online copy until Feb 28, 2027")
    assert "Online copy until" not in render_text(build_slip(sid, "SUPPLIER"))
