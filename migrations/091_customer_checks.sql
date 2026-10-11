-- THE CUSTOMERS' CHECKS, stage B part 2 (owner, 2026-10-11): every check taken on a sale or a collection waits by its check date to be deposited;
-- cleared or bounced is marked from the bank statement (the statement is the truth). A bounced check on a sale makes the invoice owed again.
CREATE TABLE IF NOT EXISTS customer_check_state (
    source TEXT NOT NULL CHECK(source IN ('sale', 'collection')),
    payment_id INTEGER NOT NULL,                     -- sale_payments or collection_payments, per source
    deposited_on DATE,
    cleared_on DATE,
    bounced_on DATE,
    note TEXT,
    created_by TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source, payment_id)
);
