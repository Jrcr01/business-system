-- Owner, 2026-10-17 (mock-up docs/mockups/ah_phone_signal_loss.html): a phone form carries a one-time code; a send whose answer was lost (the signal
-- dropped) and is pressed again is recognised and not saved twice. Kept 30 days.
CREATE TABLE IF NOT EXISTS form_once (
    token TEXT PRIMARY KEY,
    user_email TEXT,
    claimed_at DATETIME NOT NULL,
    done_at DATETIME,                 -- NULL while being saved
    note TEXT,                        -- what was saved, for the "already saved" answer
    url TEXT                          -- where the page went after it
);
