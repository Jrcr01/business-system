-- The cash count at closing (owner, 2026-10-19, review item 8): the cash in the drawer, against the cash sales recorded that day.
CREATE TABLE IF NOT EXISTS cash_counts (
    count_id INTEGER PRIMARY KEY AUTOINCREMENT,
    count_day DATE NOT NULL UNIQUE,
    counted REAL NOT NULL,                   -- the cash in the drawer at closing
    change_fund REAL NOT NULL DEFAULT 0,     -- the starting cash kept in the drawer (not a sale)
    expected REAL NOT NULL,                  -- the cash sales recorded for the day, when counted
    note TEXT,
    counted_by TEXT,
    counted_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
