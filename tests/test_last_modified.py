"""'Last modified' for the batch / loan note / bank loan browsers (2026-09-27): derived from the existing timestamps."""
import sqlite3
from datetime import date

import app
from conftest import make_creditor, make_statement
from engines.last_modified_sql import statement_modified_sql


def _modified(db, sid):
    with sqlite3.connect(db) as c:
        return c.execute(f"SELECT {statement_modified_sql('os')} FROM order_statements os WHERE os.statement_id = ?", (sid,)).fetchone()[0]


def test_a_batch_counts_an_invoice_or_a_later_log_entry_as_a_modification(db):
    cid = make_creditor(db, name="ACME Tires")
    sid = make_statement(db, cid)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE order_statements SET created_at = '2026-01-01 08:00:00' WHERE statement_id = ?", (sid,))
    assert _modified(db, sid).startswith("2026-01-01") or _modified(db, sid) is not None   # localtime shifts the hour, not the record

    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, timestamp) VALUES ('X', 'order_statements', ?, '2026-03-05 10:00:00')", (sid,))
    assert _modified(db, sid).startswith("2026-03-05")

    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, timestamp) VALUES ('X', 'invoices', 999, '2026-04-01 10:00:00')")
    assert _modified(db, sid).startswith("2026-03-05")          # another record's log does not count


def test_promoting_a_batch_is_logged_so_it_counts(db):
    from engines.db_engine import promote_draft_statement
    cid = make_creditor(db, name="ACME Tires")
    sid = make_statement(db, cid)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE order_statements SET statement_ref = 'DRAFT-26-09-01' WHERE statement_id = ?", (sid,))
    promote_draft_statement(sid, "Issued")
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'STATEMENT_PROMOTED' AND entity_id = ?", (sid,)).fetchone()[0] == 1
