-- Migration 019: check-print calibration settings
--
-- User provided a real Chrysanth Check Writer sample print + its Options dialog (Printer/
-- Alignment/Font/Style/Locale tabs) and asked to build our own direct-print check engine
-- matching that style, rather than exporting data for Chrysanth to print. The one real blocker
-- that remains is that no physical Metrobank check leaf has ever been measured -- rather than
-- guess exact millimeter positions and get them wrong, these 4 settings make the canvas size and
-- print offset tunable after a real test print, mirroring Chrysanth's own per-account Alignment
-- tab ("Shift Right"/"Shift Down", "finetune the adjustment for particular cheques only").
--
-- Defaults: 203.2mm x 76.2mm (= the old check_engine.py draft's guessed 8in x 3in canvas, kept
-- as the starting point since nothing better is known yet) with zero shift.

ALTER TABLE bank_accounts ADD COLUMN check_width_mm REAL NOT NULL DEFAULT 203.2;
ALTER TABLE bank_accounts ADD COLUMN check_height_mm REAL NOT NULL DEFAULT 76.2;
ALTER TABLE bank_accounts ADD COLUMN check_offset_x_mm REAL NOT NULL DEFAULT 0;
ALTER TABLE bank_accounts ADD COLUMN check_offset_y_mm REAL NOT NULL DEFAULT 0;
