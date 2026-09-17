-- Migration 029: Phase 8a (Master Data Setup) -- settings table
--
-- Plain key/value store for system-wide configuration that used to live only in people's heads
-- (or in the Access UI's hardcoded defaults) -- e.g. the primary bank's display name, the
-- standard terms picklist offered when adding a creditor. Not meant for high-churn data; just a
-- small number of seeded rows the user can override from an Admin & Audit > Settings screen.

CREATE TABLE IF NOT EXISTS settings (
    setting_id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    value TEXT,
    category TEXT,
    description TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_by TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
