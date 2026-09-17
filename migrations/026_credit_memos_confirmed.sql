-- Credit Memo Deduction Guard (2026-09-14): a batch with credit memos silently reduces its
-- net total (v_batch_net_totals sums every credit_memos row for the statement) -- this column
-- gates "Issue checks" behind an explicit human acknowledgment of that reduction, so a memo
-- doesn't quietly change what's owed without anyone noticing before checks go out.
-- Column name matches order_statements' own real PK (statement_id) -- not order_statement_id.
ALTER TABLE order_statements ADD COLUMN credit_memos_confirmed INTEGER NOT NULL DEFAULT 0;

-- Backfill: a statement with no credit memos at all has nothing to confirm.
UPDATE order_statements SET credit_memos_confirmed = 1
WHERE statement_id NOT IN (SELECT DISTINCT statement_id FROM credit_memos);
