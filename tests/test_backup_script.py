"""backup_live_db.py and db_location.py (2026-09-19): scheduled daily backup + one shared idea of where the live db is."""
import glob
import os
import sqlite3
import sys

import backup_live_db
import db_location


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE checks_issued (check_id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO checks_issued DEFAULT VALUES")
    conn.commit(); conn.close()


def test_backup_creates_a_readable_copy_and_keeps_only_the_newest(tmp_path, monkeypatch):
    src = str(tmp_path / "live.db"); _make_db(src)
    dest = tmp_path / "backups"
    for stamp in ("20260101_000000", "20260102_000000", "20260103_000000"):
        os.makedirs(dest, exist_ok=True)
        _make_db(str(dest / f"ap_disbursement_backup_{stamp}.db"))
    monkeypatch.setattr(sys, "argv", ["x", "--db", src, "--dest", str(dest), "--keep", "2"])

    assert backup_live_db.main() == 0

    kept = sorted(glob.glob(str(dest / "ap_disbursement_backup_*.db")))
    assert len(kept) == 2
    newest = sqlite3.connect(kept[-1])
    assert newest.execute("SELECT COUNT(*) FROM checks_issued").fetchone()[0] == 1


def test_backup_fails_cleanly_when_the_database_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["x", "--db", str(tmp_path / "nope.db"), "--dest", str(tmp_path / "b")])
    assert backup_live_db.main() == 1


def test_live_db_path_prefers_env_then_pointer_file_then_default(tmp_path, monkeypatch):
    pointer = tmp_path / "live_db_path.txt"
    monkeypatch.setattr(db_location, "POINTER", str(pointer))
    monkeypatch.delenv("AP_DB_PATH", raising=False)
    assert db_location.live_db_path() == db_location.DEFAULT

    pointer.write_text("C:\AP_Data\ap_disbursement.db\n", encoding="utf-8")
    assert db_location.live_db_path() == "C:\AP_Data\ap_disbursement.db"

    monkeypatch.setenv("AP_DB_PATH", "D:\other.db")
    assert db_location.live_db_path() == "D:\other.db"


def test_backup_also_saves_the_code_history_next_to_the_database(tmp_path, monkeypatch):
    """2026-09-27: the git history is saved as one verified bundle file beside the backed-up database (off Google Drive)."""
    import shutil
    import subprocess
    if not shutil.which("git"):
        import pytest
        pytest.skip("git not installed")
    src = str(tmp_path / "live.db")
    _make_db(src)
    monkeypatch.setattr(sys, "argv", ["x", "--db", src, "--dest", str(tmp_path / "backups")])
    assert backup_live_db.main() == 0
    bundles = glob.glob(str(tmp_path / "code_backups" / "ap_code_*.bundle"))
    assert len(bundles) == 1
    assert subprocess.run(["git", "bundle", "verify", bundles[0]], capture_output=True).returncode == 0
