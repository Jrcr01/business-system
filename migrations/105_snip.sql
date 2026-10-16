-- SNIP FROM A DOCUMENT (owner, 2026-10-16; like DataSnipper, inside Price lists, Bank loans and the Vendor SOA matcher; engines/snip.py). A document
-- (PDF or photo) kept beside the database; each snip = one box on one page, with what was read, so a figure can be traced back ("source" mark).
CREATE TABLE IF NOT EXISTS snip_files (
    file_id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT NOT NULL UNIQUE,                      -- the file's name on disk (<token>.<ext> in data/snip_files/)
    kind TEXT NOT NULL CHECK(kind IN ('price', 'loan', 'soa')),
    owner_id INTEGER,                                -- the supplier (price, soa) or the bank loan it was uploaded for
    filename TEXT,
    ext TEXT NOT NULL,
    pages INTEGER NOT NULL DEFAULT 1,
    has_text INTEGER NOT NULL DEFAULT 0,             -- 1 = a PDF with real text (read exactly); 0 = a picture (read by OCR)
    uploaded_by TEXT,
    created_at DATETIME NOT NULL
);
CREATE TABLE IF NOT EXISTS snips (
    snip_id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES snip_files(file_id),
    page INTEGER NOT NULL,
    x0 REAL NOT NULL, y0 REAL NOT NULL, x1 REAL NOT NULL, y1 REAL NOT NULL,   -- the box, in the page's own coordinates
    mode TEXT NOT NULL CHECK(mode IN ('table', 'text')),
    rows TEXT,                                       -- what was read (JSON), as used
    used_for TEXT,                                   -- 'price_import', 'loan_schedule', 'soa_match'
    ref_id INTEGER,
    created_by TEXT,
    created_at DATETIME NOT NULL
);
ALTER TABLE supplier_price_list ADD COLUMN snip_id INTEGER REFERENCES snips(snip_id);
