"""core step C2 (2026-09-28): the audit log is locked and checked. Owner's choices: "lock + tamper check"; private data hidden only by the
owner-run mask_audit_text.py.
  * the database refuses UPDATE / DELETE on audit_logs and audit_seals (only the one-time user_email fill is allowed);
  * the daily check seals every entry with a key kept outside the database and finds entries changed, deleted, slipped in, cut off, a
    lock removed or the key missing -- made with an outside tool that bypasses the lock; after a problem new entries are not sealed;
  * the daily backup runs the check first, so the backup holds the seals; Data health shows problems;
  * masking hides only the private text, keeps the check clean and records itself."""
import glob
import os
import sqlite3
import sys

import pytest

import backup_live_db
import mask_audit_text
from engines import audit_seal
from engines.audit_seal import PROTECT_TRIGGERS, seal_and_verify
from engines.common import get_conn


def _log(db, action="TEST", new="something happened"):
    with get_conn(db) as conn:
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, new_values) VALUES (acting_user(), ?, 'x', ?)", (action, new))


def _outside_tool(db):
    """What someone with a plain SQLite program could do: no app, no lock (the protection triggers dropped)."""
    conn = sqlite3.connect(db)
    for t in PROTECT_TRIGGERS:
        conn.execute(f"DROP TRIGGER IF EXISTS {t}")
    return conn


# ---- the lock ------------------------------------------------------------------------------------------------------------------

def test_the_database_refuses_changing_or_deleting_audit_entries(db):
    _log(db)
    with get_conn(db) as conn:
        with pytest.raises(sqlite3.DatabaseError, match="cannot be changed"):
            conn.execute("UPDATE audit_logs SET new_values = 'rewritten'")
        with pytest.raises(sqlite3.DatabaseError, match="cannot be deleted"):
            conn.execute("DELETE FROM audit_logs")


def test_only_the_one_time_who_fill_is_allowed(db):
    with get_conn(db) as conn:
        conn.execute("INSERT INTO audit_logs (user_action, entity_name) VALUES ('OLD', 'x')")       # a row from before F1.5
        conn.execute("UPDATE audit_logs SET user_email = 'owner@example.com' WHERE user_email IS NULL")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE audit_logs SET user_email = 'someone.else@example.com'")          # once filled, never again
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE audit_logs SET user_email = 'x@example.com', new_values = 'sneaky' WHERE user_action = 'OLD'")


def test_seals_cannot_be_changed_or_deleted(db):
    _log(db)
    seal_and_verify(db)
    with get_conn(db) as conn:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE audit_seals SET fingerprint = 'x'")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM audit_seals")


# ---- the tamper check ----------------------------------------------------------------------------------------------------------

def test_first_check_seals_the_whole_log_and_keeps_the_key_outside_the_database(db):
    for i in range(3):
        _log(db, new=f"entry {i}")
    res = seal_and_verify(db)
    assert res["ok"] and res["newly_sealed"] == 3 and res["sealed_total"] == 3
    folder = os.path.dirname(db)
    assert os.path.exists(os.path.join(folder, "audit_seal.key")) and os.path.exists(os.path.join(folder, "audit_seal.state"))
    _log(db)
    res = seal_and_verify(db)
    assert res["ok"] and res["newly_sealed"] == 1 and res["sealed_total"] == 4
    with get_conn(db) as conn:
        assert audit_seal.last_result(conn)["ok"]


def test_a_change_made_with_an_outside_tool_is_found_and_new_entries_wait(db):
    for i in range(3):
        _log(db, new=f"entry {i}")
    seal_and_verify(db)
    conn = _outside_tool(db)
    conn.execute("UPDATE audit_logs SET new_values = 'rewritten' WHERE new_values = 'entry 1'")
    conn.commit()
    conn.close()
    _log(db, new="after the change")
    res = seal_and_verify(db)
    assert not res["ok"] and res["newly_sealed"] == 0
    text = " ".join(res["problems"])
    assert "Changed: 1" in text and "Lock removed" in text


def test_a_deleted_entry_is_found(db):
    for i in range(3):
        _log(db, new=f"entry {i}")
    seal_and_verify(db)
    conn = _outside_tool(db)
    conn.execute("DELETE FROM audit_logs WHERE new_values = 'entry 1'")
    conn.commit()
    conn.close()
    assert any(p.startswith("Deleted: 1") for p in seal_and_verify(db)["problems"])


def test_cutting_off_the_newest_entries_and_their_seals_is_found(db):
    for i in range(4):
        _log(db, new=f"entry {i}")
    seal_and_verify(db)
    conn = _outside_tool(db)
    top = conn.execute("SELECT MAX(log_id) FROM audit_logs").fetchone()[0]
    conn.execute("DELETE FROM audit_logs WHERE log_id >= ?", (top - 1,))
    conn.execute("DELETE FROM audit_seals WHERE log_id >= ?", (top - 1,))
    conn.commit()
    conn.close()
    assert any(p.startswith("Cut off") for p in seal_and_verify(db)["problems"])


