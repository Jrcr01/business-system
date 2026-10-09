-- DAILY PAYABLES (owner, 2026-10-09): a bank-loan installment is paid only when it CLEARS -- the bank statement shows the debit (a bank line matched to
-- it, bank_transactions.matched_installment_id) or someone ticks it paid (cleared_at). Its due date passing no longer counts (it used to: "past = paid").
-- The one-time cut-off marks the installments due before the first imported bank statement "cleared before go-live" (engines/clearing.apply_cutoff).
ALTER TABLE bank_loan_installments ADD COLUMN cleared_at DATE;
ALTER TABLE bank_loan_installments ADD COLUMN cleared_by TEXT;
ALTER TABLE bank_loan_installments ADD COLUMN cleared_note TEXT;
