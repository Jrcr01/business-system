-- Migration 016: abolish bank cash tracking
--
-- User: "abolish metrobank cash i do not want to trace bank's cash." Investigated first: there
-- was no actual hard block anywhere tied to bank_accounts.current_balance -- it was purely an
-- unenforced debit/credit ledger (decremented on check issuance, credited back on cancel/void
-- and check-amount corrections, plus a manual "adjustment" admin feature), shown as "Available
-- balance" (sidebar) and "Balance" (Admin & Audit). The real spending limits in this app are the
-- payable ceilings (batch net total / loan total_payable), which are untouched by this. Confirmed
-- via AskUserQuestion: drop the column from the schema entirely rather than just stop updating it.
--
-- SQLite 3.35+ supports ALTER TABLE ... DROP COLUMN directly (checked: this app runs on 3.49.1),
-- so no full-table-rebuild dance is needed this time. Verified beforehand that no view, index, or
-- trigger selects current_balance (v_effective_checks joins bank_accounts but never reads the
-- column), so dropping it is safe.

ALTER TABLE bank_accounts DROP COLUMN current_balance;
