-- WHAT A PERSON SEES (owner, 2026-10-09: "i want to control what they can see, what they can do"): the pages the System admin hid from this person.
-- Hidden = gone everywhere: menu, Home, search results, the page's address. What they may do stays in user_restrictions.
CREATE TABLE IF NOT EXISTS user_hidden_pages (
    user_id INTEGER NOT NULL REFERENCES app_users(user_id),
    page_id TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT,
    PRIMARY KEY (user_id, page_id)
);
