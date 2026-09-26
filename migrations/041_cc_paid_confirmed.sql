-- Credit card statements: payment not known until the next statement (2026-09-21).
ALTER TABLE cc_statements ADD COLUMN paid_confirmed INTEGER NOT NULL DEFAULT 1 CHECK(paid_confirmed IN (0, 1));
