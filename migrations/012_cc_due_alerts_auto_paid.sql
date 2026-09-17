-- Migration 012: credit card statements are auto-considered paid once their due date passes
--
-- User: "drop but dont remove direct cash settlement, for now let be once the due date is pass
-- considered it paid." Same standing convention this app already uses elsewhere (a check's
-- cleared status, Bank Loan installments): once due_date is in the past, treat it as settled
-- without requiring a manual action -- but keep the "Direct cash settlement" form as-is for
-- explicitly recording an early/actual payment when that matters.
--
-- View-only change, no table/data touched: v_cc_due_alerts drops the OVERDUE bucket (an overdue
-- statement is now auto-paid, not overdue) and excludes any statement whose due_date has passed
-- from the WHERE clause entirely, so it stops surfacing as a stale alert forever. The stored
-- cc_statements.status column is untouched -- a manually-settled statement still says 'Paid'
-- with a real payment_date/payment_method; an untouched-but-past-due one just stops alerting.

DROP VIEW IF EXISTS v_cc_due_alerts;

CREATE VIEW v_cc_due_alerts AS
SELECT
    c.creditor_name AS bank_issuer,
    cc.card_name,
    cc.card_number_last4,
    cs.cc_statement_id,
    cs.statement_ref,
    cs.due_date,
    cs.total_amount_due,
    CAST(JULIANDAY(cs.due_date) - JULIANDAY('now', 'localtime') AS INTEGER) AS days_until_due,
    CASE
        WHEN JULIANDAY(cs.due_date) - JULIANDAY('now', 'localtime') <= 3 THEN 'CRITICAL (0-3 Days)'
        WHEN JULIANDAY(cs.due_date) - JULIANDAY('now', 'localtime') <= 7 THEN 'WARNING (4-7 Days)'
        ELSE 'UPCOMING'
    END AS urgency_status
FROM cc_statements cs
JOIN credit_cards cc ON cs.card_id = cc.card_id
JOIN creditors c ON cc.creditor_id = c.creditor_id
WHERE cs.status = 'Unpaid' AND cs.due_date >= DATE('now', 'localtime')
ORDER BY cs.due_date ASC;
