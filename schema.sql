-- Metrobank AP & Disbursement System DDL Schema

CREATE TABLE IF NOT EXISTS creditors (
    creditor_id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor_name TEXT NOT NULL,
    creditor_type TEXT NOT NULL CHECK(creditor_type IN ('SUPPLIER', 'LENDER', 'BANK_LOAN', 'CREDIT_CARD')),
    tin TEXT,
    contact_person TEXT,
    contact_number TEXT,
    email TEXT,
    terms TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    -- legacy_payee_id (migration 031, Phase 8a): traceable link back to the old Access
    -- TblPayee.PayeeId for any creditor migrated from the legacy system; NULL for one created
    -- directly in the new system.
    legacy_payee_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    -- migration 060 (2026-10-01): a LESSOR is a creditor we pay rent to. Stored as a LENDER-type creditor flagged here (the type list is a CHECK constraint on a table
    -- used everywhere); screens and reports show it as "Lessor" and keep it out of the lender lists.
    is_lessor INTEGER NOT NULL DEFAULT 0 CHECK(is_lessor IN (0, 1)),
    -- migration 064 (2026-10-02): the supplier's usual delivery days; the default delivery date on a new purchase order is the order date plus this (empty = 7).
    lead_days INTEGER CHECK(lead_days IS NULL OR (lead_days >= 0 AND lead_days <= 365))
);

-- Bank cash is not tracked in this system (abolished 2026-09-06, migration 016) -- this table
-- only identifies which physical account a check is drawn against, nothing more.
-- check_width_mm/check_height_mm/check_offset_x_mm/check_offset_y_mm (migration 019): the check
-- engine's print calibration for this account -- tunable after a real test print, since no
-- physical leaf has been measured. Mirrors Chrysanth Check Writer's own per-account Alignment tab.
CREATE TABLE IF NOT EXISTS bank_accounts (
    bank_account_id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_name TEXT NOT NULL,
    account_name TEXT NOT NULL,
    account_number TEXT UNIQUE NOT NULL,
    branch_code TEXT NOT NULL DEFAULT '399',
    check_width_mm REAL NOT NULL DEFAULT 203.2,
    check_height_mm REAL NOT NULL DEFAULT 76.2,
    check_offset_x_mm REAL NOT NULL DEFAULT 0,
    check_offset_y_mm REAL NOT NULL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Seed Metrobank Account 1 (Branch Code 399)
INSERT INTO bank_accounts (bank_account_id, bank_name, account_name, account_number, branch_code)
VALUES (1, 'Metrobank', 'Metrobank Account 1', 'MBTC-399-PRIMARY', '399')
ON CONFLICT(bank_account_id) DO UPDATE SET
    bank_name = 'Metrobank',
    account_name = 'Metrobank Account 1',
    branch_code = '399';

CREATE TABLE IF NOT EXISTS order_statements (
    statement_id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor_id INTEGER NOT NULL,
    statement_ref TEXT UNIQUE NOT NULL,
    total_amount REAL NOT NULL CHECK(total_amount >= 0),
    status TEXT NOT NULL DEFAULT 'Draft' CHECK(status IN ('Draft', 'Issued', 'Cancelled')),
    is_locked INTEGER NOT NULL DEFAULT 0,
    -- Credit Memo Deduction Guard (migration 026): must be explicitly confirmed (or the memo(s)
    -- removed) before checks can be issued, whenever this batch has at least one credit memo --
    -- a memo silently reduces the net total (v_batch_net_totals), and this stops that reduction
    -- from going unnoticed. Defaults to 1 (nothing to confirm) on a fresh build since a brand
    -- new statement has no memos yet; add_credit_memo()/edit_credit_memo() reset it to 0.
    credit_memos_confirmed INTEGER NOT NULL DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    -- Thermal statement printing (migration 048): 1 = edited after an unlock and re-locked, revised statement not printed yet.
    needs_reprint INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id)
);

-- Cardholder registry: who inside the company holds/uses a card, separate from which bank
-- issued it (creditors, type CREDIT_CARD). "there is a need of credit card holder registry."
-- legacy_cardholder_id (migration 035, Phase 8a): traceable link back to the old Access
-- TblCreditCardHolder id for a cardholder migrated from the legacy system.
-- is_active (migration 037, Credit Card CRUD parity): reversible soft-delete, same pattern as
-- creditors.is_active / credit_cards.is_active -- a cardholder who's left stops appearing as
-- pickable for new cards/statements but keeps their existing history.
CREATE TABLE IF NOT EXISTS cardholders (
    cardholder_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    department TEXT,
    contact_number TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    legacy_cardholder_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- annual_fee_due_month is month-only, not a full date: the real anniversary is the card's
-- original issuance date, which the user doesn't have on file for existing cards, so this
-- tracks only what's actually knowable ("annual fee due sometime in March").
-- statement_day (the nth day of the month a statement generates) is a permanent property of
-- the card, not re-entered per statement -- "make this a permanent information of the card."
-- credit_limit nullable, network/is_active/card_number_full/legacy_card_id added (migration
-- 034, Phase 8a): cards migrated from Access have no credit limit on file, plus a payment
-- network, an active/closed flag, and -- separate from the last4 used everywhere in the UI --
-- the complete card number, needed as a real reference when paying a bill through a banking
-- app. add_credit_card()/edit_credit_card() (app.py) still require a positive credit limit for
-- anything entered through the UI; only a direct import can leave it NULL.
CREATE TABLE IF NOT EXISTS credit_cards (
    card_id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor_id INTEGER NOT NULL,
    cardholder_id INTEGER,
    card_name TEXT NOT NULL,
    card_number_last4 TEXT NOT NULL,
    card_number_full TEXT,
    network TEXT,
    credit_limit REAL CHECK(credit_limit IS NULL OR credit_limit > 0),
    annual_fee_due_month INTEGER CHECK(annual_fee_due_month BETWEEN 1 AND 12),
    statement_day INTEGER CHECK(statement_day BETWEEN 1 AND 31),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    legacy_card_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id),
    FOREIGN KEY (cardholder_id) REFERENCES cardholders(cardholder_id)
);

