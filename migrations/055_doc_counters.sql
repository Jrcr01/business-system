-- DOC COUNTERS (Phase F1.4, 2026-09-28). Applied by update_database.bat (schema.sql adds the new table); this file is the record.

-- Document numbers (migration 055, Phase F1.4, 2026-09-28): the last permanent number handed out per series and year ('OS' = order
-- statement batches "YYYY - OS NNNN", 'LN' = loan notes "YYYY - LN NNNN"), taken inside the same locked step that promotes the draft,
-- so two people promoting at the same moment never get the same number and a number is never reused.
CREATE TABLE IF NOT EXISTS doc_counters (
    series TEXT NOT NULL,
    year INTEGER NOT NULL,
    last_no INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (series, year)
);
