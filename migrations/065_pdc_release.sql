-- PDC RELEASE (2026-10-02). Applied by update_database.bat (schema.sql carries the columns; this file is the record).
ALTER TABLE checks_issued ADD COLUMN released_date DATE;
ALTER TABLE checks_issued ADD COLUMN released_to TEXT;