CREATE TABLE IF NOT EXISTS invoices (
    invoice_id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor_id INTEGER NOT NULL,
    doc_ref TEXT NOT NULL,
    invoice_date DATE NOT NULL,
    due_date DATE NOT NULL,
    gross_amount REAL NOT NULL CHECK(gross_amount >= 0),
    trade_discounts TEXT,
    amount REAL NOT NULL CHECK(amount >= 0),
    status TEXT NOT NULL DEFAULT 'Unpaid' CHECK(status IN ('Unpaid', 'Partially Paid', 'Paid', 'Cancelled')),
    statement_id INTEGER,
    paid_via_card_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    -- migration 058 (2026-10-01): 1 = the "Beginning balance (from MS Access)" line of a migrated batch -- a balance, not a real invoice (see engines/beginning_balance_service.py)
    is_opening_balance INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id),
    FOREIGN KEY (statement_id) REFERENCES order_statements(statement_id),
    FOREIGN KEY (paid_via_card_id) REFERENCES credit_cards(card_id),
    UNIQUE(creditor_id, doc_ref)
);

-- principal_amount/monthly_interest_rate/total_payable nullable, status CHECK includes
-- 'Pending Setup' (migration 032, Phase 8a): a lender migrated from Access with no real
-- principal/rate/schedule on file yet must be representable without inventing numbers.
CREATE TABLE IF NOT EXISTS loan_notes (
    loan_note_id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor_id INTEGER NOT NULL,
    note_ref TEXT UNIQUE NOT NULL,
    issue_date DATE NOT NULL,
    principal_amount REAL CHECK(principal_amount IS NULL OR principal_amount > 0),
    monthly_interest_rate REAL,
    total_interest REAL DEFAULT 0.0,
    total_payable REAL,
    status TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Active', 'Settled', 'Defaulted', 'Cancelled', 'Pending Setup')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    -- migration 059 (2026-10-01): "Beginning balance (from MS Access)" label on a migrated placeholder note (interest / terms not split out yet)
    is_beginning_balance INTEGER NOT NULL DEFAULT 0,
    beginning_label_removed INTEGER NOT NULL DEFAULT 0,
    -- migration 060 (2026-10-01): a LEASE is rent paid to a lessor: a loan note with no interest whose schedule is the rent checks. is_lease = 1 keeps it off the Lenders
    -- page and out of debt figures; lease_description is the property / what is rented.
    is_lease INTEGER NOT NULL DEFAULT 0 CHECK(is_lease IN (0, 1)),
    lease_description TEXT,
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id)
);

-- Repayment schedule for a loan note. Entered progressively as real payment dates are
-- committed to (not fully planned upfront at signing). interest is a suggested value
-- (rate x outstanding balance x days-since-previous-row/30) but stays freely editable per
-- row for lender-specific conventions (e.g. flat monthly division instead of day-proration).
CREATE TABLE IF NOT EXISTS loan_installments (
    installment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_note_id INTEGER NOT NULL,
    installment_no INTEGER NOT NULL,
    due_date DATE NOT NULL,
    principal REAL NOT NULL DEFAULT 0 CHECK(principal >= 0),
    interest REAL NOT NULL DEFAULT 0 CHECK(interest >= 0),
    outstanding_balance REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pending' CHECK(status IN ('Pending', 'Active')),
    check_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (loan_note_id) REFERENCES loan_notes(loan_note_id),
    FOREIGN KEY (check_id) REFERENCES checks_issued(check_id)
);

-- Loan beneficiary registry: who actually uses/benefits from a bank loan the company pays for
-- -- a person or an affiliate company, distinct from the bank (creditors) and reusable across
-- multiple loans, same idea as the credit card cardholder registry but broader (not always a
-- person).
CREATE TABLE IF NOT EXISTS loan_beneficiaries (
    beneficiary_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    beneficiary_type TEXT NOT NULL CHECK(beneficiary_type IN ('Person', 'Affiliate')),
    contact_number TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- start_date/maturity_date nullable, has_amortization added (migration 033, Phase 8a): a bank
-- loan migrated from Access with no amortization schedule uploaded yet has neither real dates
-- nor a schedule; has_amortization flags which loans still need one (0 = placeholder).
CREATE TABLE IF NOT EXISTS bank_loans (
    bank_loan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor_id INTEGER NOT NULL,
    beneficiary_id INTEGER,
    loan_ref TEXT UNIQUE NOT NULL,
    account_number TEXT,
    start_date DATE,
    maturity_date DATE,
    repricing_date DATE,
    principal_amount REAL,
    mode_of_payment TEXT DEFAULT 'ADA',
    has_amortization INTEGER NOT NULL DEFAULT 0 CHECK(has_amortization IN (0, 1)),
    status TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Active', 'Closed')),
    is_cancelled INTEGER NOT NULL DEFAULT 0 CHECK(is_cancelled IN (0, 1)),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    -- migration 059 (2026-10-01): "Beginning balance (from MS Access)" label on a bank loan imported as a schedule only (no start date, no principal)
    is_beginning_balance INTEGER NOT NULL DEFAULT 0,
    beginning_label_removed INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id),
    FOREIGN KEY (beneficiary_id) REFERENCES loan_beneficiaries(beneficiary_id)
);

-- Phase 3 (Bank Loan): just Due Date + Amount per installment, per explicit user simplification
-- ("i need only the Due date and amount") -- no principal/interest/insurance breakdown, no
-- computation of any kind. "Paid" is not a stored field, same standing convention as a check's
-- "cleared" status elsewhere in this app (CLAUDE.md): implied automatically once due_date is in
-- the past relative to today, computed at query time. Deliberately no check_id/checks_issued
-- link, unlike loan_installments (Loan Notes) -- settlement here is untracked cash.
-- interest_portion (migration 028, 2026-09-15, Unified Debt Interest Tracking): optional, typed
-- straight off the bank's document like Amount already is -- still "no computation of any kind"
-- per the original Phase 3 simplification, just one more fact recorded when it's known, so
-- interest paid on bank loans can feed the interest-tracking dashboard/KPI.
CREATE TABLE IF NOT EXISTS bank_loan_installments (
    installment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_loan_id INTEGER NOT NULL,
    installment_no INTEGER NOT NULL,
    due_date DATE NOT NULL,
    amount REAL NOT NULL DEFAULT 0 CHECK(amount >= 0),
    interest_portion REAL NOT NULL DEFAULT 0 CHECK(interest_portion >= 0),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (bank_loan_id) REFERENCES bank_loans(bank_loan_id)
);

