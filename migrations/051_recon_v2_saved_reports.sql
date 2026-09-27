-- BANK RECONCILIATION v2 + SAVED REPORTS (2026-09-28, owner-approved plan). Additive only; applied by update_database.bat (schema.sql);
-- this file is the record.

-- How a bank line needing a look was resolved (Linked by hand / Bank error / Legacy record / Customer bounced check / Expected bank debit /
-- Before the system / Other), what it was matched to besides our checks (a bank-loan installment, a credit-card statement), and suggested
-- check matches the owner rejected (comma-separated check ids, so they are not suggested again).
ALTER TABLE bank_transactions ADD COLUMN resolution TEXT;
ALTER TABLE bank_transactions ADD COLUMN matched_installment_id INTEGER;
ALTER TABLE bank_transactions ADD COLUMN matched_cc_statement_id INTEGER;
ALTER TABLE bank_transactions ADD COLUMN rejected_check_ids TEXT;

-- When every line of an imported statement needing a look was resolved.
ALTER TABLE bank_statement_imports ADD COLUMN reconciled_at DATETIME;

-- "My reports": saved report criteria (a JSON spec checked against engines/report_builder.py's fixed list of datasets and fields -- never
-- free SQL), optionally pinned to the Dashboard and / or included in the overall report.
CREATE TABLE IF NOT EXISTS saved_reports (
    report_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    spec_json TEXT NOT NULL,
    pin_dashboard INTEGER NOT NULL DEFAULT 0,
    include_in_overview INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_run_at DATETIME
);
