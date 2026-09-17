-- Payment calendar "greyed out" dates (2026-09-14): both Philippine holidays and any
-- custom date the user marks as a no-issue day render the same way on the calendar,
-- distinguished only by `kind` for the label shown.
--
-- Holiday dates below are computed, not guessed: fixed civil dates as-is; Maundy
-- Thursday/Good Friday via the standard Meeus/Jones/Butcher Easter algorithm; National
-- Heroes Day as the actual last Monday of August for each year. Deliberately excluded:
-- Eid'l Fitr and Eid'l Adha, which are set by lunar sighting and annual presidential
-- proclamation with no reliable formula -- add those by hand once announced, same as
-- any other custom date.
CREATE TABLE IF NOT EXISTS calendar_blocked_dates (
    blocked_date DATE PRIMARY KEY,
    label TEXT,
    kind TEXT NOT NULL DEFAULT 'custom' CHECK(kind IN ('holiday', 'custom')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO calendar_blocked_dates (blocked_date, label, kind) VALUES
    ('2026-01-01', 'New Year''s Day', 'holiday'),
    ('2026-04-02', 'Maundy Thursday', 'holiday'),
    ('2026-04-03', 'Good Friday', 'holiday'),
    ('2026-04-09', 'Araw ng Kagitingan', 'holiday'),
    ('2026-05-01', 'Labor Day', 'holiday'),
    ('2026-06-12', 'Independence Day', 'holiday'),
    ('2026-08-21', 'Ninoy Aquino Day', 'holiday'),
    ('2026-08-31', 'National Heroes Day', 'holiday'),
    ('2026-11-30', 'Bonifacio Day', 'holiday'),
    ('2026-12-25', 'Christmas Day', 'holiday'),
    ('2026-12-30', 'Rizal Day', 'holiday'),
    ('2027-01-01', 'New Year''s Day', 'holiday'),
    ('2027-03-25', 'Maundy Thursday', 'holiday'),
    ('2027-03-26', 'Good Friday', 'holiday'),
    ('2027-04-09', 'Araw ng Kagitingan', 'holiday'),
    ('2027-05-01', 'Labor Day', 'holiday'),
    ('2027-06-12', 'Independence Day', 'holiday'),
    ('2027-08-21', 'Ninoy Aquino Day', 'holiday'),
    ('2027-08-30', 'National Heroes Day', 'holiday'),
    ('2027-11-30', 'Bonifacio Day', 'holiday'),
    ('2027-12-25', 'Christmas Day', 'holiday'),
    ('2027-12-30', 'Rizal Day', 'holiday');
