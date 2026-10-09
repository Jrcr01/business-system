-- CARD PAYMENTS, THE SOA IS FINAL (owner, 2026-10-09): when the next SOA confirms what was paid on a statement (card_rules.confirm_cc_payment_from_next_statement),
-- the moment is kept here. From then on the statement's paid figure is the SOA's: taking back a payment recorded on Daily payables only removes that record,
-- and the reports count the SOA amount, not the daily records, when the two differ.
ALTER TABLE cc_statements ADD COLUMN soa_confirmed_at DATETIME;
UPDATE cc_statements SET soa_confirmed_at = (SELECT MIN(a.timestamp) FROM audit_logs a WHERE a.entity_name = 'cc_statements'
                                             AND a.entity_id = cc_statements.cc_statement_id AND a.user_action = 'CC_PAYMENT_CONFIRMED')
 WHERE soa_confirmed_at IS NULL;
