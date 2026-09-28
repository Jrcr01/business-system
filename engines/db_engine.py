import os
import sqlite3
from contextlib import contextmanager
import pandas as pd
from datetime import datetime

from engines.clock import manila_now, manila_today, sql_now, sql_today  # noqa: F401

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

BUSY_TIMEOUT_MS = 10000
_WAL_DONE: set = set()


def _ensure_wal(conn: sqlite3.Connection, attempts: int = 50) -> None:
    """Switch the file to WAL only if it is not WAL yet. Switching needs the file to itself and SQLite does not wait for that (the busy
    timeout does not apply), so when two connections open a brand-new database at the same instant one of them gets "database is
    locked" -- found by the two-users-at-once tests (2026-09-28). Retry briefly; once the other connection has switched, the check
    below sees 'wal' and there is nothing left to do."""
    import time

    for i in range(attempts):
        try:
            if str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal":
                return
            conn.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() or i == attempts - 1:
                raise
            time.sleep(0.1)


def open_db(db_path: str | None = None) -> sqlite3.Connection:
    """THE one door to the database (Phase F1.2, 2026-09-28) -- every connection in engines / views / app comes from here (through
    common.get_conn()), so the safety settings are the same everywhere:
      * foreign_keys = ON   -- links between records are checked (SQLite has them OFF unless asked, per connection);
      * busy_timeout        -- wait up to 10 s when someone else is saving, instead of failing with "database is locked";
      * journal_mode = WAL  -- people can read while someone saves (stored in the file; recent saves sit in <db>-wal until SQLite
                               checkpoints them, so copy the database only with the backup API -- db_location.safe_copy()).
    `db_path` defaults to this module's DB_PATH; callers pass their own module's DB_PATH so tests / scripts that point one module at
    another file keep working."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    # one clock (Phase F1.3): SQL asks Python for Manila's today / now, never the computer's clock
    conn.create_function("manila_today", 0, sql_today)
    conn.create_function("manila_now", 0, sql_now)
    if path not in _WAL_DONE and path != ":memory:" and not str(path).startswith("file::memory:"):
        _ensure_wal(conn)                               # stored in the file itself: once per file per run is enough
        _WAL_DONE.add(path)
    return conn



@contextmanager
def write_tx(db_path: str | None = None):
    """ONE locked step for anything that checks and then saves (Phase F1.4, 2026-09-28): BEGIN IMMEDIATE takes the write lock FIRST, so
    the check (e.g. "is there still room in this batch?") and the save happen together -- a second person's save waits (busy_timeout)
    until this one is finished, then sees its result. Commits at the end, rolls back on an error. Code inside must NOT call
    conn.commit() (it would end the locked step early). Reads through other connections inside the step are fine (WAL: they see
    everything saved before the lock was taken, and nobody else can save in between)."""
    conn = open_db(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        if conn.in_transaction:
            conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def next_doc_number(conn: sqlite3.Connection, series: str, year: int, in_use: int = 0) -> int:
    """The next permanent number of a series ('OS' batches, 'LN' loan notes) for a year, from the doc_counters table -- never
    "highest existing + 1" read outside a lock (two people promoting at once could get the same number). Call it inside write_tx().
    `in_use` = the highest number already on file: the counter never goes below it (numbers made before the counter existed, or
    typed in by hand), and a number is never reused even if its batch is later deleted."""
    row = conn.execute("SELECT last_no FROM doc_counters WHERE series = ? AND year = ?", (series, int(year))).fetchone()
    nxt = max(int(row[0]) if row else 0, int(in_use)) + 1
    conn.execute("""INSERT INTO doc_counters (series, year, last_no) VALUES (?, ?, ?)
                    ON CONFLICT (series, year) DO UPDATE SET last_no = excluded.last_no""", (series, int(year), nxt))
    return nxt


def df_query(sql_query: str, params: tuple = ()) -> pd.DataFrame:
    """Executes SQL query and returns a Pandas DataFrame with NULLs coerced to empty strings."""
    with open_db(DB_PATH) as conn:
        df = pd.read_sql_query(sql_query, conn, params=params)
        return df.fillna('')

def promote_draft_statement(statement_id: int, target_status: str) -> str:
    """Atomically transitions an Order Statement and assigns a permanent YYYY - OS XXXX sequence ID."""
    if target_status not in ('Issued',):
        raise ValueError("Target status must be 'Issued'.")

    with write_tx(DB_PATH) as conn:                  # Phase F1.4: read, number and save in ONE locked step
        cursor = conn.cursor()
        cursor.execute("SELECT statement_ref, status FROM order_statements WHERE statement_id = ?", (statement_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Order Statement ID {statement_id} not found.")
            
        current_ref, current_status = row
        permanent_ref = current_ref

        # Only a batch still carrying its DRAFT- placeholder gets a new number. A batch that went back to Draft after an
        # un-park keeps the permanent OS number it already has -- promoting it again must not burn a new one.
        if current_ref.startswith("DRAFT-"):
            current_year = manila_now().year
            prefix = f"{current_year} - OS "

            # Take the MAX sequence number actually in use, not "whichever row has the highest
            # statement_id" -- statement_id order and promotion order aren't the same thing (a
            # batch created earlier can be promoted later than one created after it), so the
            # previous "ORDER BY statement_id DESC LIMIT 1" could pick a lower sequence number
            # than one already assigned to an earlier-promoted-but-higher-statement_id batch,
            # producing a ref collision (UNIQUE constraint failed) on the very next promotion.
            cursor.execute("SELECT statement_ref FROM order_statements WHERE statement_ref LIKE ?", (f"{prefix}%",))
            existing_seqs = [int(suffix) for (ref,) in cursor.fetchall() if (suffix := ref.replace(prefix, "").strip()).isdigit()]
            next_seq = next_doc_number(conn, "OS", current_year, max(existing_seqs, default=0))   # counter, never reused

            permanent_ref = f"{prefix}{next_seq:04d}"

        cursor.execute("""
            UPDATE order_statements 
            SET statement_ref = ?, status = ? 
            WHERE statement_id = ?
        """, (permanent_ref, target_status, statement_id))
        
        cursor.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('STATEMENT_PROMOTED', 'order_statements', ?, ?)",
            (statement_id, f"{current_ref} -> {permanent_ref} ({target_status})"),
        )
    return permanent_ref