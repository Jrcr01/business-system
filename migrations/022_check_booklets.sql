-- Migration 022: Phase 6 Redux -- check_booklets (multi-booklet management) + checks_issued.booklet_id
--
-- Replaces the old passive "checkbooks" range-registry (registered a leaf range purely for a
-- manual, after-the-fact gap audit against whatever check_number text had been typed in) with a
-- stateful booklet: it now knows next_available_number, the cursor the new print-time
-- auto-sequencer reads and advances. Same real-world entity (a physical booklet of blank check
-- leaves, e.g. "Booklet A: 1000-1050") -- extended, not duplicated alongside the old table, so
-- this migration renames/rebuilds it in place rather than leaving two tables describing the same
-- thing. Column names changed too (start_check_number/end_check_number -> start_number/
-- end_number, checkbook_id -> booklet_id) to match the new "booklet" vocabulary used throughout
-- this phase's UI and code.
--
-- next_available_number seeds equal to start_number here (a safe, always-valid placeholder) --
-- for the one real booklet in the live DB it is corrected immediately after this migration by a
-- one-off Python pass that re-derives the true next-available leaf the same way the old
-- next_check_sequence() did (skipping every number already on file in checks_issued or
-- spoiled_checks for that bank account/range), since that logic depends on parsing existing
-- check_number text and doesn't belong in a portable SQL migration. A fresh install has no rows
-- in checkbooks, so this placeholder is never actually wrong there.
--
-- checks_issued.booklet_id is a plain nullable FK add (no rebuild needed) -- existing rows stay
-- NULL (they predate booklet tracking; a best-effort backfill for rows whose check_number falls
-- inside a known booklet range is done in the same Python follow-up pass, not here, for the same
-- text-parsing reason as above).

CREATE TABLE check_booklets (
    booklet_id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_account_id INTEGER NOT NULL,
    booklet_code TEXT,
    start_number INTEGER NOT NULL,
    end_number INTEGER NOT NULL,
    next_available_number INTEGER NOT NULL,
    date_received DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Active', 'Exhausted', 'Archived')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (bank_account_id) REFERENCES bank_accounts(bank_account_id),
    CHECK(end_number >= start_number),
    CHECK(next_available_number >= start_number)
);

INSERT INTO check_booklets (bank_account_id, booklet_code, start_number, end_number, next_available_number, date_received, status, created_at)
SELECT bank_account_id, booklet_code, start_check_number, end_check_number, start_check_number, date_received, status, created_at
FROM checkbooks;

ALTER TABLE checks_issued ADD COLUMN booklet_id INTEGER REFERENCES check_booklets(booklet_id);

DROP TABLE checkbooks;
