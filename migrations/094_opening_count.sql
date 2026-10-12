-- THE OPENING COUNT, ADD AS YOU GO (owner, 2026-10-13: "we have inventory notebook" / "these are not only brand we have"): a count that starts
-- empty; each tire is found or added at the rack. A line may carry the cost typed from the notebook (used only while the item has no cost of
-- its own), and remembers that its item was made in this count.
ALTER TABLE stock_counts ADD COLUMN opening INTEGER NOT NULL DEFAULT 0;
ALTER TABLE stock_count_lines ADD COLUMN cost_typed REAL;
ALTER TABLE stock_count_lines ADD COLUMN is_new INTEGER NOT NULL DEFAULT 0;
