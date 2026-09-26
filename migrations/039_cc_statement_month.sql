-- Statement month (2026-09-19): records which billing month a credit card statement is for
-- ('YYYY-MM'), separately from its due date. Nullable; existing rows fall back to their due
-- date's month wherever it's read.
ALTER TABLE cc_statements ADD COLUMN statement_month TEXT;
