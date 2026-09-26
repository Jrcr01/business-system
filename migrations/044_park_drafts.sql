-- Checks typed into an order statement's "Issue a check" grid but not parked yet (2026-09-27): remembered so they are never
-- lost when you open another order statement or refresh the page. One row per batch; removed when the checks are parked.
CREATE TABLE IF NOT EXISTS park_drafts (
    statement_id INTEGER PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (statement_id) REFERENCES order_statements(statement_id)
);
