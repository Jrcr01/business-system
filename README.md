# AP Disbursement System — Slick's Tire Supply

The accounts-payable system: supplier order statements (batches) and invoices, check issuing and numbering, Chrysanth batch printing, private-lender loan notes, bank loans and credit card statements, with dashboards, reports and PDFs. Built with Python + Streamlit on a SQLite database. Single user, runs on one Windows laptop.

## Everyday use (double-click these in this folder)

| File | What it does |
|---|---|
| `start_live.bat` | Starts the app and opens it in the browser at **http://localhost:8501** |
| `restart_live.bat` | Restarts it (needed after a program update) |
| `stop_live.bat` | Turns it off — the data is always saved already |
| `update_database.bat` | After a program update that needs it (the app says so on start): backs up, updates the database, restarts |
| `run_backup.bat` | Backs up now. Also runs automatically every day at 6 PM (Windows Task Scheduler, "AP System Daily Backup") |
| `restore_test.bat` | **Once a month:** proves the newest backup can rebuild the system. Must end in **PASSED** |
| `run_test_streamlit.bat` | A separate test copy of the app on port 8502 with its own test database — for trying things safely |

Only this laptop can open the app (it listens on 127.0.0.1 only). There is no login.

## Where things are

| What | Where |
|---|---|
| **Live database** | `C:\AP_Data\ap_disbursement.db` (its path is in `live_db_path.txt`) — on the laptop, deliberately **not** on Google Drive |
| Daily database backups (newest 30) | `H:\My Drive\Claude9OS\AP_Backups` (Google Drive, off the laptop) |
| Daily copy of the program's history (newest 30) | `C:\AP_Data\code_backups` (a git bundle; restore with `git clone <file>.bundle Project_Checke`) |
| The program | this folder (on Google Drive) |
| What changed and when | `WHATS_NEW.md` — the app also shows the newest entry once per session |

## If something goes wrong

- **The app says a database update is needed** → close it, run `update_database.bat`.
- **The laptop or the database is lost** → install Python 3.12 and the packages (`python -m pip install -r requirements-lock.txt`), copy the newest file from `AP_Backups` to `C:\AP_Data\ap_disbursement.db`, run `update_database.bat`, then `start_live.bat`. `restore_test.bat` rehearses exactly this.
- **The program folder's history is damaged** → `git clone C:\AP_Data\code_backups\<newest>.bundle` gives it back.

## For developers

- `app.py` — the screens (Streamlit). Business rules are in `engines/`: `common.py`, `statement_rules.py`, `check_rules.py`, `creditor_rules.py`, `loan_note_rules.py`, `card_rules.py`, plus the older `*_service.py` modules (reports, PDFs, imports, Chrysanth export, search…). `app.py` imports the rules back, so `app.<name>` works everywhere.
- `schema.sql` is the full, current database design; `migrations/` is the numbered change log (001–047). `apply_pending_migrations.py` (run by `update_database.bat`) brings any database up to `schema.sql` — it only ever adds tables, columns, views, indexes and triggers.
- Hard rules the **database itself** enforces: no duplicate check number (a number exists once — one check or one spoiled entry, 7- and 10-digit forms are the same leaf); the order-statement total always equals its invoices.
- Tests: `python -m pytest` (about 270 tests, ~1 minute). Every test gets its own fresh database built from `schema.sql`; the tests never touch real data (`tests/conftest.py` repoints every module automatically).
- Package versions are pinned (`requirements.txt`, full list in `requirements-lock.txt`). Before upgrading Streamlit, move the three `components.v1.html` uses (print preview, keyboard shortcuts, leave-page prompt) to the newer API.
- Machine-specific settings (the Python path) are in `settings.bat`; put overrides for another PC in `local_settings.bat` (not in git).
- Never commit real data: `.gitignore` excludes every database, export, workbook and printout. The only workbook in git is `excel_invoice_entry/invoice_entry_master.xlsm`, which the app needs.
- `archive/one_time_scripts/` — migration and clean-up scripts that already ran; kept as a record, not to be re-run.
- `CLAUDE.md` is the detailed specification and rules; `MEMORY.md` is the log of decisions and why they were made.
