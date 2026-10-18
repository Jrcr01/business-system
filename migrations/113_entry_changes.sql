-- Corrections to saved entries, kept before and after (owner, 2026-10-19: "make all things for now editable since my brother is adapting the system";
-- "my worry is they incorrectly input data"): what feeds the owner's "Changed today" list on Checks and review.
CREATE TABLE IF NOT EXISTS entry_changes (
    change_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                      -- SALE_EDITED, DELIVERY_EDITED
    ref_type TEXT NOT NULL,                  -- sales, purchase_orders
    ref_id INTEGER,
    ref_text TEXT NOT NULL,                  -- SI 087988, DR 10901 (PO-0012)
    before_text TEXT NOT NULL,
    after_text TEXT NOT NULL,
    why TEXT,
    changed_by TEXT,
    changed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    seen_by TEXT,
    seen_at DATETIME
);
CREATE INDEX IF NOT EXISTS idx_entry_changes_day ON entry_changes (changed_at);
CREATE INDEX IF NOT EXISTS idx_entry_changes_ref ON entry_changes (ref_type, ref_id);
