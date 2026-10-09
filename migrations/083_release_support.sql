-- RELEASES WITH PEOPLE AT WORK (owner, 2026-10-09: "since my brothers are now using the system"): who is active before a deploy, and which
-- "What's new" note each person has seen.
ALTER TABLE app_users ADD COLUMN last_seen_at DATETIME;          -- the last page this person opened (written at most once a minute)
ALTER TABLE app_users ADD COLUMN last_seen_path TEXT;
ALTER TABLE app_users ADD COLUMN whats_new_seen TEXT;            -- the newest "What's new" note they pressed Got it on (web/core/whats_new.py)