-- check_booklets (migration 022, Phase 6 Redux): a stateful multi-booklet registry, e.g.
-- "Booklet A: 1000-1050". next_available_number is the cursor the print-time auto-sequencer
-- reads and advances (skipping any number already logged in spoiled_checks). Replaces the old
-- passive "checkbooks" range-registry that only supported an after-the-fact manual gap audit --
-- same real-world entity, extended in place rather than duplicated, since check numbers are now
-- assigned by the system, not typed in freehand.
CREATE TABLE IF NOT EXISTS check_booklets (
    booklet_id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_account_id INTEGER NOT NULL,
    booklet_code TEXT,
    start_number INTEGER NOT NULL,
    end_number INTEGER NOT NULL,
    next_available_number INTEGER NOT NULL,
    date_received DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Active', 'Exhausted', 'Archived')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    -- migration 056 (2026-10-01): the last leaf the person holding this booklet says they have used (from the photos the owner receives) -- see checkbook_service.unrecorded_leaves
    reported_up_to INTEGER,
    FOREIGN KEY (bank_account_id) REFERENCES bank_accounts(bank_account_id),
    CHECK(end_number >= start_number),
    CHECK(next_available_number >= start_number)
);

-- check_print_batches (migration 036, 2026-09-17): checks are now printed outside this system
-- (Chrysanth's own batch-print feature, fed by an Excel export), so there's no in-app PDF
-- generation event left to hang a "what got printed when" log on -- this records that moment
-- instead, at the point the Excel export happens. checks_issued.print_batch_id (below) links
-- each check back to whichever export it went out in; NULL means never exported yet (still
-- parked).
CREATE TABLE IF NOT EXISTS check_print_batches (
    print_batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
    exported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    check_count INTEGER NOT NULL,
    total_amount REAL NOT NULL,
    printed_confirmed_at DATETIME
);

-- Every check must belong to a batch (statement_id for Supplier, loan_note_id for Lender) --
-- no standalone check path anywhere in the system, including Encashment (migration 018).
-- printed_at (migration 020): NULL means committed/recorded but not yet physically printed --
-- a check "belongs to the current check run" simply by having status='Active' AND printed_at
-- IS NULL, no separate run-tracking table. Stamped automatically the moment its PDF is generated.
-- check_number is nullable (migration 021, Phase 6 Redux): a check number is assigned at PRINT
-- time, not issue/park time -- a parked check genuinely has no number yet until the print-time
-- auto-sequencer (or a manual backfill) assigns one from a booklet (booklet_id, migration 022).
CREATE TABLE IF NOT EXISTS checks_issued (
    check_id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor_id INTEGER NOT NULL,
    bank_account_id INTEGER NOT NULL,
    statement_id INTEGER,
    loan_note_id INTEGER,
    booklet_id INTEGER,
    print_batch_id INTEGER,
    check_number TEXT,
    check_date DATE NOT NULL,
    amount REAL NOT NULL CHECK(amount >= 0),
    status TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Active', 'Cleared', 'Cancelled', 'Replaced', 'Spoiled')),
    replacement_check_id INTEGER,
    replacement_reason TEXT,
    replaced_at DATETIME,
    printed_at DATETIME,
    export_placeholder TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    -- bank statement import (migration 050): the bank's own cleared date and the matching bank line (NULL = not on a statement yet)
    bank_cleared_date DATE,
    bank_txn_id INTEGER,
    -- migration 054 (2026-10-01): a void keeps its date and reason; a handwritten check (written outside the system, recorded afterwards) is tagged
    cancelled_at DATETIME,
    cancel_reason TEXT,
    is_handwritten INTEGER NOT NULL DEFAULT 0,
    -- migration 055 (2026-10-01): "settled outside the bank" -- the payee gave the check back and was paid in cash (never marked cleared by the bank)
    settled_outside_at DATE,
    settled_outside_reason TEXT,
    settled_outside_note TEXT,
    -- migration 065 (2026-10-02, PDC release): the day the supplier COLLECTED this printed check, and who took it. NULL = still in the office.
    released_date DATE,
    released_to TEXT,
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id),
    FOREIGN KEY (bank_account_id) REFERENCES bank_accounts(bank_account_id),
    FOREIGN KEY (statement_id) REFERENCES order_statements(statement_id),
    FOREIGN KEY (loan_note_id) REFERENCES loan_notes(loan_note_id),
    FOREIGN KEY (booklet_id) REFERENCES check_booklets(booklet_id),
    FOREIGN KEY (print_batch_id) REFERENCES check_print_batches(print_batch_id),
    CHECK (statement_id IS NOT NULL OR loan_note_id IS NOT NULL)
);

-- Migration 038 (2026-09-17): app-code alone (_is_check_number_available()) was the only thing
-- stopping two checks from ending up with the same number on the same bank account -- a real
-- backstop matters here since more than one code path writes check_number (issuance, manual
-- correction, booklet assignment). NULL check_number (a parked, not-yet-printed check) is exempt
-- since SQLite treats every NULL as distinct under a UNIQUE index, so any number of parked checks
-- can coexist.
CREATE UNIQUE INDEX IF NOT EXISTS idx_checks_issued_unique_number ON checks_issued(bank_account_id, check_number);

-- guarantee_checks (migration 057, 2026-10-01): a check given to a lender as security, usually with no date, later cancelled or taken back once the loan is
-- settled (owner: "guarantee checks"). It is NOT a payment: it never enters checks_issued, payables, the schedule or the cash forecast. face_amount may be
-- unknown. status: Held (the lender has it) / Returned (taken back or cancelled) / Presented (the bank paid or received it -- an alert).
CREATE TABLE IF NOT EXISTS guarantee_checks (
    guarantee_id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_account_id INTEGER NOT NULL DEFAULT 1,
    check_number TEXT NOT NULL,
    creditor_id INTEGER NOT NULL,
    loan_note_id INTEGER,
    face_amount REAL CHECK(face_amount IS NULL OR face_amount >= 0),
    given_on DATE,
    status TEXT NOT NULL DEFAULT 'Held' CHECK(status IN ('Held', 'Returned', 'Presented')),
    returned_on DATE,
    return_note TEXT,
    presented_on DATE,
    bank_txn_id INTEGER,
    note TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (bank_account_id, check_number),
    FOREIGN KEY (bank_account_id) REFERENCES bank_accounts(bank_account_id),
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id),
    FOREIGN KEY (loan_note_id) REFERENCES loan_notes(loan_note_id)
);

CREATE TABLE IF NOT EXISTS credit_memos (
    credit_memo_id INTEGER PRIMARY KEY AUTOINCREMENT,
    statement_id INTEGER NOT NULL,
    memo_ref TEXT NOT NULL,
    memo_date DATE NOT NULL,
    amount REAL NOT NULL CHECK(amount > 0),
    reason TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (statement_id) REFERENCES order_statements(statement_id)
);

