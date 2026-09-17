-- Check print batch log (2026-09-17): checks are now printed outside this system (Chrysanth's
-- own batch-print feature, fed by an Excel export), so there's no PDF generation event left to
-- hang a "what got printed when" history on -- this table records that moment instead, at the
-- point the Excel export happens. checks_issued.print_batch_id links each check back to whichever
-- export it went out in; NULL means it's never been included in an export yet (still parked).
CREATE TABLE IF NOT EXISTS check_print_batches (
    print_batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
    exported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    check_count INTEGER NOT NULL,
    total_amount REAL NOT NULL
);

ALTER TABLE checks_issued ADD COLUMN print_batch_id INTEGER REFERENCES check_print_batches(print_batch_id);
