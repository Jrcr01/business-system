-- Settings > Bank accounts (owner, 2026-10-20; mock-up bank_setup.html): each account's nature, its check-number rule, which statement
-- file it imports and what it is used for -- instead of "Metrobank, branch 399, account 1" written into the code. The existing account
-- (id 1, Slick's Metrobank) gets the values the code assumed, so nothing changes for Slick's.
ALTER TABLE bank_accounts ADD COLUMN shown_as TEXT;                                            -- the short label on screens and printouts
ALTER TABLE bank_accounts ADD COLUMN nature TEXT NOT NULL DEFAULT 'operating';                 -- operating / settlement / payroll / savings / loan / ewallet / other
ALTER TABLE bank_accounts ADD COLUMN check_prefix TEXT NOT NULL DEFAULT '';                    -- printed on every leaf before the running number (Metrobank: 399)
ALTER TABLE bank_accounts ADD COLUMN check_digits INTEGER NOT NULL DEFAULT 7;                  -- digits of the running number after the prefix
ALTER TABLE bank_accounts ADD COLUMN statement_number_form TEXT NOT NULL DEFAULT 'either';     -- how the bank's statement writes a check number: full / leaf / either
ALTER TABLE bank_accounts ADD COLUMN statement_reader TEXT NOT NULL DEFAULT 'generic';         -- mbos (Metrobank's file) / generic (CSV or Excel columns)
ALTER TABLE bank_accounts ADD COLUMN writes_checks INTEGER NOT NULL DEFAULT 0 CHECK(writes_checks IN (0, 1));   -- the one account the booklets belong to
ALTER TABLE bank_accounts ADD COLUMN in_forecast INTEGER NOT NULL DEFAULT 1 CHECK(in_forecast IN (0, 1));
ALTER TABLE bank_accounts ADD COLUMN cards_paid_from INTEGER NOT NULL DEFAULT 0 CHECK(cards_paid_from IN (0, 1));
ALTER TABLE bank_accounts ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1));
ALTER TABLE bank_accounts ADD COLUMN closed_at DATETIME;
UPDATE bank_accounts SET shown_as = COALESCE(shown_as, 'Metrobank Account 1'), check_prefix = COALESCE(NULLIF(check_prefix, ''), branch_code, '399'),
       statement_reader = 'mbos', writes_checks = 1, cards_paid_from = 1 WHERE bank_account_id = 1;
