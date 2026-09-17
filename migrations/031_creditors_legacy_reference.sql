-- Migration 031: Phase 8a (Master Data Setup) -- creditors.legacy_payee_id
--
-- Every creditor migrated from the old Access TblPayee export needs a traceable link back to
-- its original PayeeId, for audit/reconciliation during the Access-to-new-system cutover. Plain
-- additive nullable column (no rebuild needed) -- NULL for any creditor created directly in the
-- new system going forward.

ALTER TABLE creditors ADD COLUMN legacy_payee_id INTEGER;
