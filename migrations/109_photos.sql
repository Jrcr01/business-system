-- Owner, 2026-10-17 (mock-up docs/mockups/ak_phone_features.html, "All four"): a photo kept as proof -- the receipt of an expense, the front
-- and back of a customer's check at Collect payments. The picture is a file beside the database (photos/), made smaller on the way in.
CREATE TABLE IF NOT EXISTS photos (
    photo_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref_type TEXT NOT NULL CHECK(ref_type IN ('expenses', 'collections')),
    ref_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('receipt', 'check_front', 'check_back')),
    file TEXT NOT NULL,                -- photos/<yyyy-mm>/<token>.jpg, relative to the database's folder
    bytes INTEGER,
    created_by TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_photos_ref ON photos (ref_type, ref_id);
