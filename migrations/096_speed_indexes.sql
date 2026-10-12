-- SPEED (owner, 2026-10-13: "do the speed and tidy-up pass"). Order statements took two seconds because "last modified" searched the whole
-- audit log once per batch; the lists by batch and by loan read every check and invoice. Indexes only: no data is changed.
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_logs (entity_name, entity_id);
CREATE INDEX IF NOT EXISTS idx_checks_statement ON checks_issued (statement_id);
CREATE INDEX IF NOT EXISTS idx_checks_loan_note ON checks_issued (loan_note_id);
CREATE INDEX IF NOT EXISTS idx_checks_bank_account ON checks_issued (bank_account_id);
CREATE INDEX IF NOT EXISTS idx_invoices_statement ON invoices (statement_id);
CREATE INDEX IF NOT EXISTS idx_credit_memos_statement ON credit_memos (statement_id);
