-- THE REVENUE CYCLE, STAGE A (owner, 2026-10-11): "Sell, and the stock moves". The tables are in schema.sql (item_barcodes, stock_counts,
-- stock_count_lines, affiliate_moves, affiliate_move_lines, affiliate_settlements, si_booklets, si_cancelled, sales, sale_lines, sale_payments,
-- sale_returns); apply_pending_migrations.py adds them and these columns to a database that lacks them.
ALTER TABLE items ADD COLUMN oldest_dot TEXT;
ALTER TABLE items ADD COLUMN last_counted_at DATE;
ALTER TABLE po_receipt_lines ADD COLUMN dot TEXT;
ALTER TABLE customers ADD COLUMN is_affiliate INTEGER NOT NULL DEFAULT 0 CHECK(is_affiliate IN (0, 1));
ALTER TABLE customers ADD COLUMN credit_allowed INTEGER NOT NULL DEFAULT 0 CHECK(credit_allowed IN (0, 1));
