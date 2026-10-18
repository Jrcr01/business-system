-- The morning "Yesterday" phone notification (owner, 2026-10-19: "me and owners, sales and purchases, send no sales and purchases too";
-- "our shop is always open. remind my family to input as soon as possible"; "not 2 pm but after working hours").
CREATE TABLE IF NOT EXISTS yesterday_push_prefs (
    user_email TEXT PRIMARY KEY,
    receives INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS yesterday_push_sent (
    day DATE NOT NULL,                       -- the day reported on (yesterday)
    slot TEXT NOT NULL,                      -- am (the report) / pm (the after-hours reminder)
    user_email TEXT NOT NULL,
    phones INTEGER NOT NULL DEFAULT 0,
    sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (day, slot, user_email)
);
