"""Set-up combined (owner, 2026-10-09): Creditors, Settings (Numbers, Users, Audit log, Backup) and Checks and review (Review list, Data checks, Possible
duplicates) -- in place of Roles and controls, Admin and audit and Data health."""
import os
import sqlite3

from conftest import fetchone
from tests.test_web_d0 import _admin, _sign_in_new


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def test_settings_has_the_audit_log_and_backup(web, db, tmp_path, monkeypatch):
    monkeypatch.setenv("AP_BACKUP_DIR", str(tmp_path))
    _admin(web)
    tabs = web.get("/settings/").content.decode()
    assert 'href="/settings/audit/"' in tabs and 'href="/settings/backup/"' in tabs
    with sqlite3.connect(_db()) as conn:
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, new_values) VALUES ('admin@slicks.test', 'CHECK_MARKED_CLEARED', 'checks_issued', 'Check 0004471 cleared')")
    page = web.get("/settings/audit/?q=0004471").content.decode()
    assert "Check marked cleared" in page and "0004471" in page
    assert web.get("/settings/audit/?download=xlsx").status_code == 200
    web.post("/settings/backup/", {})
    made = [f for f in os.listdir(tmp_path) if f.startswith("manual_")]
    assert len(made) == 1 and made[0] in web.get("/settings/backup/").content.decode()
    assert web.get(f"/settings/backup/?download={made[0]}").status_code == 200
    assert web.get("/settings/backup/?download=../../etc/passwd").status_code == 404       # only a file from the list
    family = _sign_in_new("family@slicks.test", "owner")
    assert family.get("/settings/audit/").status_code == 403 and family.get("/settings/backup/").status_code == 403


def test_checks_and_review_ticks_the_review_list(web, db):
    _admin(web)
    with sqlite3.connect(_db()) as conn:
        conn.execute("INSERT INTO review_flags (kind, ref_type, ref_id, detail) VALUES ('CC_PAYMENT_MISMATCH', 'cc_statements', NULL, 'Gold - 1234: SOA 2,500, daily 1,000')")
        fid = conn.execute("SELECT MAX(flag_id) FROM review_flags").fetchone()[0]
    page = web.get("/review/").content.decode()
    assert "Card payments recorded daily differ from the SOA" in page and "Data checks" in page and "Possible duplicates" in page
    web.post("/review/", {"action": "reviewed", f"pick_{fid}": "1", "note": "the SOA is right"})
    assert fetchone(_db(), "SELECT reviewed_at IS NOT NULL FROM review_flags WHERE flag_id = ?", (fid,))[0] == 1
    assert web.get("/review/?tab=data").status_code == 200 and web.get("/review/?tab=dups").status_code == 200
    home = web.get("/").content.decode()
    assert "Checks and review" in home and "Roles and controls" not in home and "Admin and audit" not in home and "Data health" not in home
