-- AUDIT LOG PROTECTION (core step C2, 2026-09-28). Applied by update_database.bat (schema.sql adds the table and triggers);
-- this file is the record. After it, the daily backup seals the log (engines/audit_seal.py; the first run seals it as it is).

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
