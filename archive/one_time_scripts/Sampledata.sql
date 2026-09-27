-- Sampledata.sql — Comprehensive demo seed data across all 4 AP settlement tracks.
-- Reflects the current schema (post Phase 1 rebuild): terms on creditors, batch
-- lock state, credit memos, check corrections/replacements, spoiled checks,
-- invoice corrections. Run against a freshly-cleared database (see reseed.py).

PRAGMA foreign_keys = ON;

-- ----------------------------------------------------------------------
-- 1. PRIMARY BANK ACCOUNT & CHECKBOOK INVENTORY (Metrobank Branch 399)
-- ----------------------------------------------------------------------
INSERT OR REPLACE INTO bank_accounts (bank_account_id, bank_name, account_name, account_number, branch_code, current_balance)
VALUES (1, 'Metrobank', 'Metrobank Account 1', 'MBTC-399-PRIMARY', '399', 3545000.00);

INSERT INTO checkbooks (bank_account_id, booklet_code, start_check_number, end_check_number, date_received, status)
VALUES (1, 'MBTC-399-BOX-01', 12501, 12550, '2026-08-01', 'Active');


-- ----------------------------------------------------------------------
-- 2. CREDITOR MASTER (Supplier, Lender, Bank Loan, Credit Card)
-- ----------------------------------------------------------------------
-- Track 1: Trade Suppliers (with agreed payment terms)
INSERT INTO creditors (creditor_id, creditor_name, creditor_type, tin, contact_person, contact_number, email, terms)
VALUES
(101, 'Slick Auto Parts Inc.', 'SUPPLIER', '004-123-456-000', 'Roberto Cruz', '+63 917 111 2233', 'billing@slickautoparts.ph', '30'),
(102, 'Manila Tire & Rubber Corp.', 'SUPPLIER', '008-987-654-000', 'Elena Santos', '+63 918 444 5566', 'ap@manilatire.com', '60');

-- Track 2: Private Lenders
INSERT INTO creditors (creditor_id, creditor_name, creditor_type, tin, contact_person, contact_number, email)
VALUES
(201, 'Vanguard Capital Holdings', 'LENDER', '101-555-888-000', 'Atty. Victor Reyes', '+63 920 888 9900', 'reyes@vanguardcap.ph');

-- Track 3: Bank Loans
INSERT INTO creditors (creditor_id, creditor_name, creditor_type, tin, contact_person, contact_number, email)
VALUES
(301, 'BDO Unibank Commercial Loans', 'BANK_LOAN', '000-345-678-000', 'Maria Gomez (Account Officer)', '+63 2 8840 7000', 'corporate@bdo.com.ph');

-- Track 4: Credit Cards
INSERT INTO creditors (creditor_id, creditor_name, creditor_type, tin, contact_person, contact_number, email)
VALUES
(401, 'Metrobank Card Corporation', 'CREDIT_CARD', '000-123-789-000', 'Customer Support', '+63 2 8870 0700', 'cards@metrobankcard.com'),
(402, 'BDO Credit Card Services', 'CREDIT_CARD', '000-345-678-001', 'Merchant Services', '+63 2 8631 8000', 'cards@bdo.com.ph');


-- ----------------------------------------------------------------------
-- TRACK 1: SUPPLIERS — ORDER STATEMENTS / INVOICES / CREDIT MEMOS / CHECKS
-- Four batches, each demonstrating a different lifecycle state.
-- ----------------------------------------------------------------------

-- OS 0001 — Slick Auto Parts Inc. — CLOSED: balanced, locked, with both a
-- check correction AND a check replacement in its history.
INSERT INTO order_statements (statement_id, creditor_id, statement_ref, total_amount, status, is_locked)
VALUES (1, 101, '2026 - OS 0001', 350000.00, 'Issued', 1);

