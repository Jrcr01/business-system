-- The stand-in cheque number a check carried in its Chrysanth export file (2026-09-27), remembered so a re-download of that batch
-- gives exactly the numbers Chrysanth already holds. NULL for checks never exported (and for those exported before this existed).
ALTER TABLE checks_issued ADD COLUMN export_placeholder TEXT;
