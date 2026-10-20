-- The AI foundation (owner, 2026-10-20: "I want to ready my system for this"): every call to the AI is logged with its cost in pesos, so the
-- monthly cap can be kept and the System admin sees what it costs. engines/ai_client.py writes here; nothing else does.
CREATE TABLE IF NOT EXISTS ai_usage (
    usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
    used_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    user_email TEXT,
    feature TEXT NOT NULL,                   -- which part of the system asked, e.g. 'price_photo'
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_php REAL NOT NULL DEFAULT 0,
    ok INTEGER NOT NULL DEFAULT 1 CHECK(ok IN (0, 1)),
    note TEXT                                -- why it failed, when it did
);
CREATE INDEX IF NOT EXISTS idx_ai_usage_used_at ON ai_usage(used_at);
