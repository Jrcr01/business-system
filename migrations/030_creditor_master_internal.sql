-- Migration 030: Phase 8a (Master Data Setup) -- creditor_master_internal table
--
-- The old Access payee list mixed 5 internal control accounts in among real payees: Error
-- Checks, Cash, Encashment, Adjustment, Cancelled -- these aren't creditors anyone gets paid,
-- they're bookkeeping placeholders the old system needed a "payee" row to hang a transaction
-- off of. Kept out of `creditors` (whose creditor_type CHECK only recognizes real
-- SUPPLIER/LENDER/BANK_LOAN/CREDIT_CARD payees) so they don't show up in creditor pickers, but
-- still tracked here with their legacy Access PayeeId for traceability.

CREATE TABLE IF NOT EXISTS creditor_master_internal (
    internal_payee_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    type TEXT,
    legacy_payee_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
