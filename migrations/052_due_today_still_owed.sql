-- DUE TODAY = STILL OWED (2026-09-28, owner: "due today = still owed" everywhere). A check dated today is not cleared yet; it counts
-- as cleared once its date has PASSED (was: on its date). The same rule was applied to every paid / outstanding figure in the code
-- (engines/*, views/*). Applied by update_database.bat (schema.sql; diff_and_sync re-creates changed views); this file is the record.
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
        WHEN c.status = 'Active' AND c.check_date < DATE('now', '-180 days') THEN 'Stale'
        WHEN c.status = 'Active' AND c.check_date < DATE('now', 'localtime') THEN 'Cleared'
        ELSE c.status
    END AS effective_status,
    c.replacement_check_id,
    c.statement_id,
    c.loan_note_id
FROM checks_issued c
JOIN creditors cr ON c.creditor_id = cr.creditor_id
JOIN bank_accounts b ON c.bank_account_id = b.bank_account_id;