CREATE TABLE IF NOT EXISTS check_corrections (
    correction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    check_id INTEGER NOT NULL,
    statement_id INTEGER,
    correction_type TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    reason TEXT,
    corrected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (check_id) REFERENCES checks_issued(check_id),
    FOREIGN KEY (statement_id) REFERENCES order_statements(statement_id)
);

CREATE TABLE IF NOT EXISTS invoice_corrections (
    correction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,
    statement_id INTEGER,
    field_name TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    corrected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id),
    FOREIGN KEY (statement_id) REFERENCES order_statements(statement_id)
);

CREATE TABLE IF NOT EXISTS spoiled_checks (
    spoiled_id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_account_id INTEGER NOT NULL,
    check_number TEXT NOT NULL,
    reason TEXT,
    logged_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (bank_account_id) REFERENCES bank_accounts(bank_account_id)
);

-- No statement_ref (never used beyond being typed in) and no statement_date (the day a
-- statement generates is a permanent property of the card -- credit_cards.statement_day --
-- not something that varies per cycle) -- just what actually varies: due_date and the amount.
-- Rolling balance columns (migration 027, 2026-09-15, Unified Debt Interest Tracking):
-- previous_balance + new_charges + interest_charged = total_amount_due (computed, not typed in
-- directly, once entered through the rolling-balance form); amount_paid can be less than that;
-- unpaid_balance_next is what's left, and auto-fills next month's previous_balance. Defaults to
-- 0 so a statement entered the old way (just a lump total_amount_due) behaves identically to
-- before -- going-forward only, no backfill.
CREATE TABLE IF NOT EXISTS cc_statements (
    cc_statement_id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL,
    -- statement_month (migration 039, 2026-09-19): which billing month this statement is FOR, as
    -- 'YYYY-MM', entered separately from due_date so a month can be tracked even when its due date
    -- falls in the next one. NULL on rows imported before this existed -- readers fall back to the
    -- month of due_date (COALESCE(statement_month, SUBSTR(due_date, 1, 7))).
    statement_month TEXT,
    due_date DATE NOT NULL,
    total_amount_due REAL NOT NULL CHECK(total_amount_due >= 0),
    previous_balance REAL NOT NULL DEFAULT 0,
    new_charges REAL NOT NULL DEFAULT 0,
    interest_charged REAL NOT NULL DEFAULT 0,
    amount_paid REAL NOT NULL DEFAULT 0,
    unpaid_balance_next REAL NOT NULL DEFAULT 0,
    -- paid_confirmed (migration 041, 2026-09-21): 0 = the amount paid is not known yet -- it is confirmed by the
    -- NEXT statement's carried-over balance. Existing rows are 1 (their figures stand as entered).
    paid_confirmed INTEGER NOT NULL DEFAULT 1 CHECK(paid_confirmed IN (0, 1)),
    status TEXT NOT NULL DEFAULT 'Unpaid' CHECK(status IN ('Unpaid', 'Partially Paid', 'Paid', 'Cancelled')),
    payment_method TEXT DEFAULT 'Cash / Online' CHECK(payment_method IN ('Cash / Online', 'Direct Debit', 'Check')),
    payment_date DATE,
    payment_check_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (card_id) REFERENCES credit_cards(card_id),
    FOREIGN KEY (payment_check_id) REFERENCES checks_issued(check_id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_action TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    entity_id INTEGER,
    old_values TEXT,
    new_values TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    user_email TEXT          -- who did it (Phase F1.5, migration 056): written by the SQL function acting_user()
);

-- Audit log protection (core step C2, migration 057, 2026-09-28; owner: "lock + tamper check"). The database refuses every change to
-- audit_logs / audit_seals; the only allowed UPDATE fills user_email on a row that has none (update_database's one-time F1.5 fill).
-- Private data that must be hidden goes through mask_audit_text.py only (owner-run, backup first, logged as AUDIT_MASKED).
-- audit_seals: one HMAC fingerprint per audit row, made with a key kept OUTSIDE the database (engines/audit_seal.py).
CREATE TABLE IF NOT EXISTS audit_seals (
    log_id INTEGER PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    sealed_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER IF NOT EXISTS audit_logs_no_update
BEFORE UPDATE ON audit_logs
WHEN NOT (OLD.user_email IS NULL AND NEW.user_email IS NOT NULL
          AND NEW.log_id IS OLD.log_id AND NEW.user_action IS OLD.user_action AND NEW.entity_name IS OLD.entity_name
          AND NEW.entity_id IS OLD.entity_id AND NEW.old_values IS OLD.old_values AND NEW.new_values IS OLD.new_values
          AND NEW.timestamp IS OLD.timestamp)
BEGIN
    SELECT RAISE(ABORT, 'The audit log cannot be changed.');
END;

CREATE TRIGGER IF NOT EXISTS audit_logs_no_delete
BEFORE DELETE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'The audit log cannot be deleted.');
END;

CREATE TRIGGER IF NOT EXISTS audit_seals_no_update
BEFORE UPDATE ON audit_seals
BEGIN
    SELECT RAISE(ABORT, 'Audit seals cannot be changed.');
END;

CREATE TRIGGER IF NOT EXISTS audit_seals_no_delete
BEFORE DELETE ON audit_seals
BEGIN
    SELECT RAISE(ABORT, 'Audit seals cannot be deleted.');
END;

-- Views
CREATE VIEW IF NOT EXISTS v_batch_net_totals AS
SELECT
    os.statement_id,
    os.creditor_id,
    os.statement_ref,
    os.total_amount AS gross_total,
    COALESCE(cm.memo_total, 0.0) AS credit_memo_total,
    os.total_amount - COALESCE(cm.memo_total, 0.0) AS net_total,
    os.status,
    os.is_locked
FROM order_statements os
LEFT JOIN (
    SELECT statement_id, SUM(amount) AS memo_total
    FROM credit_memos
    GROUP BY statement_id
) cm ON cm.statement_id = os.statement_id;

CREATE VIEW IF NOT EXISTS v_effective_checks AS
SELECT 
    c.check_id,
    c.creditor_id,
    cr.creditor_name,
    c.bank_account_id,
    b.bank_name,
    c.check_number,
    c.check_date,
    c.amount,
    c.status AS recorded_status,
    CASE 
        WHEN c.status = 'Active' AND c.check_date < DATE(manila_today(), '-180 days') THEN 'Stale'
        WHEN c.status = 'Active' AND c.check_date < manila_today() THEN 'Cleared'
        ELSE c.status
    END AS effective_status,
    c.replacement_check_id,
    c.statement_id,
    c.loan_note_id
FROM checks_issued c
JOIN creditors cr ON c.creditor_id = cr.creditor_id
JOIN bank_accounts b ON c.bank_account_id = b.bank_account_id;

-- A statement past its due date is treated as paid automatically -- "for now, let it be once
-- the due date is pass considered it paid" -- so it drops out of the alert list rather than
-- sitting as a permanent OVERDUE flag; the manual "Direct cash settlement" path is kept (not
-- removed) for actually recording an early/explicit payment.
-- unpaid_balance_next-aware (migration 027): a statement carrying a real rolling balance still
-- alerts even once its due date has passed, since the old amount is genuinely still owed -- a
-- legacy statement (unpaid_balance_next always 0) keeps the original due-date-only behavior.
CREATE VIEW IF NOT EXISTS v_cc_due_alerts AS
SELECT
    c.creditor_name AS bank_issuer,
    cc.card_name,
    cc.card_number_last4,
    cs.cc_statement_id,
    cs.due_date,
    CASE WHEN cs.unpaid_balance_next > 0 THEN cs.unpaid_balance_next ELSE cs.total_amount_due END AS total_amount_due,
    CAST(JULIANDAY(cs.due_date) - JULIANDAY(manila_now()) AS INTEGER) AS days_until_due,
    CASE
        WHEN JULIANDAY(cs.due_date) - JULIANDAY(manila_now()) <= 3 THEN 'CRITICAL (0-3 Days)'
        WHEN JULIANDAY(cs.due_date) - JULIANDAY(manila_now()) <= 7 THEN 'WARNING (4-7 Days)'
        ELSE 'UPCOMING'
    END AS urgency_status
FROM cc_statements cs
JOIN credit_cards cc ON cs.card_id = cc.card_id
JOIN creditors c ON cc.creditor_id = c.creditor_id
WHERE cs.status NOT IN ('Paid', 'Cancelled')
  AND (cs.unpaid_balance_next > 0 OR cs.due_date >= manila_today())
ORDER BY cs.due_date ASC;

-- Triggers
-- Structural safety net for order_statements.total_amount (migration 017): keeps it in sync with
-- SUM(invoices.amount) for that statement_id automatically, so correctness no longer depends on
-- every invoice-mutating code path remembering to call recompute_statement_total(). Mirrors that
-- function's exact logic (no status filter -- a Cancelled invoice still counts).
CREATE TRIGGER IF NOT EXISTS trg_invoices_ai_recompute_total
AFTER INSERT ON invoices
WHEN NEW.statement_id IS NOT NULL
BEGIN
    UPDATE order_statements
    SET total_amount = (SELECT COALESCE(SUM(amount), 0.0) FROM invoices WHERE statement_id = NEW.statement_id)
    WHERE statement_id = NEW.statement_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_invoices_au_recompute_total
AFTER UPDATE ON invoices
WHEN NEW.statement_id IS NOT NULL OR OLD.statement_id IS NOT NULL
BEGIN
    UPDATE order_statements
    SET total_amount = (SELECT COALESCE(SUM(amount), 0.0) FROM invoices WHERE statement_id = NEW.statement_id)
    WHERE NEW.statement_id IS NOT NULL AND statement_id = NEW.statement_id;

    UPDATE order_statements
    SET total_amount = (SELECT COALESCE(SUM(amount), 0.0) FROM invoices WHERE statement_id = OLD.statement_id)
    WHERE OLD.statement_id IS NOT NULL AND OLD.statement_id IS NOT NEW.statement_id AND statement_id = OLD.statement_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_invoices_ad_recompute_total
AFTER DELETE ON invoices
WHEN OLD.statement_id IS NOT NULL
BEGIN
    UPDATE order_statements
    SET total_amount = (SELECT COALESCE(SUM(amount), 0.0) FROM invoices WHERE statement_id = OLD.statement_id)
    WHERE statement_id = OLD.statement_id;
END;-- Payment calendar "greyed out" dates (2026-09-14): both Philippine holidays and any
-- custom date the user marks as a no-issue day render the same way on the calendar,
-- distinguished only by `kind` for the label shown.
--
-- Holiday dates below are computed, not guessed: fixed civil dates as-is; Maundy
-- Thursday/Good Friday via the standard Meeus/Jones/Butcher Easter algorithm; National
-- Heroes Day as the actual last Monday of August for each year. Deliberately excluded:
-- Eid'l Fitr and Eid'l Adha, which are set by lunar sighting and annual presidential
-- proclamation with no reliable formula -- add those by hand once announced, same as
-- any other custom date.
CREATE TABLE IF NOT EXISTS calendar_blocked_dates (
    blocked_date DATE PRIMARY KEY,
    label TEXT,
    kind TEXT NOT NULL DEFAULT 'custom' CHECK(kind IN ('holiday', 'custom')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO calendar_blocked_dates (blocked_date, label, kind) VALUES
    ('2026-01-01', 'New Year''s Day', 'holiday'),
    ('2026-04-02', 'Maundy Thursday', 'holiday'),
    ('2026-04-03', 'Good Friday', 'holiday'),
    ('2026-04-09', 'Araw ng Kagitingan', 'holiday'),
    ('2026-05-01', 'Labor Day', 'holiday'),
    ('2026-06-12', 'Independence Day', 'holiday'),
    ('2026-08-21', 'Ninoy Aquino Day', 'holiday'),
    ('2026-08-31', 'National Heroes Day', 'holiday'),
    ('2026-11-30', 'Bonifacio Day', 'holiday'),
    ('2026-12-25', 'Christmas Day', 'holiday'),
    ('2026-12-30', 'Rizal Day', 'holiday'),
    ('2027-01-01', 'New Year''s Day', 'holiday'),
    ('2027-03-25', 'Maundy Thursday', 'holiday'),
    ('2027-03-26', 'Good Friday', 'holiday'),
    ('2027-04-09', 'Araw ng Kagitingan', 'holiday'),
    ('2027-05-01', 'Labor Day', 'holiday'),
    ('2027-06-12', 'Independence Day', 'holiday'),
    ('2027-08-21', 'Ninoy Aquino Day', 'holiday'),
    ('2027-08-30', 'National Heroes Day', 'holiday'),
    ('2027-11-30', 'Bonifacio Day', 'holiday'),
    ('2027-12-25', 'Christmas Day', 'holiday'),
    ('2027-12-30', 'Rizal Day', 'holiday');

-- Per-invoice check allocation (2026-09-14, migration 025): which specific invoice(s) a check
-- pays, and how much of each -- many-to-many (one check can pay several invoices; one invoice
-- can be paid across several checks), mandatory for every check issued going forward. Going-
-- forward only -- no backfill for checks issued before this table existed.
CREATE TABLE IF NOT EXISTS invoice_check_allocations (
    allocation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,
    check_id INTEGER NOT NULL,
    amount REAL NOT NULL CHECK(amount > 0),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id),
    FOREIGN KEY (check_id) REFERENCES checks_issued(check_id)
);

-- Plain key/value store for system-wide configuration (migration 029, Phase 8a: Master Data
-- Setup) -- e.g. the primary bank's display name, the standard terms picklist offered when
-- adding a creditor. Small number of seeded rows, user-overridable from Admin & Audit >
-- Settings.
CREATE TABLE IF NOT EXISTS settings (
    setting_id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    value TEXT,
    category TEXT,
    description TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_by TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Internal bookkeeping control accounts from the old Access payee list (migration 030, Phase 8a:
-- Master Data Setup) -- Error Checks, Cash, Encashment, Adjustment, Cancelled. Not real payees
-- (creditors.creditor_type's CHECK only recognizes SUPPLIER/LENDER/BANK_LOAN/CREDIT_CARD), so
-- kept in their own table rather than polluting the creditors picker; legacy_payee_id keeps the
-- link to the old Access PayeeId.
CREATE TABLE IF NOT EXISTS creditor_master_internal (
    internal_payee_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    type TEXT,
    legacy_payee_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Findings the user reviewed on Reports > Possible duplicates and marked "not a duplicate"
-- (migration 042, 2026-09-21). dup_key identifies the finding, e.g. inv:<supplier id>:<normalized number>.
CREATE TABLE IF NOT EXISTS duplicate_dismissals (
    dismissal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dup_key TEXT NOT NULL UNIQUE,
    note TEXT,
    dismissed_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Credit card payments actually made (migration 043, 2026-09-21): when and how much was paid on a statement.
CREATE TABLE IF NOT EXISTS cc_payments (
    payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    cc_statement_id INTEGER NOT NULL,
    card_id INTEGER NOT NULL,
    payment_date DATE NOT NULL,
    amount REAL NOT NULL CHECK(amount > 0),
    method TEXT,
    note TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (cc_statement_id) REFERENCES cc_statements(cc_statement_id),
    FOREIGN KEY (card_id) REFERENCES credit_cards(card_id)
);

-- Checks typed into an order statement's "Issue a check" grid but not parked yet (2026-09-27): remembered so they are never
-- lost when you open another order statement or refresh the page. One row per batch; removed when the checks are parked.
CREATE TABLE IF NOT EXISTS park_drafts (
    statement_id INTEGER PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (statement_id) REFERENCES order_statements(statement_id)
);

-- NO DUPLICATE CHECK NUMBERS -- a hard rule enforced by the database itself (2026-09-27), not just by the screens. A physical leaf number
-- may exist once: as ONE check (any status) or as ONE spoiled-check log entry, never both, never twice -- however it is typed
-- (2554301 and 3992554301 are the same leaf). Any code path, script or import that tries is refused with 'Duplicate check number'.
CREATE UNIQUE INDEX IF NOT EXISTS idx_spoiled_checks_unique_number ON spoiled_checks(bank_account_id, check_number);

CREATE TRIGGER IF NOT EXISTS trg_spoiled_checks_no_duplicate_bi
BEFORE INSERT ON spoiled_checks
BEGIN
    SELECT RAISE(ABORT, 'Duplicate check number: already logged as spoiled')
    WHERE EXISTS (SELECT 1 FROM spoiled_checks WHERE bank_account_id = NEW.bank_account_id AND LTRIM(CASE WHEN LENGTH(check_number) = 10 AND SUBSTR(check_number, 1, 3) = '399' THEN SUBSTR(check_number, 4) ELSE check_number END, '0') = LTRIM(CASE WHEN LENGTH(NEW.check_number) = 10 AND SUBSTR(NEW.check_number, 1, 3) = '399' THEN SUBSTR(NEW.check_number, 4) ELSE NEW.check_number END, '0'));
    SELECT RAISE(ABORT, 'Duplicate check number: already the number of a check on file')
    WHERE EXISTS (SELECT 1 FROM checks_issued WHERE bank_account_id = NEW.bank_account_id AND check_number IS NOT NULL AND LTRIM(CASE WHEN LENGTH(check_number) = 10 AND SUBSTR(check_number, 1, 3) = '399' THEN SUBSTR(check_number, 4) ELSE check_number END, '0') = LTRIM(CASE WHEN LENGTH(NEW.check_number) = 10 AND SUBSTR(NEW.check_number, 1, 3) = '399' THEN SUBSTR(NEW.check_number, 4) ELSE NEW.check_number END, '0'));
END;

CREATE TRIGGER IF NOT EXISTS trg_checks_issued_no_duplicate_bi
BEFORE INSERT ON checks_issued
WHEN NEW.check_number IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Duplicate check number: already the number of another check')
    WHERE EXISTS (SELECT 1 FROM checks_issued WHERE bank_account_id = NEW.bank_account_id AND check_number IS NOT NULL AND LTRIM(CASE WHEN LENGTH(check_number) = 10 AND SUBSTR(check_number, 1, 3) = '399' THEN SUBSTR(check_number, 4) ELSE check_number END, '0') = LTRIM(CASE WHEN LENGTH(NEW.check_number) = 10 AND SUBSTR(NEW.check_number, 1, 3) = '399' THEN SUBSTR(NEW.check_number, 4) ELSE NEW.check_number END, '0'));
    SELECT RAISE(ABORT, 'Duplicate check number: already logged as spoiled')
    WHERE EXISTS (SELECT 1 FROM spoiled_checks WHERE bank_account_id = NEW.bank_account_id AND LTRIM(CASE WHEN LENGTH(check_number) = 10 AND SUBSTR(check_number, 1, 3) = '399' THEN SUBSTR(check_number, 4) ELSE check_number END, '0') = LTRIM(CASE WHEN LENGTH(NEW.check_number) = 10 AND SUBSTR(NEW.check_number, 1, 3) = '399' THEN SUBSTR(NEW.check_number, 4) ELSE NEW.check_number END, '0'));
END;

CREATE TRIGGER IF NOT EXISTS trg_checks_issued_no_duplicate_bu
BEFORE UPDATE OF check_number ON checks_issued
WHEN NEW.check_number IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Duplicate check number: already the number of another check')
    WHERE EXISTS (SELECT 1 FROM checks_issued WHERE check_id != NEW.check_id AND bank_account_id = NEW.bank_account_id AND check_number IS NOT NULL AND LTRIM(CASE WHEN LENGTH(check_number) = 10 AND SUBSTR(check_number, 1, 3) = '399' THEN SUBSTR(check_number, 4) ELSE check_number END, '0') = LTRIM(CASE WHEN LENGTH(NEW.check_number) = 10 AND SUBSTR(NEW.check_number, 1, 3) = '399' THEN SUBSTR(NEW.check_number, 4) ELSE NEW.check_number END, '0'));
    SELECT RAISE(ABORT, 'Duplicate check number: already logged as spoiled')
    WHERE EXISTS (SELECT 1 FROM spoiled_checks WHERE bank_account_id = NEW.bank_account_id AND LTRIM(CASE WHEN LENGTH(check_number) = 10 AND SUBSTR(check_number, 1, 3) = '399' THEN SUBSTR(check_number, 4) ELSE check_number END, '0') = LTRIM(CASE WHEN LENGTH(NEW.check_number) = 10 AND SUBSTR(NEW.check_number, 1, 3) = '399' THEN SUBSTR(NEW.check_number, 4) ELSE NEW.check_number END, '0'));
END;

-- THERMAL STATEMENT PRINTING (migration 048, 2026-09-27): statement versions + their public A4 copy on Drive, and the print log.
-- One row per version of a batch's statement. The A4 PDF of that version is uploaded to Drive once (never overwritten); its link is what
-- the slip's QR encodes. drive_public = 1 while anyone with the link can open it; a newer printed version switches the old one's sharing off
-- (drive_public = 0, revoked_at set) -- the file itself is kept, privately, for the audit history.
CREATE TABLE IF NOT EXISTS statement_versions (
    version_id INTEGER PRIMARY KEY AUTOINCREMENT,
    statement_id INTEGER NOT NULL,
    version_no INTEGER NOT NULL,
    content_hash TEXT,
    pdf_file_name TEXT,
    drive_file_id TEXT,
    drive_link TEXT,
    drive_public INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    uploaded_at DATETIME,
    revoked_at DATETIME,
    -- link expiry (migration 049): last computed "public until" date, and the date a reprint re-opened an expired link until
    public_until DATE,
    reopened_until DATE,
    UNIQUE (statement_id, version_no),
    FOREIGN KEY (statement_id) REFERENCES order_statements(statement_id)
);

-- One row per thermal copy sent to a printer. SUCCESS only once the whole job (including the cut) was sent; a FAILED attempt does not
-- count, so the retry is still the ORIGINAL.
CREATE TABLE IF NOT EXISTS statement_prints (
    print_id INTEGER PRIMARY KEY AUTOINCREMENT,
    statement_id INTEGER NOT NULL,
    version_id INTEGER,
    copy_type TEXT NOT NULL CHECK (copy_type IN ('FILE', 'SUPPLIER')),
    print_type TEXT NOT NULL CHECK (print_type IN ('ORIGINAL', 'REPRINT', 'REVISED')),
    backend TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('SUCCESS', 'FAILED')),
    error TEXT,
    drive_file_id TEXT,
    printed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (statement_id) REFERENCES order_statements(statement_id),
    FOREIGN KEY (version_id) REFERENCES statement_versions(version_id)
);
CREATE INDEX IF NOT EXISTS idx_statement_prints_statement ON statement_prints(statement_id, printed_at);

-- BANK STATEMENT IMPORT (migration 050, 2026-09-27): imported MBOS statements and their lines.
-- One row per imported file. file_hash is unique: the same file is never imported twice.
CREATE TABLE IF NOT EXISTS bank_statement_imports (
    import_id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_account_id INTEGER NOT NULL DEFAULT 1,
    file_name TEXT NOT NULL,
    file_hash TEXT NOT NULL UNIQUE,
    stored_path TEXT,
    account_last4 TEXT,
    period_from DATE,
    period_to DATE,
    opening_balance REAL,
    closing_balance REAL,
    line_count INTEGER,
    new_lines INTEGER,
    balance_ok INTEGER NOT NULL DEFAULT 1,
    imported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    reconciled_at DATETIME,
    FOREIGN KEY (bank_account_id) REFERENCES bank_accounts(bank_account_id)
);

-- One row per bank line. line_key is unique, so overlapping statement periods never double up.
-- kind: CHECK (ours clearing) / DEPOSIT / RETURNED_CHECK / BILLS_PAYMENT / DEBIT_MEMO / OTHER.
-- match_status (CHECK lines): matched / amount_mismatch / cancelled_check_cleared / not_in_system; returned_check for RETURNED_CHECK.
CREATE TABLE IF NOT EXISTS bank_transactions (
    txn_id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id INTEGER NOT NULL,
    bank_account_id INTEGER NOT NULL DEFAULT 1,
    txn_date DATE NOT NULL,
    posting_date DATE NOT NULL,
    txn_time TEXT,
    check_number TEXT,
    description TEXT,
    debit REAL NOT NULL DEFAULT 0,
    credit REAL NOT NULL DEFAULT 0,
    balance REAL,
    channel TEXT,
    sequence_no TEXT,
    kind TEXT NOT NULL DEFAULT 'OTHER',
    matched_check_id INTEGER,
    match_status TEXT,
    reviewed INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    line_key TEXT NOT NULL UNIQUE,
    -- reconciliation v2 (migration 051): how it was resolved, non-check matches, rejected suggestions
    resolution TEXT,
    matched_installment_id INTEGER,
    matched_cc_statement_id INTEGER,
    rejected_check_ids TEXT,
    FOREIGN KEY (import_id) REFERENCES bank_statement_imports(import_id),
    FOREIGN KEY (matched_check_id) REFERENCES checks_issued(check_id)
);
CREATE INDEX IF NOT EXISTS idx_bank_transactions_date ON bank_transactions(posting_date);
CREATE INDEX IF NOT EXISTS idx_bank_transactions_check ON bank_transactions(check_number);

-- SAVED REPORTS (migration 051, 2026-09-28)
-- "My reports": saved report criteria (a JSON spec checked against engines/report_builder.py's fixed list of datasets and fields -- never
-- free SQL), optionally pinned to the Dashboard and / or included in the overall report.
CREATE TABLE IF NOT EXISTS saved_reports (
    report_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    spec_json TEXT NOT NULL,
    pin_dashboard INTEGER NOT NULL DEFAULT 0,
    include_in_overview INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_run_at DATETIME
);

-- Bank adjustments (migration 053, 2026-09-28, owner: "Bank statement is the ultimate truth"; "not direct edit the record but tag it
-- as adjustment due to incorrect recording in the book"). The check stays exactly as recorded; each difference the bank shows is a
-- separate, reversible entry: CHECK_AMOUNT (signed: bank amount - recorded amount), CHECK_DATE (recorded date vs the bank's date) and
-- BATCH_BALANCE (signed, the matching change to what the batch owes, "bank clearing difference"). Reversed = reversed_at set, kept.
CREATE TABLE IF NOT EXISTS bank_adjustments (
    adjustment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('CHECK_AMOUNT', 'CHECK_DATE', 'BATCH_BALANCE')),
    check_id INTEGER,
    bank_txn_id INTEGER,
    statement_id INTEGER,
    loan_note_id INTEGER,
    recorded_value TEXT,
    bank_value TEXT,
    amount REAL NOT NULL DEFAULT 0,
    reason TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    reversed_at DATETIME,
    FOREIGN KEY (check_id) REFERENCES checks_issued(check_id),
    FOREIGN KEY (bank_txn_id) REFERENCES bank_transactions(txn_id),
    FOREIGN KEY (statement_id) REFERENCES order_statements(statement_id)
);

-- Document numbers (migration 055, Phase F1.4, 2026-09-28): the last permanent number handed out per series and year ('OS' = order
-- statement batches "YYYY - OS NNNN", 'LN' = loan notes "YYYY - LN NNNN"), taken inside the same locked step that promotes the draft,
-- so two people promoting at the same moment never get the same number and a number is never reused.
CREATE TABLE IF NOT EXISTS doc_counters (
    series TEXT NOT NULL,
    year INTEGER NOT NULL,
    last_no INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (series, year)
);

-- PURCHASING (migration 061, 2026-10-01; see the plan "Cash disbursement cycle: order to pay"). Supplier price lists, purchase orders and what was
-- received, so an order shows in the cash forecast on its expected payment date BEFORE the supplier's invoice exists. The price list gives the ESTIMATE
-- for a PO line; when the goods and the supplier's invoice arrive, the ACTUAL price prevails (purchase_order_lines.actual_*, po_receipts.actual_amount).
CREATE TABLE IF NOT EXISTS supplier_price_list (
    price_id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor_id INTEGER NOT NULL,
    item_code TEXT,
    item_name TEXT NOT NULL,
    unit TEXT,
    unit_price REAL NOT NULL CHECK(unit_price >= 0),
    effective_date DATE NOT NULL,                                   -- the price applies from this date until a later row for the same item
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (creditor_id, item_name, effective_date),
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id)
);
CREATE INDEX IF NOT EXISTS idx_price_list_lookup ON supplier_price_list (creditor_id, item_name, effective_date);

CREATE TABLE IF NOT EXISTS purchase_orders (
    po_id INTEGER PRIMARY KEY AUTOINCREMENT,
    po_no TEXT NOT NULL UNIQUE,                                     -- DRAFT-PO-... until first ordered, then "YYYY - PO NNNN" (doc_counters series 'PO')
    creditor_id INTEGER NOT NULL,
    order_date DATE NOT NULL,
    expected_delivery_date DATE,
    terms_snapshot TEXT,                                            -- the supplier's terms when the order was made
    expected_pay_date DATE,                                         -- delivery date + terms, moved to a bank day: when the check will be due
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT', 'ORDERED', 'PARTIAL', 'RECEIVED', 'CANCELLED')),
    notes TEXT,
    created_by TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    over_cash_plan INTEGER NOT NULL DEFAULT 0 CHECK(over_cash_plan IN (0, 1)),   -- set when the order was placed (or changed) and the cash on its pay date did not cover it
    cash_note TEXT,                                                              -- plain words for the owner: how far short, and when
    cash_ack_at DATETIME,                                                        -- the owner has seen it
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id)
);
CREATE INDEX IF NOT EXISTS idx_po_creditor_status ON purchase_orders (creditor_id, status);

CREATE TABLE IF NOT EXISTS purchase_order_lines (
    line_id INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id INTEGER NOT NULL,
    price_id INTEGER,                                               -- the price-list row the estimate came from (none for a typed price)
    item_name TEXT NOT NULL,
    unit TEXT,
    qty REAL NOT NULL CHECK(qty > 0),
    est_unit_price REAL NOT NULL DEFAULT 0 CHECK(est_unit_price >= 0),
    est_amount REAL NOT NULL DEFAULT 0 CHECK(est_amount >= 0),
    qty_received REAL NOT NULL DEFAULT 0 CHECK(qty_received >= 0),
    actual_unit_price REAL CHECK(actual_unit_price IS NULL OR actual_unit_price >= 0),
    actual_amount REAL CHECK(actual_amount IS NULL OR actual_amount >= 0),
    FOREIGN KEY (po_id) REFERENCES purchase_orders(po_id),
    FOREIGN KEY (price_id) REFERENCES supplier_price_list(price_id)
);
CREATE INDEX IF NOT EXISTS idx_po_lines_po ON purchase_order_lines (po_id);

CREATE TABLE IF NOT EXISTS po_receipts (
    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id INTEGER NOT NULL,
    received_date DATE NOT NULL,
    supplier_invoice_no TEXT,
    supplier_invoice_date DATE,
    actual_amount REAL NOT NULL CHECK(actual_amount >= 0),
    est_amount REAL NOT NULL DEFAULT 0,                             -- what the order estimated for the part received (the difference = what the actual price changed)
    invoice_id INTEGER,                                             -- the AP invoice this receipt created or matched
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (po_id) REFERENCES purchase_orders(po_id),
    FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id)
);
CREATE INDEX IF NOT EXISTS idx_po_receipts_po ON po_receipts (po_id);

-- SIGN-IN (migration 066, 2026-10-02; B8 phase 1). The people who may use the AP app. The password is stored SCRAMBLED (scrypt + its own salt), never as typed.
-- role: 'owner' (Payables: everything) or 'purchasing' (purchase orders and price lists). A person is switched off, never deleted. Phase 2 (hosting) adds Google 2-step
-- sign-in on this same list, keyed by email.
CREATE TABLE IF NOT EXISTS app_users (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 1 CHECK(must_change_password IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until DATETIME,
    last_login_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
