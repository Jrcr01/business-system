-- Migration 003: supplier-level payment terms
-- Terms live on the creditor (supplier), not per-invoice, so every invoice for that
-- vendor picks it up live and it stays current if the supplier's terms change later.

ALTER TABLE creditors ADD COLUMN terms TEXT;
