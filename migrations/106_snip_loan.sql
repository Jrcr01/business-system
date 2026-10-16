-- Snip, stage 2 (owner, 2026-10-16: "I can use it as well for bank loans when they give amortization table"): a bank loan installment that came from a
-- snipped amortisation table keeps its snip (the source mark).
ALTER TABLE bank_loan_installments ADD COLUMN snip_id INTEGER REFERENCES snips(snip_id);
