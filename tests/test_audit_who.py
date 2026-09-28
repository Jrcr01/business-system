"""Phase F1.5 (2026-09-28): every audit row says who did it, and typing several check numbers by hand is all or nothing.
  * audit_logs.user_email is written by the SQL function acting_user() on every audit INSERT -- the owner until logins (F2),
    the person set by acting_as() otherwise; two people at once never get each other's name;
  * a guard: no audit INSERT anywhere in the program leaves user_email out;
  * update_database fills the rows made before F1.5 with the owner's email, once;
  * manual check numbers: one bad number saves none of the batch (before, the numbers typed before the bad one stayed saved)."""
import os
import re
import sqlite3
import threading
from datetime import date

import app
import apply_pending_migrations
from conftest import PROJECT_ROOT, SCHEMA_PATH, fetchone, make_creditor, make_statement
from engines.acting_user import OWNER_EMAIL, acting_as, acting_user
from engines.common import get_conn


def _checks(db, n):
    creditor_id = make_creditor(db, name="Who Supplier")
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "WHO-1", date(2026, 1, 1), date(2026, 1, 31), 1000.0 * n, [], statement_id)
    return [app.issue_check(creditor_id, 1000.0, date(2026, 3, 1 + i), statement_id=statement_id)[2] for i in range(n)]


def test_audit_rows_are_the_owners_until_logins_exist(db):
    _checks(db, 1)
    rows = get_conn(db).execute("SELECT user_email FROM audit_logs").fetchall()
    assert rows and {r[0] for r in rows} == {OWNER_EMAIL}


def test_audit_rows_carry_the_person_set_by_acting_as(db):
    with acting_as("Brother@Example.com"):
        _checks(db, 1)
    assert acting_user() == OWNER_EMAIL                 # back to the owner after the block
    emails = {r[0] for r in get_conn(db).execute("SELECT user_email FROM audit_logs WHERE user_action = 'CHECK_ISSUED'")}
    assert emails == {"brother@example.com"}


def test_two_people_at_once_keep_their_own_names():
    seen = {}
    barrier = threading.Barrier(2)

    def run(email):
        with acting_as(email):
            barrier.wait()
            seen[email] = get_conn(":memory:").execute("SELECT acting_user()").fetchone()[0]

    threads = [threading.Thread(target=run, args=(e,)) for e in ("a@x.com", "b@x.com")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert seen == {"a@x.com": "a@x.com", "b@x.com": "b@x.com"}


def test_no_audit_insert_leaves_the_person_out():
    missing = []
    for folder, _, files in os.walk(PROJECT_ROOT):
        if any(part in folder for part in ("archive", "tests", "__pycache__", ".git", ".venv")):
            continue
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                for m in re.finditer(r"INSERT INTO audit_logs \(([^)]*)\)", text):
                    if "user_email" not in m.group(1):
                        missing.append(f"{os.path.relpath(path, PROJECT_ROOT)}: {m.group(0)}")
    assert missing == []


def test_update_database_fills_the_old_rows_with_the_owner_once(tmp_path):
    old_db = str(tmp_path / "old.db")
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema = f.read()
    schema = schema.replace(",\n    user_email TEXT          -- who did it", "\n    -- (no user_email before F1.5)", 1)
    conn = sqlite3.connect(old_db)
    conn.executescript(schema)
    assert "user_email" not in {r[1] for r in conn.execute("PRAGMA table_info(audit_logs)")}
    conn.executemany("INSERT INTO audit_logs (user_action, entity_name) VALUES (?, 'x')", [("OLD_1",), ("OLD_2",)])
    conn.commit()
    conn.close()

    report = apply_pending_migrations.diff_and_sync(old_db)

    assert "audit_logs.user_email" in report["columns_added"]
    conn = sqlite3.connect(old_db)
    assert [r[0] for r in conn.execute("SELECT user_email FROM audit_logs")] == [OWNER_EMAIL, OWNER_EMAIL]
    conn.execute("INSERT INTO audit_logs (user_action, entity_name) VALUES ('LATER', 'x')")   # not the program's way; left empty
    conn.commit()
    conn.close()
    apply_pending_migrations.diff_and_sync(old_db)                                          # second run: column exists, no refill
    assert fetchone(old_db, "SELECT user_email FROM audit_logs WHERE user_action = 'LATER'")[0] is None


def test_manual_numbers_all_saved_when_every_number_is_good(db):
    ids = _checks(db, 3)

    ok, msg, done = app.assign_check_numbers_manual({ids[0]: "5001", ids[1]: "5002", ids[2]: "5003"}, None)

    assert ok and done == ids
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CHECK_NUMBER_ASSIGNED' AND user_email = ?", (OWNER_EMAIL,))[0] == 3


def test_manual_numbers_are_all_or_nothing_when_one_is_taken(db):
    ids = _checks(db, 3)
    assert app.log_spoiled_check("5003", "jam")[0]              # the third number is already logged as spoiled

    ok, msg, done = app.assign_check_numbers_manual({ids[0]: "5001", ids[1]: "5002", ids[2]: "5003"}, None)

    assert not ok and "Nothing was assigned" in msg and done == []
    assert fetchone(db, "SELECT COUNT(*) FROM checks_issued WHERE check_number IS NOT NULL")[0] == 0     # 5001 and 5002 rolled back too
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CHECK_NUMBER_ASSIGNED'")[0] == 0


def test_manual_numbers_are_all_or_nothing_when_a_check_changed_meanwhile(db):
    ids = _checks(db, 2)
    assert app.assign_check_numbers_manual({ids[1]: "6002"}, None)[0]     # someone numbered the second check in the meantime

    ok, msg, done = app.assign_check_numbers_manual({ids[0]: "6001", ids[1]: "6003"}, None)

    assert not ok and "Nothing was assigned" in msg
    assert fetchone(db, "SELECT check_number FROM checks_issued WHERE check_id = ?", (ids[0],))[0] is None
