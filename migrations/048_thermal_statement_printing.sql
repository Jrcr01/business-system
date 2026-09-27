-- THERMAL STATEMENT PRINTING (2026-09-27, owner-approved schema): statement versions with their public A4 copy on Google Drive, a log of
-- every thermal copy printed, and a "Needs reprint" mark per batch. Additive only -- nothing existing is changed.
-- Applied by update_database.bat (apply_pending_migrations.diff_and_sync, from schema.sql); this file is the record.

-- One row per version of a batch's statement. The A4 PDF of that version is uploaded to Drive once (never overwritten); its link is what
-- the slip's QR encodes. drive_public = 1 while anyone with the link can open it; a newer printed version switches the old one's sharing off
-- (drive_public = 0, revoked_at set) -- the file itself is kept, privately, for the audit history.
CREATE TABLE IF NOT EXISTS statement_versions (
    version_id INTEGER PRIMARY KEY AUTOINCREMENT,
    statement_id INTEGER NOT NULL,
    version_no INTEGER NOT NULL,
    content_hash TEXT,
    pdf_file_name TEXT,
    drive_file_id TEXT,
    drive_link TEXT,
    drive_public INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    uploaded_at DATETIME,
    revoked_at DATETIME,
    UNIQUE (statement_id, version_no),
    FOREIGN KEY (statement_id) REFERENCES order_statements(statement_id)
);

-- One row per thermal copy sent to a printer. SUCCESS only once the whole job (including the cut) was sent; a FAILED attempt does not
-- count, so the retry is still the ORIGINAL.
CREATE TABLE IF NOT EXISTS statement_prints (
    print_id INTEGER PRIMARY KEY AUTOINCREMENT,
    statement_id INTEGER NOT NULL,
    version_id INTEGER,
    copy_type TEXT NOT NULL CHECK (copy_type IN ('FILE', 'SUPPLIER')),
    print_type TEXT NOT NULL CHECK (print_type IN ('ORIGINAL', 'REPRINT', 'REVISED')),
    backend TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('SUCCESS', 'FAILED')),
    error TEXT,
    drive_file_id TEXT,
    printed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (statement_id) REFERENCES order_statements(statement_id),
    FOREIGN KEY (version_id) REFERENCES statement_versions(version_id)
);
CREATE INDEX IF NOT EXISTS idx_statement_prints_statement ON statement_prints(statement_id, printed_at);

-- 1 = the batch was edited after an unlock and re-locked, and the revised statement has not been printed yet.
ALTER TABLE order_statements ADD COLUMN needs_reprint INTEGER NOT NULL DEFAULT 0;
