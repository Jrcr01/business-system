-- PAYMENT CALENDAR NOTES (2026-10-03). Applied by update_database.bat (schema.sql carries the table; this file is the record).
-- A note on a day (a supplier visit, payday): shown on the calendar, moves no date. Holidays and no-check days stay in calendar_blocked_dates, which do move dates.
CREATE TABLE IF NOT EXISTS calendar_notes (
    note_date DATE PRIMARY KEY,
    label TEXT NOT NULL,
    created_by TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