INSERT INTO invoices (invoice_id, creditor_id, doc_ref, invoice_date, due_date, gross_amount, trade_discounts, amount, status, statement_id)
VALUES
(1, 101, 'INV-2026-8801', '2026-08-10', '2026-09-10', 200000.00, '[5.0, 5.0]', 180500.00, 'Paid', 1),
(2, 101, 'INV-2026-8802', '2026-08-15', '2026-09-15', 178421.05, '[5.0]', 169500.00, 'Paid', 1);

INSERT INTO credit_memos (credit_memo_id, statement_id, memo_ref, memo_date, amount, reason, created_at)
VALUES (1, 1, 'CM-2026-001', '2026-08-28', 15000.00, 'Damaged goods returned on delivery', '2026-08-28 09:15:00');
-- net total = 350000 - 15000 = 335000, fully covered by the active check below.

INSERT INTO checks_issued (check_id, creditor_id, bank_account_id, statement_id, check_number, check_date, amount, status, replacement_check_id, replacement_reason, replaced_at, created_at)
VALUES
(1, 101, 1, 1, '3990012501', '2026-09-10', 330000.00, 'Replaced', 6, 'Postdated beyond revised terms; supplier requested reissue with a later date', '2026-09-12 10:15:00', '2026-09-01 09:00:00'),
(6, 101, 1, 1, '3990012506', '2026-09-20', 335000.00, 'Active', NULL, NULL, NULL, '2026-09-12 10:15:00');

INSERT INTO check_corrections (correction_id, check_id, statement_id, correction_type, old_value, new_value, reason, corrected_at)
VALUES (1, 1, 1, 'Amount', '330000.0', '335000.0', 'Corrected to match invoice total after credit memo', '2026-09-11 09:00:00');


-- OS 0002 — Manila Tire & Rubber Corp. — IN PROGRESS: unlocked, partially
-- paid via one check, remaining balance still open (multi-check in progress).
INSERT INTO order_statements (statement_id, creditor_id, statement_ref, total_amount, status, is_locked)
VALUES (2, 102, '2026 - OS 0002', 125000.00, 'Issued', 0);

INSERT INTO invoices (invoice_id, creditor_id, doc_ref, invoice_date, due_date, gross_amount, trade_discounts, amount, status, statement_id)
VALUES (3, 102, 'MTR-99021', '2026-08-20', '2026-09-27', 125000.00, '[]', 125000.00, 'Partially Paid', 2);

INSERT INTO invoice_corrections (correction_id, invoice_id, statement_id, field_name, old_value, new_value, corrected_at)
VALUES (1, 3, 2, 'due_date', '2026-09-20', '2026-09-27', '2026-09-05 08:30:00');

INSERT INTO checks_issued (check_id, creditor_id, bank_account_id, statement_id, check_number, check_date, amount, status, created_at)
VALUES (2, 102, 1, 2, '3990012502', '2026-09-20', 60000.00, 'Active', '2026-09-06 11:00:00');
-- remaining balance = 125000 - 60000 = 65000, still payable.


-- OS 0003 — Manila Tire & Rubber Corp. — DRAFT with invoices, no checks yet
-- (ready to pay; good spot to try the in-place invoice edit or issue a check).
INSERT INTO order_statements (statement_id, creditor_id, statement_ref, total_amount, status, is_locked)
VALUES (3, 102, '2026 - OS 0003', 42000.00, 'Draft', 0);

INSERT INTO invoices (invoice_id, creditor_id, doc_ref, invoice_date, due_date, gross_amount, trade_discounts, amount, status, statement_id)
VALUES (4, 102, 'MTR-99022', '2026-08-25', '2026-10-24', 42000.00, '[]', 42000.00, 'Unpaid', 3);


-- OS 0004 — Slick Auto Parts Inc. — freshly created empty Draft (total ₱0,
-- no invoices yet) — matches exactly what "Create order statement" produces.
INSERT INTO order_statements (statement_id, creditor_id, statement_ref, total_amount, status, is_locked)
VALUES (4, 101, '2026 - OS 0004', 0.00, 'Draft', 0);


