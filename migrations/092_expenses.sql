-- EXPENSES / PETTY CASH (owner, 2026-10-11, the Books scoped to the Income statement): anything paid from the drawer or the bank that is not a
-- supplier payable -- electricity, rent, wages, meals, supplies. One line each, a category from the owner's own list; taken back only with a
-- reason; feeds the Deposits page and the month's Income statement.
CREATE TABLE IF NOT EXISTS expense_categories (
    category_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    sort INTEGER NOT NULL DEFAULT 0,
    created_by TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO expense_categories (name, sort) VALUES
    ('Electricity', 1), ('Water', 2), ('Rent', 3), ('Wages & helpers', 4), ('Meals', 5), ('Shop supplies', 6),
    ('Fuel & delivery', 7), ('Repairs', 8), ('Permits & taxes', 9), ('Bank charges', 10), ('Others', 11);
CREATE TABLE IF NOT EXISTS expenses (
    expense_id INTEGER PRIMARY KEY AUTOINCREMENT,
    exp_no TEXT UNIQUE,                              -- EXP-0001 ..., the system's own number
    expense_date DATE NOT NULL,
    category_id INTEGER NOT NULL REFERENCES expense_categories(category_id),
    paid_from TEXT NOT NULL DEFAULT 'Cash (drawer)', -- the drawer, a bank account's name, GCash
    detail TEXT,                                     -- what for / received by, e.g. "Meralco bill Sept, OR 5512"
    amount REAL NOT NULL CHECK(amount > 0),
    void_reason TEXT,
    voided_at DATETIME,
    voided_by TEXT,
    created_by TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses (expense_date);
