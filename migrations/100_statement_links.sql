-- THE SUPPLIER'S LINK (owner, 2026-10-14): a QR code on the thermal strip and the supplier's copy opens that one payment statement's supplier
-- copy with no sign-in. One unguessable code per statement, made the first time it is printed; every opening is recorded.
CREATE TABLE IF NOT EXISTS statement_links (
    statement_id INTEGER PRIMARY KEY REFERENCES order_statements(statement_id),
    token TEXT NOT NULL UNIQUE,               -- 32 random characters: the whole secret
    created_at DATETIME NOT NULL,             -- Manila time
    created_by TEXT,
    revoked_at DATETIME,                      -- switched off by the System admin
    revoked_by TEXT
);
CREATE TABLE IF NOT EXISTS statement_link_opens (
    open_id INTEGER PRIMARY KEY AUTOINCREMENT,
    statement_id INTEGER NOT NULL REFERENCES order_statements(statement_id),
    opened_at DATETIME NOT NULL,              -- Manila time
    ip TEXT,
    country TEXT,
    browser TEXT
);
CREATE INDEX IF NOT EXISTS idx_statement_link_opens ON statement_link_opens (statement_id, open_id);
