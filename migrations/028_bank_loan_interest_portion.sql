-- Unified Debt Interest Tracking (2026-09-15), bank loan half. User: "for bank loan still
-- simplified but i need to get the portion of interest so i can track properly" -- explicitly
-- NOT the full amortization-table-required workflow originally proposed; just one optional
-- extra number per installment, typed straight off the bank's document like Amount already is.
-- Defaults to 0 so every existing installment (and any new one where interest isn't filled in)
-- simply contributes nothing to interest tracking -- still "no computation of any kind" per the
-- original Phase 3 simplification, just one more fact recorded when it's actually known.
ALTER TABLE bank_loan_installments ADD COLUMN interest_portion REAL NOT NULL DEFAULT 0 CHECK(interest_portion >= 0);
