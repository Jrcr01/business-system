-- ONE CLOCK (Phase F1.3, 2026-09-28): the views use Manila's business date from the program (SQL functions manila_today() /
-- manila_now(), registered on every connection by engines/db_engine.open_db) instead of the computer's clock -- a cloud server runs on
-- UTC, which would make "today" yesterday from 00:00 to 08:00 Manila time. Also fixes the Stale rule, which used UTC ('now' without
-- 'localtime'). Applied by update_database.bat (schema.sql; diff_and_sync re-creates changed views); this file is the record.
-- Note: these two views can only be read through the program (an outside SQLite viewer does not know manila_today()).
DROP VIEW IF EXISTS v_effective_checks;
CREATE VIEW v_effective_checks AS
SELECT 
    c.check_id,
    c.creditor_id,
    cr.creditor_name,
    c.bank_account_id,
    b.bank_name,
    c.check_number,
    c.check_date,
    c.amount,
    c.status AS recorded_status,
    CASE 
        WHEN c.status = 'Active' AND c.check_date < DATE(manila_today(), '-180 days') THEN 'Stale'
        WHEN c.status = 'Active' AND c.check_date < manila_today() THEN 'Cleared'
        ELSE c.status
    END AS effective_status,
    c.replacement_check_id,
    c.statement_id,
    c.loan_note_id
FROM checks_issued c
JOIN creditors cr ON c.creditor_id = cr.creditor_id
JOIN bank_accounts b ON c.bank_account_id = b.bank_account_id;

DROP VIEW IF EXISTS v_cc_due_alerts;
CREATE VIEW v_cc_due_alerts AS
SELECT
    c.creditor_name AS bank_issuer,
    cc.card_name,
    cc.card_number_last4,
    cs.cc_statement_id,
    cs.due_date,
    CASE WHEN cs.unpaid_balance_next > 0 THEN cs.unpaid_balance_next ELSE cs.total_amount_due END AS total_amount_due,
    CAST(JULIANDAY(cs.due_date) - JULIANDAY(manila_now()) AS INTEGER) AS days_until_due,
    CASE
        WHEN JULIANDAY(cs.due_date) - JULIANDAY(manila_now()) <= 3 THEN 'CRITICAL (0-3 Days)'
        WHEN JULIANDAY(cs.due_date) - JULIANDAY(manila_now()) <= 7 THEN 'WARNING (4-7 Days)'
        ELSE 'UPCOMING'
    END AS urgency_status
FROM cc_statements cs
JOIN credit_cards cc ON cs.card_id = cc.card_id
JOIN creditors c ON cc.creditor_id = c.creditor_id
WHERE cs.status NOT IN ('Paid', 'Cancelled')
  AND (cs.unpaid_balance_next > 0 OR cs.due_date >= manila_today())
ORDER BY cs.due_date ASC;
