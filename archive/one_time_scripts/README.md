# One-time scripts (archived 2026-09-27)

These scripts each did one job on the live data and **have already been run**. They are kept only as a record of what was done and how — they are not part of the running system, and there is no reason to run them again. Several would do harm if re-run (for example, the Phase 8 scripts re-import data from the old Access system, including full card numbers).

| Script | What it did | When |
|---|---|---|
| `phase8a_master_data_setup.py` … `phase8e_backfill_recent_cc_statements.py`, `phase8b/8c/8d_promote_to_live.py` | Migrated suppliers, lenders, bank loans, cards and open checks from the old Access database | Sep 2026 |
| `move_database_out_of_drive.py`, `move_database.bat` | Moved the live database out of Google Drive to `C:\AP_Data` | 20 Sep 2026 |
| `prefix_existing_check_numbers.py`, `renumber_checks.bat` | Turned 7-digit check numbers into the full 10-digit form | 20 Sep 2026 |
| `apply_updates.bat`, `fix_amex_issuer.py`, `remove_inactive_cards.py` | First bundled update: card issuer fix, inactive cards removed | 19–20 Sep 2026 |
| `update_bank_loans.bat`, `delete_duplicate_bank_loan.py`, `rename_banks_full_names.py`, `restore_creditor_case.py`, `set_default_supplier_terms.py` | Bank-loan clean-up, full bank names, proper-case names, default 120-day terms | 21 Sep 2026 |
| `delete_empty_loan_notes.py`, `clean_empty_records.py` | Removed empty loan notes, lenders and bank loans | 21 Sep 2026 |
| `remove_duplicate_spoiled_checks.py` | Removed a duplicate spoiled-check entry | 27 Sep 2026 |
| `remove_full_card_numbers.py`, `clean_card_numbers_in_copies.py`, `remove_card_numbers.bat` | Removed full card numbers from the database and every copy | 27 Sep 2026 |
| `df_query().py`, `Sampledata.sql` | Early development scratch file and demo data | Sep 2026 |

`tests/` holds the tests that belonged to these scripts. They are not run with the main test suite.

If one ever had to be run again, run it from the project folder so it can find `db_location.py`, e.g. `set PYTHONPATH=.` then `python archive\one_time_scripts\<script>.py` — and preview first.
