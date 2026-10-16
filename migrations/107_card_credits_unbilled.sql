-- Owner, 2026-10-17 (mock-up docs/mockups/ac_card_installments.html): a card statement's CREDITS (a balance converted to installments, a reversal,
-- a refund -- the lines ending in C that are not payments) and the UNBILLED INSTALLMENTS still to be billed on the card (the bank's "summary of
-- outstanding installment", one figure). Carried over = earlier total - payments - credits; owed on a card = its latest total due + unbilled.
ALTER TABLE cc_statements ADD COLUMN credits REAL NOT NULL DEFAULT 0;
ALTER TABLE cc_statements ADD COLUMN unbilled_installments REAL NOT NULL DEFAULT 0;

-- The snip reads card statements too: snip_files.kind gains 'card'. SQLite cannot change a CHECK, so the (transient) table is rebuilt without it --
-- engines/snip.KINDS is the rule from now on. apply_pending_migrations only adds columns: run this block ONCE by hand on a database made before 107
-- (the sandbox 2026-10-17; live at the deploy), inside a transaction.
CREATE TABLE snip_files_new (
    file_id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    owner_id INTEGER,
    filename TEXT,
    ext TEXT NOT NULL,
    pages INTEGER NOT NULL DEFAULT 1,
    has_text INTEGER NOT NULL DEFAULT 0,
    uploaded_by TEXT,
    created_at DATETIME NOT NULL
);
INSERT INTO snip_files_new SELECT file_id, token, kind, owner_id, filename, ext, pages, has_text, uploaded_by, created_at FROM snip_files;
DROP TABLE snip_files;
ALTER TABLE snip_files_new RENAME TO snip_files;
