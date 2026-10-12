-- CREDIT CARD REMINDERS ON THE PHONE (owner, 2026-10-13: a daily payables message "will only stresses us. instead have credit cards only
-- incoming statement, due date, annual fee"). The phones allowed to receive them, who gets which cards, and what was already sent.
CREATE TABLE IF NOT EXISTS push_subscriptions (
    sub_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_email TEXT NOT NULL,
    endpoint TEXT NOT NULL UNIQUE,                                  -- the browser maker's push address for that one phone
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    device TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_ok_at DATETIME
);
CREATE TABLE IF NOT EXISTS card_reminder_prefs (
    user_email TEXT PRIMARY KEY,
    scope TEXT NOT NULL DEFAULT 'own' CHECK(scope IN ('off', 'own', 'all')),
    cardholder_id INTEGER                                           -- which cardholder this person is ('own' cards)
);
CREATE TABLE IF NOT EXISTS card_reminders_sent (
    kind TEXT NOT NULL,                                             -- STATEMENT / DUE / FEE
    card_id INTEGER NOT NULL,
    key TEXT NOT NULL,                                              -- the statement month, the due date, the fee month
    slot TEXT NOT NULL,                                             -- am / pm
    sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    phones INTEGER NOT NULL DEFAULT 0,
    UNIQUE (kind, card_id, key, slot)
);
