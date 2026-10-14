-- THE 8 AM EMAIL (owner, 2026-10-14): who receives it, and what was sent on which day (once a day per person).
CREATE TABLE IF NOT EXISTS morning_email_prefs (
    user_email TEXT PRIMARY KEY,
    receives INTEGER NOT NULL DEFAULT 1 CHECK(receives IN (0, 1))
);
CREATE TABLE IF NOT EXISTS morning_emails_sent (
    day DATE NOT NULL,                        -- Manila day
    user_email TEXT NOT NULL,
    sent_at DATETIME NOT NULL,
    ok INTEGER NOT NULL DEFAULT 0 CHECK(ok IN (0, 1)),
    note TEXT,                                -- why it was not sent
    PRIMARY KEY (day, user_email)
);