def test_removing_every_seal_is_found_not_resealed(db):
    for i in range(2):
        _log(db, new=f"entry {i}")
    seal_and_verify(db)
    conn = _outside_tool(db)
    conn.execute("DELETE FROM audit_seals")
    conn.commit()
    conn.close()
    res = seal_and_verify(db)
    assert not res["ok"] and res["sealed_total"] == 0 and any(p.startswith("Cut off") for p in res["problems"])


def test_a_missing_key_is_reported(db):
    _log(db)
    seal_and_verify(db)
    os.remove(os.path.join(os.path.dirname(db), "audit_seal.key"))
    res = seal_and_verify(db)
    assert not res["ok"] and "Key missing" in res["problems"][0]


def test_data_health_shows_the_audit_log_group(db):
    from engines.data_health_service import findings

    _log(db)
    assert [f["check"] for f in findings() if f["group"] == "Audit log"] == ["Audit log not checked yet"]
    seal_and_verify(db)
    assert [f for f in findings() if f["group"] == "Audit log"] == []
    conn = _outside_tool(db)
    conn.execute("UPDATE audit_logs SET new_values = 'rewritten'")
    conn.commit()
    conn.close()
    seal_and_verify(db)
    checks = {f["check"] for f in findings() if f["group"] == "Audit log"}
    assert {"Audit log lock removed", "Audit log was changed outside the system"} <= checks


def test_the_daily_backup_checks_and_seals_first_so_the_backup_holds_the_seals(db, tmp_path, monkeypatch, capsys):
    _log(db)
    dest = tmp_path / "backups"
    monkeypatch.setattr(sys, "argv", ["x", "--db", db, "--dest", str(dest), "--keep", "5"])
    monkeypatch.setattr(backup_live_db, "backup_code", lambda *a, **k: "code history skipped in this test")
    assert backup_live_db.main() == 0
    assert "Audit log check: OK (1 new entries sealed" in capsys.readouterr().out
    copy = sqlite3.connect(glob.glob(str(dest / "ap_disbursement_backup_*.db"))[0])
    assert copy.execute("SELECT COUNT(*) FROM audit_seals").fetchone()[0] == 1
    assert audit_seal.verify(copy, audit_seal.load_key(db)) == []


# ---- masking (the only allowed change) -----------------------------------------------------------------------------------------

CARD = "4111111111111111"


def test_mask_rules():
    assert mask_audit_text.mask_is_allowed(CARD, "************1111") is None
    assert "asterisks" in mask_audit_text.mask_is_allowed(CARD, "hidden")
    assert "at most 4" in mask_audit_text.mask_is_allowed(CARD, "****11111111")
    assert "come from the hidden text" in mask_audit_text.mask_is_allowed(CARD, "****9999")


def test_masking_previews_then_hides_only_the_private_text_and_stays_sealed(db):
    _log(db, "CARD_ADDED", f"card {CARD} added for ACME")
    seal_and_verify(db)
    with get_conn(db) as conn:
        log_id = conn.execute("SELECT log_id FROM audit_logs WHERE user_action = 'CARD_ADDED'").fetchone()[0]

    ok, msg = mask_audit_text.mask(db, log_id, CARD, "************1111", "full card number", apply=False)
    assert ok and msg.startswith("PREVIEW")
    with get_conn(db) as conn:
        assert CARD in conn.execute("SELECT new_values FROM audit_logs WHERE log_id = ?", (log_id,)).fetchone()[0]

    ok, msg = mask_audit_text.mask(db, log_id, CARD, "************1111", "full card number", apply=True)
    assert ok and msg.startswith("DONE")
    with get_conn(db) as conn:
        assert conn.execute("SELECT new_values FROM audit_logs WHERE log_id = ?", (log_id,)).fetchone()[0] == "card ************1111 added for ACME"
        masked = conn.execute("SELECT new_values FROM audit_logs WHERE user_action = 'AUDIT_MASKED'").fetchone()[0]
        assert CARD not in masked and "full card number" in masked
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    assert set(PROTECT_TRIGGERS) <= have                                  # the lock is back
    assert glob.glob(db + ".bak_before_audit_mask_*")                      # backup first
    assert seal_and_verify(db)["ok"]                                       # the renewed seal matches


def test_masking_refuses_text_that_is_not_in_the_entry(db):
    _log(db)
    with get_conn(db) as conn:
        log_id = conn.execute("SELECT MAX(log_id) FROM audit_logs").fetchone()[0]
    ok, msg = mask_audit_text.mask(db, log_id, CARD, "************1111", "card", apply=True)
    assert not ok and "doesn't contain" in msg
