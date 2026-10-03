-- REVIEW LIST (2026-10-03). Applied by update_database.bat (schema.sql carries the table; this file is the record).
-- Controls guide and record; they rarely block. A step that breaks a duty rule (the same person ordered and received, received and matched), a new payee, or a price far above
-- its order goes through at once and lands here for the System admin or an owner to tick off. Nothing is deleted: a ticked flag keeps who ticked it and when.
CREATE TABLE IF NOT EXISTS review_flags (
    flag_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                      -- SOD_ORDERED_RECEIVED, SOD_RECEIVED_MATCHED, PRICE_OVER_PO, NEW_PAYEE, ...
    ref_type TEXT,                           -- the table the flag is about
    ref_id INTEGER,
    detail TEXT NOT NULL,                    -- plain words for the reviewer
    raised_by TEXT,
    raised_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    reviewed_by TEXT,
    reviewed_at DATETIME
);
CREATE INDEX IF NOT EXISTS idx_review_flags_open ON review_flags (reviewed_at);
