-- Migration 035: Phase 8a (Master Data Setup) -- cardholders.legacy_cardholder_id
--
-- Traceable link back to the old Access TblCreditCardHolder.CardHolderId for any cardholder
-- migrated from the legacy system. Plain additive nullable column (no rebuild needed) -- NULL
-- for a cardholder added directly in the new system going forward.

ALTER TABLE cardholders ADD COLUMN legacy_cardholder_id INTEGER;
