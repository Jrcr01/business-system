"""Thermal Statement Printing, build step 2: the A4 copy behind the QR -- statement versions, Drive upload, per-file public sharing and
revocation. Uses a fake Drive (same five methods as engines.drive_service.GoogleDrive), so no internet or Google account is needed."""
import sqlite3
from datetime import date

import pytest

import app
import db_location
from conftest import make_creditor, make_statement
from engines.drive_service import DriveError, statement_folder_id
from engines.statement_version_service import content_hash, current_version, ensure_published, revoke_older_versions


class FakeDrive:
    def __init__(self, fail_upload=False, fail_share=False):
        self.files, self.folders, self.public = {}, {}, set()
        self.uploads = 0
        self.fail_upload, self.fail_share = fail_upload, fail_share

    def create_folder(self, name):
        self.folder_count = getattr(self, "folder_count", 0) + 1
        fid = f"folder{self.folder_count}"
        self.folders[fid] = name
        return fid

    def folder_exists(self, folder_id):
        return folder_id in self.folders

    def upload_pdf(self, data, name, folder_id):
        if self.fail_upload:
            raise DriveError("no internet")
        self.uploads += 1
        fid = f"file{self.uploads}"
        self.files[fid] = {"name": name, "data": data, "folder": folder_id}
        return fid, f"https://drive.google.com/file/d/{fid}/view"

    def share_public(self, file_id):
        if self.fail_share:
            raise DriveError("sharing refused")
        self.public.add(file_id)

    def revoke_public(self, file_id):
        self.public.discard(file_id)


def _batch(db):
    creditor_id = make_creditor(db, name="ACME Tires")
    sid = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 9, 1), date(2026, 10, 1), 1000.0, [], sid)
    return creditor_id, sid


def test_first_print_uploads_the_a4_copy_once_and_shares_only_that_file(db):
    _, sid = _batch(db)
    drive = FakeDrive()
    v = ensure_published(sid, drive, sandbox=True)
    assert v["version_no"] == 1 and v["drive_public"] == 1 and v["drive_link"].endswith("/file1/view")
    f = drive.files["file1"]
    assert f["data"].startswith(b"%PDF") and "TEST-REF - statement v1 - " in f["name"]
    assert drive.public == {"file1"} and f["folder"] not in drive.public               # the file is shared, never the folder
    assert list(drive.folders.values()) == ["Slick's AP - statement copies (QR) (TEST)"]
    again = ensure_published(sid, drive, sandbox=True)                                   # a reprint reuses the same PDF and link
    assert drive.uploads == 1 and again["drive_link"] == v["drive_link"]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'STATEMENT_PDF_PUBLISHED'").fetchone()[0] == 1


def test_one_folder_per_database_recreated_if_deleted(db):
    drive = FakeDrive()
    first = statement_folder_id(drive, sandbox=False)
    assert statement_folder_id(drive, sandbox=False) == first and len(drive.folders) == 1
    del drive.folders[first]                                                              # someone deleted it in Drive
    assert statement_folder_id(drive, sandbox=False) != first and len(drive.folders) == 1


@pytest.mark.parametrize("failure", ["fail_upload", "fail_share"])
def test_a_failed_upload_or_share_is_not_recorded_so_the_print_must_abort(db, failure):
    _, sid = _batch(db)
    with pytest.raises(DriveError):
        ensure_published(sid, FakeDrive(**{failure: True}), sandbox=True)
    v = current_version(sid)
    assert v["drive_file_id"] is None and v["drive_public"] == 0 and v["drive_link"] is None
    ok = ensure_published(sid, FakeDrive(), sandbox=True)                                 # the retry uploads a fresh file
    assert ok["drive_public"] == 1


def test_revoking_older_versions_switches_off_their_link_but_keeps_the_file(db):
    _, sid = _batch(db)
    drive = FakeDrive()
    v1 = ensure_published(sid, drive, sandbox=True)
    with sqlite3.connect(db) as conn:                                                     # a version 2 (step 3 decides when)
        conn.execute("INSERT INTO statement_versions (statement_id, version_no) VALUES (?, 2)", (sid,))
    v2 = ensure_published(sid, drive, sandbox=True)
    assert v2["version_no"] == 2 and drive.public == {"file1", "file2"}
    assert revoke_older_versions(sid, v2["version_id"], drive) == [1]
    assert drive.public == {"file2"} and "file1" in drive.files                            # old link off, file kept
    with sqlite3.connect(db) as conn:
        old = conn.execute("SELECT drive_public, revoked_at FROM statement_versions WHERE version_id = ?", (v1["version_id"],)).fetchone()
    assert old[0] == 0 and old[1] is not None
    assert revoke_older_versions(sid, v2["version_id"], drive) == []                      # nothing left to revoke


def test_the_content_fingerprint_changes_only_when_the_statement_changes(db):
    creditor_id, sid = _batch(db)
    h = content_hash(sid)
    assert content_hash(sid) == h
    app.add_invoice(creditor_id, "INV-2", date(2026, 9, 2), date(2026, 10, 2), 50.0, [], sid)
    assert content_hash(sid) != h


def test_drive_login_files_live_outside_the_project(monkeypatch, tmp_path):
    monkeypatch.setenv("AP_DRIVE_DIR", str(tmp_path / "drive"))
    assert db_location.drive_dir() == str(tmp_path / "drive")
    monkeypatch.delenv("AP_DRIVE_DIR")
    assert not db_location.drive_dir().startswith(db_location.ROOT)
