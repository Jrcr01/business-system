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
