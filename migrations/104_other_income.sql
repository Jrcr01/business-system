-- OTHER INCOME (owner, 2026-10-13 "i want to add other income"; go 2026-10-15): money received that is not a sale -- rent received, a supplier's
-- rebate or incentive. One line each, a kind from the owner's own list; taken back only with a reason; adds to the month's Income statement. It does
-- not count in the drawer on Deposits (his answer). Mock-up docs/mockups/q_other_income.html.
CREATE TABLE IF NOT EXISTS income_kinds (
    kind_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    sort INTEGER NOT NULL DEFAULT 0,
    created_by TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO income_kinds (name, sort) VALUES ('Rent received', 1), ('Supplier rebates and incentives', 2), ('Others', 3);
CREATE TABLE IF NOT EXISTS other_income (
    income_id INTEGER PRIMARY KEY AUTOINCREMENT,
    inc_no TEXT UNIQUE,                              -- OI-0001 ..., the system's own number
    income_date DATE NOT NULL,
    kind_id INTEGER NOT NULL REFERENCES income_kinds(kind_id),
    received_in TEXT NOT NULL DEFAULT 'Cash (drawer)', -- the drawer, a bank account's name, GCash
    detail TEXT,                                     -- from whom / what for, e.g. "Stall 2 rent, October"
    amount REAL NOT NULL CHECK(amount > 0),
    void_reason TEXT,
    voided_at DATETIME,
    voided_by TEXT,
    created_by TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_other_income_date ON other_income (income_date);
