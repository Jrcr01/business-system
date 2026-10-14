-- OPEN LOANS (owner, 2026-10-14: "guaranteed checks these are open loans. it has no maturity as long as we pay interest then it stops when we pay the
-- principal"). A loan note marked open earns interest every month on the principal still owed, on its interest day, from interest_from on (older months
-- are not tracked). The system writes one INTEREST row a month; a principal payment is a PRINCIPAL row. A row is paid by its check (when the bank pays
-- it) or ticked paid in cash or by transfer (paid_at / paid_how).
ALTER TABLE loan_notes ADD COLUMN is_open INTEGER NOT NULL DEFAULT 0;
ALTER TABLE loan_notes ADD COLUMN interest_day INTEGER;
ALTER TABLE loan_notes ADD COLUMN interest_from DATE;
ALTER TABLE loan_installments ADD COLUMN kind TEXT NOT NULL DEFAULT '';
ALTER TABLE loan_installments ADD COLUMN paid_at DATE;
ALTER TABLE loan_installments ADD COLUMN paid_how TEXT;
ALTER TABLE loan_installments ADD COLUMN paid_by TEXT;
