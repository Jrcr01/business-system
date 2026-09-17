-- Migration 020: track whether a check has actually been printed
--
-- User's real workflow: select a batch, issue checks (dates + split amounts) across several
-- batches without printing any of them yet ("park" the printing), watch a running total of
-- everything committed-but-not-printed, then print the whole run at once with a summary report.
-- Issuing a check already fully records it in checks_issued today -- the only real gap is that
-- nothing distinguishes "recorded" from "physically printed," so there was no way to build a
-- pending-print queue. NULL = parked/committed-not-printed; stamped automatically the moment a
-- check's PDF is generated (single-check print or as part of a run), not a separate manual step.
--
-- Plain nullable column, no CHECK constraint involved -- unlike migrations 018/019, no table
-- rebuild is needed here.

ALTER TABLE checks_issued ADD COLUMN printed_at DATETIME;
