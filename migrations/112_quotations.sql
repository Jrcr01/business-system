-- Stage C, customers (owner, 2026-10-17: "it is good to have this but for quotation we need the quick one", "just build it even i do not need now";
-- mock-up docs/mockups/f_stage_c_customers.html). A quotation is the quick quote kept for a customer: it moves no stock and is not money owed.
CREATE TABLE IF NOT EXISTS quotations (
    qt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    qt_no TEXT NOT NULL UNIQUE,                                     -- QT-0001
    customer_id INTEGER REFERENCES customers(customer_id),          -- NULL = a walk-in, named in customer_name
    customer_name TEXT,
    quote_date DATE NOT NULL,
    good_until DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN', 'SOLD', 'CANCELLED')),   -- lapsed = OPEN past good_until (not stored)
    sale_id INTEGER REFERENCES sales(sale_id),
    note TEXT,
    cancel_reason TEXT,
    created_by TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS quotation_lines (
    line_id INTEGER PRIMARY KEY AUTOINCREMENT,
    qt_id INTEGER NOT NULL REFERENCES quotations(qt_id),
    item_id INTEGER REFERENCES items(id),
    description TEXT NOT NULL,
    qty REAL NOT NULL CHECK(qty > 0),
    unit_price REAL NOT NULL CHECK(unit_price >= 0),
    unit_cost REAL NOT NULL DEFAULT 0                               -- the cost when quoted (FIFO, the next sale's): never shown to the customer
);
CREATE INDEX IF NOT EXISTS idx_quotation_lines_qt ON quotation_lines (qt_id);
CREATE TABLE IF NOT EXISTS customer_prices (
    cp_id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
    item_id INTEGER REFERENCES items(id),                           -- one item ...
    brand TEXT,                                                     -- ... or a whole brand ... (both empty = everything)
    price REAL,                                                     -- a fixed price (one item only) ...
    pct_off REAL,                                                   -- ... or a percent off the price quoted
    since DATE NOT NULL,
    until DATE,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_by TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_customer_prices_c ON customer_prices (customer_id);
CREATE TABLE IF NOT EXISTS customer_contacts (
    contact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
    qt_id INTEGER REFERENCES quotations(qt_id),
    contacted_on DATE NOT NULL,
    note TEXT NOT NULL,
    next_on DATE,                                                   -- call again on (empty = no follow-up)
    created_by TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_customer_contacts_c ON customer_contacts (customer_id);
