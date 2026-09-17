-- Unified Debt Interest Tracking (2026-09-15), credit card half: a CC statement gains a real
-- rolling unpaid balance instead of being a single always-fully-owed total. previous_balance +
-- new_charges + interest_charged = total_amount_due (now computed, not typed in); amount_paid
-- can be less than that; unpaid_balance_next is what's left, and auto-fills next month's
-- previous_balance. total_amount_due itself is kept (not renamed/dropped) -- it's already read
-- by every report/PDF/Credit-Cards-tab query shipped before this migration, and it still means
-- exactly the same thing (the total owed on this statement), just computed differently now.
ALTER TABLE cc_statements ADD COLUMN previous_balance REAL NOT NULL DEFAULT 0;
ALTER TABLE cc_statements ADD COLUMN new_charges REAL NOT NULL DEFAULT 0;
ALTER TABLE cc_statements ADD COLUMN interest_charged REAL NOT NULL DEFAULT 0;
ALTER TABLE cc_statements ADD COLUMN amount_paid REAL NOT NULL DEFAULT 0;
ALTER TABLE cc_statements ADD COLUMN unpaid_balance_next REAL NOT NULL DEFAULT 0;

-- Going-forward only: every existing statement defaults unpaid_balance_next to 0, so the
-- "still outstanding" logic below (and everywhere else effective_status/outstanding-amount is
-- computed) falls straight back through to the old due-date-based rule for all of them
-- unchanged -- only a statement entered through the new rolling-balance form can carry a real
-- balance past its due date.
DROP VIEW IF EXISTS v_cc_due_alerts;
CREATE VIEW v_cc_due_alerts AS
SELECT
    c.creditor_name AS bank_issuer,
    cc.card_name,
    cc.card_number_last4,
    cs.cc_statement_id,
    cs.due_date,
    CASE WHEN cs.unpaid_balance_next > 0 THEN cs.unpaid_balance_next ELSE cs.total_amount_due END AS total_amount_due,
    CAST(JULIANDAY(cs.due_date) - JULIANDAY('now', 'localtime') AS INTEGER) AS days_until_due,
    CASE
        WHEN JULIANDAY(cs.due_date) - JULIANDAY('now', 'localtime') <= 3 THEN 'CRITICAL (0-3 Days)'
        WHEN JULIANDAY(cs.due_date) - JULIANDAY('now', 'localtime') <= 7 THEN 'WARNING (4-7 Days)'
        ELSE 'UPCOMING'
    END AS urgency_status
FROM cc_statements cs
JOIN credit_cards cc ON cs.card_id = cc.card_id
JOIN creditors c ON cc.creditor_id = c.creditor_id
WHERE cs.status NOT IN ('Paid', 'Cancelled')
  AND (cs.unpaid_balance_next > 0 OR cs.due_date >= DATE('now', 'localtime'))
ORDER BY cs.due_date ASC;
