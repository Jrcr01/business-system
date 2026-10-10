-- CHECK BOOKLET MANAGEMENT (owner, 2026-10-09: "assigned checkbooklet for example me or to someone"): who holds each booklet -- a person with an account
-- (holder_user_id) or a typed name for someone without one, e.g. "Mama" (holder_name) -- and since when. The booklet's code (BK-0001 ...) is generated
-- by the system (engines/booklet_rules.py), never typed.
ALTER TABLE check_booklets ADD COLUMN holder_user_id INTEGER;
ALTER TABLE check_booklets ADD COLUMN holder_name TEXT;
ALTER TABLE check_booklets ADD COLUMN held_since DATE;
