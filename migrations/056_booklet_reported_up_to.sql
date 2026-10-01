-- BOOKLET "USED UP TO" (2026-10-01). Applied by update_database.bat (schema.sql carries the column; this file is the record).

-- The owner's mother hand-writes urgent checks from her own booklet and sends photos; the owner records them. check_booklets.reported_up_to is the
-- last leaf the owner has seen a photo of, so every leaf up to it that is not recorded, spoiled, voided or on a bank line can be listed as "used but not recorded".
ALTER TABLE check_booklets ADD COLUMN reported_up_to INTEGER;