-- ----------------------------------------------------------------------
-- SPOILED CHECK — misprint, routed to the check-number-sequence control log,
-- not tied to any supplier/batch (separate from the history above).
-- ----------------------------------------------------------------------
INSERT INTO spoiled_checks (spoiled_id, bank_account_id, check_number, reason, logged_at)
VALUES (1, 1, '3990012505', 'Printer jam, misaligned print', '2026-09-08 14:20:00');


-- ----------------------------------------------------------------------
-- TRACK 2: PRIVATE LENDERS (Loan Notes & PDCs)
-- ----------------------------------------------------------------------
INSERT INTO loan_notes (loan_note_id, creditor_id, note_ref, issue_date, principal_amount, total_interest, total_payable, status)
VALUES (1, 201, 'PN-VCH-2026-01', '2026-08-01', 1000000.00, 60000.00, 1060000.00, 'Active');

INSERT INTO checks_issued (check_id, creditor_id, bank_account_id, loan_note_id, check_number, check_date, amount, status, created_at)
VALUES
(3, 201, 1, 1, '3990012503', '2026-09-01', 530000.00, 'Active', '2026-08-01 09:00:00'),
(4, 201, 1, 1, '3990012504', '2026-10-01', 530000.00, 'Active', '2026-08-01 09:00:00');


-- ----------------------------------------------------------------------
-- TRACK 3: BANK LOANS (Term Loans & ADA Amortization Schedule)
-- ----------------------------------------------------------------------
INSERT INTO loan_beneficiaries (beneficiary_id, name, beneficiary_type, contact_number)
VALUES (1, 'ABC Realty Affiliate Corp.', 'Affiliate', NULL);

INSERT INTO bank_loans (bank_loan_id, creditor_id, beneficiary_id, loan_ref, account_number, start_date, maturity_date, repricing_date, principal_amount, mode_of_payment, status)
VALUES (1, 301, 1, 'TL-BDO-2025-099', NULL, '2025-09-15', '2027-09-15', NULL, 5000000.00, 'ADA', 'Active');

INSERT INTO bank_loan_installments (installment_id, bank_loan_id, installment_no, due_date, amount)
VALUES
(1, 1, 12, '2026-09-15', 235000.00),
(2, 1, 13, '2026-10-15', 235000.00);


-- ----------------------------------------------------------------------
-- TRACK 4: CREDIT CARDS (Due Date Alerts, Cardholder Registry, Annual Fee Reminders)
-- ----------------------------------------------------------------------
INSERT INTO cardholders (cardholder_id, name, department, contact_number)
VALUES
(1, 'Jessie Roxas', 'Finance', '0917-000-0001'),
(2, 'Ramon Mendoza', 'Operations', '0917-000-0002');

INSERT INTO credit_cards (card_id, creditor_id, cardholder_id, card_name, card_number_last4, credit_limit, annual_fee_due_month, statement_day)
VALUES
(1, 401, 1, 'Metrobank Corporate World Mastercard', '4812', 1500000.00, 3, 15),
(2, 402, 2, 'BDO Executive Visa', '9055', 1000000.00, 6, 20);

INSERT INTO cc_statements (cc_statement_id, card_id, due_date, total_amount_due, status, payment_method)
VALUES
(1, 1, '2026-09-05', 145230.50, 'Unpaid', 'Cash / Online'),
(2, 2, '2026-09-12', 88400.00, 'Unpaid', 'Cash / Online');


-- ----------------------------------------------------------------------
-- AUDIT LOG INITIALIZATION
-- ----------------------------------------------------------------------
INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values)
VALUES ('SYSTEM_INIT', 'database', 1, 'Reseeded demo data across all 4 AP settlement tracks (post Phase 1 rebuild schema).');
