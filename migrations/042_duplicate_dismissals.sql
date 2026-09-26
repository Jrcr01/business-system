-- Possible-duplicates report: remembers findings marked 'not a duplicate' (2026-09-21).
CREATE TABLE IF NOT EXISTS duplicate_dismissals (
    dismissal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dup_key TEXT NOT NULL UNIQUE,
    note TEXT,
    dismissed_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
