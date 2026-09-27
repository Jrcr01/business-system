"""Thermal Statement Printing, step 3: statement versions, "Needs reprint", and ORIGINAL / REPRINT / REVISED per copy."""
import sqlite3
from datetime import date

import app
from conftest import make_creditor, make_statement
from engines.check_rules import assign_check_number
from engines.statement_rules import lock_statement, unlock_statement
from engines.statement_version_service import (
    current_version, ensure_published, needs_reprint, planned_print, print_type_for, record_print, revoke_older_versions,
)
from engines.thermal_statement import build_slip, render_text
from test_statement_versions_drive import FakeDrive


def _batch(db):
    creditor_id = make_creditor(db, name="ACME Tires")
    sid = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 9, 1), date(2026, 10, 1), 1000.0, [], sid)
    return creditor_id, sid


def _print(sid, drive, copies=("FILE", "SUPPLIER"), status="SUCCESS"):
    """What the printer section does: publish (starting a new version if the batch changed), work out each copy's type, record."""
    v = ensure_published(sid, drive, True)
    types = {c: print_type_for(sid, c, v)["print_type"] for c in copies}
    for c in copies:
        record_print(sid, v, c, types[c], "dummy", status)
    return v, types


def _versions(db, sid):
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT COUNT(*) FROM statement_versions WHERE statement_id = ?", (sid,)).fetchone()[0]


def test_first_print_is_original_then_the_same_copy_is_a_reprint(db):
    _, sid = _batch(db)
    drive = FakeDrive()
    v, types = _print(sid, drive, copies=("FILE",))
    assert types == {"FILE": "ORIGINAL"}
    assert print_type_for(sid, "FILE")["print_type"] == "REPRINT"
    assert print_type_for(sid, "SUPPLIER")["print_type"] == "ORIGINAL"          # the other copy was not printed yet
    v2, types = _print(sid, drive)
    assert types == {"FILE": "REPRINT", "SUPPLIER": "ORIGINAL"} and v2["drive_link"] == v["drive_link"] and drive.uploads == 1
    assert "REPRINT - " in render_text(build_slip(sid, "FILE", print_type="REPRINT", reprint_date="2026-09-27"))


def test_a_failed_attempt_does_not_count_so_the_retry_is_still_the_original(db):
    _, sid = _batch(db)
    drive = FakeDrive()
    _print(sid, drive, status="FAILED")
    assert print_type_for(sid, "FILE")["print_type"] == "ORIGINAL" and not needs_reprint(sid)


def test_edited_after_unlock_and_relocked_needs_reprint_until_the_revised_one_is_printed(db):
    creditor_id, sid = _batch(db)
    drive = FakeDrive()
    v1, _ = _print(sid, drive)
    lock_statement(sid)
    unlock_statement(sid)
    app.add_invoice(creditor_id, "INV-2", date(2026, 9, 2), date(2026, 10, 2), 500.0, [], sid)
    ok, msg = lock_statement(sid)
    assert ok and "Needs reprint" in msg and needs_reprint(sid)
    assert current_version(sid)["version_no"] == 2 and current_version(sid)["drive_file_id"] is None    # made when printed
    assert planned_print(sid, "FILE") == {"print_type": "REVISED", "version_no": 2, "supersedes": 1}
    v2, types = _print(sid, drive)
    assert types == {"FILE": "REVISED", "SUPPLIER": "REVISED"} and v2["version_no"] == 2 and v2["drive_file_id"] != v1["drive_file_id"]
    assert not needs_reprint(sid)
    assert revoke_older_versions(sid, v2["version_id"], drive) == [1]                                  # the old QR stops working
    assert drive.public == {v2["drive_file_id"]} and v1["drive_file_id"] in drive.files
    assert print_type_for(sid, "FILE")["print_type"] == "REPRINT"
    assert "REVISED v2 – supersedes v1" in render_text(build_slip(sid, "SUPPLIER", print_type="REVISED", version=2))


def test_relocking_without_changes_starts_nothing(db):
    _, sid = _batch(db)
    _print(sid, FakeDrive())
    unlock_statement(sid)
    ok, msg = lock_statement(sid)
    assert ok and msg == "Batch locked." and not needs_reprint(sid) and _versions(db, sid) == 1


def test_a_never_printed_batch_is_not_flagged(db):
    creditor_id, sid = _batch(db)
    unlock_statement(sid)
    app.add_invoice(creditor_id, "INV-2", date(2026, 9, 2), date(2026, 10, 2), 500.0, [], sid)
    assert lock_statement(sid) == (True, "Batch locked.") and not needs_reprint(sid) and _versions(db, sid) == 0


def test_assigning_check_numbers_after_printing_is_not_a_revision(db):
    creditor_id, sid = _batch(db)
    _, _, check_id = app.issue_check(creditor_id, 1000.0, date(2026, 10, 1), statement_id=sid)
    _print(sid, FakeDrive())
    ok, msg = assign_check_number(check_id, None, None, "3992000001")
    assert ok, msg
    lock_statement(sid)
    assert not needs_reprint(sid) and _versions(db, sid) == 1 and print_type_for(sid, "FILE")["print_type"] == "REPRINT"


def test_a_change_without_relock_still_prints_a_new_version_and_the_preview_writes_nothing(db):
    creditor_id, sid = _batch(db)
    drive = FakeDrive()
    _print(sid, drive)
    app.add_invoice(creditor_id, "INV-3", date(2026, 9, 3), date(2026, 10, 3), 25.0, [], sid)          # changed, not re-locked
    assert planned_print(sid, "FILE")["print_type"] == "REVISED" and _versions(db, sid) == 1          # the preview only predicts
    v2, types = _print(sid, drive)                                                                     # printing is the safety net
    assert v2["version_no"] == 2 and types["FILE"] == "REVISED" and drive.uploads == 2
