-- DARK MODE (owner, 2026-10-13: "our app should have a night mode or dark mode"): each person's own choice, kept on their account so it
-- follows them to every device. NULL = light, as before.
ALTER TABLE app_users ADD COLUMN theme TEXT;
