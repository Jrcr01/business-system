-- Migration 023: creditors.is_active (soft-delete/inactivate support for Creditor Master)
--
-- Stabilization-phase gap: Creditor Master could only add creditors, never edit or delete them.
-- A creditor with real transaction history (order_statements/invoices/loan_notes/bank_loans/
-- checks_issued/credit_cards referencing it) can't be hard-deleted without breaking that history,
-- so it needs a soft "inactivate" flag instead; a creditor with zero references anywhere is safe
-- to hard-DELETE outright. Plain column add, no rebuild needed (no NOT NULL-drop, no CHECK
-- constraint touched).
--
-- Applied idempotently: checked via PRAGMA table_info before running, same convention as every
-- other migration in this project.

ALTER TABLE creditors ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;
