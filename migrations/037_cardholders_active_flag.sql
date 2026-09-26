-- Cardholder CRUD parity (2026-09-17): matches credit_cards.is_active -- a cardholder who's
-- left, or no longer holds any cards, can be deactivated (soft-delete, reversible) instead of
-- losing their card/statement history. Mirrors creditors.is_active's toggle pattern.
ALTER TABLE cardholders ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;
