-- My home learns each person's most-used pages (owner, 2026-10-19: "my home tiles ... should be the basic tile should have or frequent click by
-- the user"; "auto learn with pinning, admin too").
CREATE TABLE IF NOT EXISTS home_clicks (
    user_email TEXT NOT NULL,
    card TEXT NOT NULL,                      -- a Home card's id (its key, else its page id), a "New ..." card's title, or a menu page id
    day DATE NOT NULL,
    clicks INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_email, card, day)
);
CREATE TABLE IF NOT EXISTS home_pins (
    user_email TEXT NOT NULL,
    card TEXT NOT NULL,
    pinned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_email, card)
);
