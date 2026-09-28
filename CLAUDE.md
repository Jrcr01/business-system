# AP Disbursement System — Session Memory

## Table of Contents

- [Current State (2026-09-27)](#current-state-2026-09-27) — **read this first**: what is live now, where things are, rules added since 2026-09-17
- [Thermal Statement Printing](#thermal-statement-printing) — gated mini-phase (2026-09-27): spec, locked decisions, build order, acceptance criteria
- [Bank Statement Import and Cash Insights](#bank-statement-import-and-cash-insights) — MBOS statement import, automatic check matching, cash insights (2026-09-27), reconciliation v2 + alerts (2026-09-28)
- [Phase F — Foundation](#phase-f--foundation-cloud-server-logins-safe-for-a-few-users) — **next** (scoped 2026-09-28, not started): cloud server, logins, safe for a few users; before Sales/AR
- [Current Priority / Scope](#current-priority--scope) — Phased roadmap (Phases 1-6, then Phase F, then Sales/AR)
- [Update Protocol](#update-protocol) — How to maintain this file
- [Core System Configuration](#core-system-configuration) — Bank account, database, branding, core rules
- [Implementation Backlog](#implementation-backlog)
  - Phase 1: Supplier — Order Statement
  - Phase 2: Private Lender — Loan Notes
  - Phase 3: Bank Loan — Monthly Dues
  - Phase 4: Necessary Reports
  - Phase 5: Dashboard — KPI
  - Phase 6 Redux v2: Check Printing (Simplified)

---

## Current State (2026-09-27)

The system is **live** (cut over Sep 2026) for Slick's Tire Supply, used daily by one non-technical owner (JRCR). Everything below this section is the build history and detailed rules; where it conflicts with this section, **this section wins** (older lines are corrected in place where noted).

**How it runs**
* Live database: `C:\AP_Data\ap_disbursement.db` — pointed to by `live_db_path.txt`, resolved by `db_location.live_db_path()` (env `AP_DB_PATH` first). Not on Google Drive. **Never modify the live database directly from a session**: give the owner a script with a preview/`--apply` mode (usually wrapped in a `.bat` that stops the app, previews, waits for `YES`, backs up, applies, restarts), always tested on a copy first.
* App: `run_live_streamlit.bat` → `127.0.0.1:8501` only (not reachable from the network; there is no login). Test copy: `run_test_streamlit.bat` → port 8502 on `data/ap_disbursement_TEST.db`.
* Machine paths: `settings.bat` (Python path; `local_settings.bat` overrides, not in git). Folders are derived from `%~dp0` / `$PSScriptRoot` / `__file__`. `stop_live.ps1` is the one shared "stop the app" step.
* Backups: `backup_live_db.py` via `run_backup.bat quiet`, Windows task "AP System Daily Backup" at 18:00 → `db_location.backup_dir()` (the `AP_Backups` folder beside the project, on Google Drive, newest 30) **and** a git bundle of the code history to `C:\AP_Data\code_backups` (newest 30). Monthly `restore_test.bat` rebuilds the newest backup on a temp copy, applies the schema update, starts the app on it and must say PASSED.
* Schema: `schema.sql` is the truth; `migrations/` is the log (latest 047). `apply_pending_migrations.diff_and_sync()` (run by `update_database.bat`; the app checks at startup and stops with instructions if anything is missing) adds tables, columns, views, indexes **and triggers**; it never alters or drops.
* Versions pinned: `requirements.txt` (direct) and `requirements-lock.txt` (all 55). Streamlit 1.62, pandas 3, Python 3.12. `components.v1.html` (3 uses) must move to the new API before any Streamlit upgrade.

**Code layout**
* `app.py` = the router: page config, startup schema check, the **grouped sidebar menu** (`st.navigation`, 2026-09-27: Daily work = Payment run, Add data, Order statements, Checks and checkbook · Other payables = Lenders, Bank loans, Credit cards · Records and reports = Dashboard, Creditors, Analytics, Reports · System = Admin and audit, Data health), sidebar (quick search, Search everything, mini calendar, shortcuts, park-draft reminder), hotkeys script. Pages are `st.Page(views.<x>.render, url_path=<id>)` held in `views.shared.PAGES`; **only the open page runs** (a click used to rerun all 10 tabs, ~2-3 s -> ~0.25-0.6 s). Navigation by page id: `_jump(page_id, **state)` (st.switch_page, from the sidebar) and `go_to(page_id, **state)` (sets `_global_jump`, applied before any page draws -- use it from buttons inside pages and dialogs). Search results carry `{'page', 'state'}`. Alt+1...0 click the first ten menu links. The first page (Payment run) lives at `/`, so typing `/payment-run` gives "Page not found".
* Screens: `views/<page>.py`, each `render()`: payment_run (quick report + cycle stages; data from `engines/payment_run_service.py`, Today PDF `engines/today_report_pdf.py`), add_data, dashboard, analytics, creditor_master, order_statement, lenders, bank_loans, credit_cards, checks, reports, admin. **Entry forms are shared functions** called by their own page and by Add data -- `render_batch_invoices/_memos(statement_id)`, `render_add_cc_statement_form()`, `render_new_loan_note_form()`, `render_add_installment_form(loan_note_id)`, `render_new_bank_loan_form()`, `render_bank_loan_schedule(bank_loan_id)`, `render_new_booklet_form()`, `render_log_spoiled_form()`, `render_new_creditor_form()` -- change a form in one place. Shared screen helpers in `views/shared.py`: `q`, `refresh(done=msg)` (toast after the redraw), dialogs, pickers, `guard_duplicate_submit`, `batch_scoped_key`, the check-number/print sections, `global_search_dialog`, `_jump`/`go_to`, and the look: `page_header`, `section`, `status_badge`/`status_text` (Draft grey, Parked orange, Issued blue, Paid green, Cancelled red), `money_col`/`MONEY_FORMAT` (`₱%,.2f`), `date_col` (`MMM DD, YYYY`), `OS_/LN_/BL_BROWSE|NEW` + `mode_label`. Theme: `.streamlit/config.toml` (navy sidebar, Inter, `toolbarMode = minimal`); no logo on screen (PDFs keep it). One `type="primary"` button per screen, on the main action. Order statement Checks sub-tab = one switch (`os_checks_view__<sid>`: Issue checks / Checks in this batch / Assign numbers / Corrections). Business rules in `engines/common.py` (`get_conn`, `q_live`, `php`, `has_value`, `cell_text`, dates, calendar marks, `DB_PATH`, `PRIMARY_BANK_ACCOUNT_ID`), `statement_rules.py`, `check_rules.py`, `creditor_rules.py`, `loan_note_rules.py`, `card_rules.py`. `app.py` imports every name back (`app.<name>` still works for tests). New rule code goes in `engines/`, new screen code in `views/`, not `app.py`.
* Stack decision (2026-09-27): **stay on Python + Streamlit.** Single user, one laptop, tiny data; the slowness was all-tabs-rendering, now fixed. Revisit only if: several people with their own logins/roles or approvals, use from phones / other PCs / the internet, or bank/API integrations Streamlit can't host. Then keep Python, `engines/*` and the tests, move SQLite → PostgreSQL and replace only `views/` (Django, or FastAPI + a web front end) — a gradual swap, not a rewrite.
  **2026-09-28: the revisit trigger has arrived** -- Sales/AR (next module) will be used by staff on their own computers, fewer than 5 users. Answer = Phase F: **stay on SQLite, moved to a small cloud server** (WAL + `BEGIN IMMEDIATE`), Google sign-in + roles, Streamlit stays; PostgreSQL deferred until ~10+ users at once, another program needing direct database access, or a provider-run database is wanted. The Sales front end (Streamlit vs Django) is decided at Sales phase 0.
* Tests: `python -m pytest` (~270). `tests/conftest.py` imports every `engines.*` / `views.*` module and repoints every module that has a `DB_PATH` automatically — there is no hand-kept list any more. AppTest note: it does not keep the open page across a button click (it falls back to the first page); open a page with `session_state["_global_jump"] = {"page": "<id>", "state": {...}}` and verify button flows in a browser on the test port (the preview tool reads `C:\Users\Rhodora\Documents\.claude\launch.json`, entry `test-copy-8502`; restart it after editing views -- it does not always reload them).
* `archive/one_time_scripts/` = migration/clean-up scripts that already ran (Phase 8, renumbering, card-number removal…); record only, never re-run. Their tests are there too and are not collected.
* Never commit real data (`.gitignore` covers every `*.db*`, `*.csv`, `*.xlsx/xlsm`, `*.pdf`, `data/`; the one tracked workbook is `excel_invoice_entry/invoice_entry_master.xlsm`, which the app needs). Full card numbers were removed from the database, every copy and git history (2026-09-27) — only `card_number_last4` is used; never store full numbers again.

**Rules added since 2026-09-17 (all live)**
* **No duplicate check number — enforced by the database** (migration 047: triggers on `checks_issued` INSERT/UPDATE OF check_number and `spoiled_checks` INSERT, plus a unique index on spoiled numbers). A number exists once: one check (any status) or one spoiled entry; 7-digit and 10-digit (`399`+7) forms are the same leaf. Log a spoiled check is validated the same way and warns (confirm) when the number is outside every registered booklet.
* **A check is final only once it has a check number.** Until then it can be edited (`edit_parked_check`) or un-parked (`unpark_checks`: deleted with its allocations), even if it was already exported to Chrysanth. Un-parking the last active check returns an Issued batch to Draft; promotion never renumbers a batch that already has its OS number.
* Order statement status shown to the user is derived: **Draft** (no active checks) / **Parked** (active checks, none printed) / **Issued** (at least one printed/exported) / Cancelled. The stored `order_statements.status` is still Draft/Issued/Cancelled.
* Park Metrobank checks = one confirmation showing each check → invoice match; a Draft batch is auto-promoted to Issued on park. Checks can be split "one check per invoice" (dated on each invoice's due date). Checks typed but not parked are remembered in `park_drafts` (sidebar reminder, leave-page prompt).
* Due dates follow the **bank rule**: whole-month terms (30/60/90/120/150) land on the same day of the month (month-end clamp); other terms count days (`add_terms_bank_rule`).
* **Chrysanth (paid version)**: export is a plain MS-DOS `.txt` in `ChequeTransactionTemplate` layout (8 columns, amounts without trailing zeros). Unnumbered checks get a stand-in cheque number `9` + 5-digit OS number + 6-digit check id (remembered in `checks_issued.export_placeholder`; older exports keep `9`+check id) so Chrysanth prints in OS order. Remark = the run number (`RUN 005`) only; file name lists the OS range. Runs are marked printed in Batch run history (`check_print_batches.printed_confirmed_at`) — Chrysanth does not remember what it printed; filter by the Remark **search box**, not the column filter arrow.
* Check numbers can be assigned per order statement in sequence from a booklet (one preview + one confirmation, all-or-nothing, every leaf re-checked at write time). Retract a wrongly assigned number; a typed number always wins over the booklet dropdown.
* Credit cards: the owner only **records** statements (does not pay the cards). Add statement = total due + optional payments received since the earlier statement + optional finance charge; new charges are derived; only the newest statement per card counts as owed (`cc_rules`). Statements list: Outstanding-only switch + search.
* Batch statement PDF: invoice status label is Unpaid / Partly covered / `Check issued · <last check date>` / Paid (label only, stored status unchanged); section headings never stranded; rows tighten to avoid near-empty overflow pages. Bulk statement printing (since last check print / today / 7 days / all; A4 or thermal; summary cover page; option to leave out checks not yet printed = run not marked printed).
* UI: "Search everything" pop-up (`/`, filters as you type); hotkeys (Enter/Esc in pop-ups, Alt+1…0 tabs, Alt+P main action, Alt+N new form; list in the sidebar); last-modified column/filter on batch, loan note and bank loan browsers; sidebar mini calendar with month/year pickers, marked days and prospective month choice.
* "Cleared" is still inferred from the check date in the totals (owner: show both); the bank's real cleared date comes from the imported Metrobank statement (Bank and cash page, reconciliation since 2026-09-27/28) and is shown beside it.

---

## Thermal Statement Printing

Gated mini-phase, started 2026-09-27 (owner's spec). Build hardware-independent first; work against the test/dev database only; stop after each
build step and summarize before continuing. **Status: steps 1-4 (and 2b, link expiry) done (2026-09-27); step 5 (real USB / Bluetooth / network connections) waits for the printer. Real Drive use needs the owner's one-time drive_connect.bat setup.**

**Step 1 (done):** `engines/thermal_statement.py` -- `build_slip(statement_id, 'FILE'|'SUPPLIER', print_type=, version=, reprint_date=, qr_link=)` (same rows as today's statement via `batch_statement_service._fetch_batch_data`; totals recomputed from the lines by `statement_totals()`; cancelled invoices / non-Active checks listed but not counted; a counted line without an amount makes its totals '—'), `render_text()` (48-col preview), `estimate_lines()`, `build_job()` (python-escpos Dummy: ESC @, Font A, per-line align/bold/double height, native QR centred before the closing lines, `cut(mode='PART', feed=True)` = ESC d 6 + GS V), `send_job(job, printer, chunk_bytes)` for step 5, `get_printer_config()` (settings table keys `thermal_*`, defaults in `PRINTER_DEFAULTS`, backend `dummy`). UI: `views/thermal_print.py` `render_thermal_printer_section()` in Order statements -> open batch -> Export Batch Statement (preview per copy + 'Send to the test printer', which builds the job and offers the .bin). Library: python-escpos 3.1 (pinned). Tests: `tests/test_thermal_statement.py`.

**Step 2 (done):** schema (owner-approved) `migrations/048_thermal_statement_printing.sql` + schema.sql: `statement_versions` (per batch version_no, content_hash, pdf_file_name, drive_file_id, drive_link, drive_public, uploaded_at, revoked_at), `statement_prints` (copy_type FILE/SUPPLIER, print_type ORIGINAL/REPRINT/REVISED, backend, status SUCCESS/FAILED, error, drive_file_id, printed_at), `order_statements.needs_reprint`. Applied to the TEST database only; **the live database gets it through update_database.bat** (backup + add; the app's startup check asks for it once this code is live). `engines/drive_service.py`: `GoogleDrive` (scope `drive.file` -- the app sees only files it created; `create_folder`, `folder_exists`, `upload_pdf` = always a new file, `share_public` = anyone-with-link reader on the FILE, `revoke_public`), `statement_folder_id()` (one folder per database, created by the app, id in settings `drive_statement_folder_id`, "(TEST)" for the sandbox, re-created if deleted), `DriveNotConnected` / `DriveError`. Login files in `db_location.drive_dir()` = C:/AP_Data/drive (credentials.json from Google Cloud + token.json from `drive_connect.bat` / `drive_connect.py`, which prints the Cloud setup steps when credentials.json is missing); .gitignore covers them. `engines/statement_version_service.py`: `content_hash()` (fingerprint of invoices / memos / checks for step 3), `current_version()`, `ensure_current_version()` (v1 on first use), `ensure_published()` (A4 supplier-copy PDF uploaded once per version and shared; recorded only after sharing succeeds; reused on reprint; raises -> the print aborts), `revoke_older_versions()` (old link off, file kept, audited). UI: the test printer publishes first and refuses to build a job without a working QR; the preview shows the Drive status and link. Bluetooth warning default 120 lines (owner). Libraries: google-api-python-client 2.200.0, google-auth-oauthlib 1.4.1 (pinned). Tests: `tests/test_statement_versions_drive.py` (fake Drive). **Checked against real Google Drive, 2026-09-27** (owner connected v.jrcroxas@gmail.com): test-copy OS 0009 v1 uploaded to "Slick's AP - statement copies (QR) (TEST)" and opened anonymously (HTTP 200, no sign-in); a throwaway file shared then revoked returned 401 for anonymous visitors (then trashed). The Google OAuth app stays in "Testing" (publishing needs a homepage + privacy-policy URL on an owned site), so **the sign-in expires every 7 days** -> the print aborts with "run drive_connect.bat again"; a reminder in the app was offered, not built.

**Step 2b (done) -- the QR link expires (owner, 2026-09-27):** a version's public link stays on until the batch's **last Active check date + 3 months** (a check counts as cleared on its date; a batch with no checks: last invoice due date + 3 months; same-day month rule with month-end clamp via `add_terms_bank_rule(d, '90')`). Google can't put an end date on an anyone-with-link share, so the app switches it off: `statement_version_service.link_expiry()` (recomputed every time -- re-dated / replaced checks move it), `expire_public_links(drive, today)` (revoke, file kept, audit `STATEMENT_PDF_EXPIRED`, `public_until` refreshed; failures stay public and are retried), `expired_public_versions()` (no Drive call), `daily_link_sweep()` (once a day when Payment run opens; settings key `statement_links_last_sweep`; Payment run warns when links are past their date but Drive can't be reached). Nightly: `expire_statement_links.py`, called by `run_backup.bat` after the backup (never fails it). A **reprint after expiry re-opens the same file until reprint day + 3 months** (`reopened_until`, audit `STATEMENT_PDF_REOPENED`) -- only the CURRENT version is ever re-opened (older ones are switched off by revisions and stay off); a first print of a batch already past its date also gets today + 3 months so a printed QR always works. The slip prints "Online copy until <date>" under the QR (`build_slip(link_until=)`; the QR position is `Line.qr_before`). Schema: migration 049 (`statement_versions.public_until`, `reopened_until`), test database only so far. Checked on real Drive: OS 0009 test copy public 200 -> swept as of 2027-07-01 -> 401 -> reprint -> same file 200. Tests: `tests/test_statement_link_expiry.py`.

**Step 3 (done) -- versions, Needs reprint, ORIGINAL / REPRINT / REVISED:** `statement_version_service`: `content_hash()` (invoices, credit memos, check dates / amounts / statuses -- **check numbers left out**: numbering after printing is the normal flow, not a revision), `sync_version()` (content changed after the current version went out -- published or printed -> the next version starts, unpublished until printed; otherwise the fingerprint is refreshed), `on_relock()` called from `statement_rules.lock_statement()` (changed + an earlier version printed -> `order_statements.needs_reprint = 1`, audit `BATCH_NEEDS_REPRINT`; the lock message says so), `ensure_published()` calls `sync_version()` first (print-time safety net: a change without re-lock still prints a new version), `print_type_for(sid, copy)` per copy (REPRINT = this copy of this version printed SUCCESS; REVISED = an older version printed; else ORIGINAL -- FAILED never counts), `planned_print()` (read-only prediction for the preview), `record_print()` (statement_prints row; a SUCCESS of the latest version clears needs_reprint). After a REVISED print the caller runs `revoke_older_versions()`. **Viewing is never a print** (owner asked): preview, PDFs, Drive link record nothing; test-printer jobs are recorded **only in the sandbox database** (so the first real print on live is never mistaken for a reprint). UI: batch header badge + browser column "Needs reprint"; Payment run reminder with "Open to reprint"; printer section shows "Printing now: FILE COPY as REVISED v2 (supersedes v1)...". Checked on real Drive (test copy, OS 0009): edit after unlock + re-lock -> Needs reprint -> REVISED v4 printed -> flag cleared, old QR 401, new QR 200. (A re-lock is refused while a batch is overpaid -- existing rule -- so no flag then; the print-time safety net still revises.) Tests: `tests/test_statement_reprints.py`.

**Step 4 (done) -- audit + Bluetooth guardrail:** every print goes through `engines/thermal_print_service.print_statement()`: `ensure_published` (no QR copy -> `PrintAborted('drive')`, nothing sent or recorded) -> `open_printer(backend, cfg)` (a port that does not open -> `PrintAborted('port')`, each copy recorded FAILED; missing setup / driver -> `'setup'`) -> one job per copy via `send_job` in chunks; a copy is recorded SUCCESS only after its whole job incl. the cut was sent; a write error mid-job records that copy FAILED and raises `PrintAborted('midjob', done=[...])` with "throw away the partial slip, connect USB, print again -- still the original"; after a REVISED copy, `revoke_older_versions`. `record_print` now also writes one audit entry per copy (`STATEMENT_PRINTED` / `STATEMENT_PRINT_FAILED`: print id, version, copy, print type, printer, status, Drive file id; time = audit row). `needs_bluetooth_warning(backend, lines, cfg)`: serial + a copy over the threshold (120) -> the screen offers Switch to USB and print / Print via Bluetooth anyway / Cancel. One click = one print: `guard_duplicate_submit` + a per-batch session flag while a print runs. Printer settings form (connection, Windows printer name, COM port, IP, Bluetooth threshold) saves via `thermal_statement.save_printer_config()`. Backends: dummy works; network works now (python-escpos Network); serial needs pyserial and USB needs pywin32 -- step 5. Tests: `tests/test_thermal_printing.py` (fake printer that fails on open / mid-job).

### Purpose
Print batch payment statements on an 80mm thermal printer: one FILE COPY for physical filing and one SUPPLIER COPY for handoff. These are internal
summary documents, not BIR records; the database remains the system of record.

### Hardware (not yet purchased -- build hardware-independent first)
* Preferred: Xprinter XP-T837L (USB + Serial + LAN, optional Bluetooth, 4MB buffer). Fallback: Xprinter XP-80T (USB + LAN).
* ESC/POS, Font A, 48 columns, native QR, partial auto-cut.

### Decisions (locked)
**Printing**
* python-escpos with three configurable backends: Win32Raw (USB through the installed Xprinter Windows driver), Serial (Bluetooth virtual COM port),
  Network (LAN, port 9100).
* Printer settings (backend, COM port, IP, line threshold) live in config, never hardcoded.
* Initialize with ESC @; send in chunks; feed before cut.
* 48-column layout; currency printed as "PHP 1,234.56" (no ₱ glyph).
* Header shows the copy label: FILE COPY / SUPPLIER COPY. Signature line ("Received by / Date") on FILE COPY.

**Correctness**
* The statement total is COMPUTED from the invoice / credit-memo lines at print time, never read from `order_statements.total_amount`.
* Explicit NULL formatting: never print "None"; never print a NULL amount as 0.00; missing values print as "—".
* Guard against duplicate prints on Streamlit reruns (single button action + session-state flag).

**QR / digital copy**
* At print time, generate the A4 statement PDF (ReportLab; reuse the existing statement generator, colour: the owner chose to keep today's
  navy #1f3a5f over the spec's #1D5A8E after seeing samples, 2026-09-27), upload it to a dedicated Google Drive folder via the Drive API, set per-file
  sharing to ANYONE-WITH-LINK, and encode that link in the QR (bottom center).
* The folder itself is not shared; sharing is set per file only. Store the Drive file ID and link with the print record. PDFs are never overwritten.
* Upload failure = print aborted (no slip without a valid QR). Drive OAuth credentials and tokens stay out of git (.gitignore).

**Versioning and reprints**
* REPRINT = same content: reuse the existing PDF and link; header shows "REPRINT" with date.
* Editing a batch after unlock creates a new statement version; on re-lock the batch is flagged "Needs reprint" until the revised statement is printed.
* New version = new PDF + new Drive link; header shows "REVISED vN – supersedes vN-1". When a new version is printed, revoke public sharing on the
  previous version's PDF (keep the file privately for audit history).
* Reprints always go through the app, never the printer's hardware reprint button.

**Bluetooth guardrail**
* Before printing, estimate the rendered line count. If the active backend is Serial (Bluetooth) and the count exceeds the threshold (default 60,
  configurable -- owner set 120), warn with three choices: Switch to USB / Print via Bluetooth anyway / Cancel.
* Check that the COM port opens before sending; if not, suggest USB immediately. If a write fails mid-job: stop, tell the user to discard the partial
  slip, connect USB, and retry.

**Audit**
* Log each print in the audit log (`audit_logs`): batch ID, statement version, copy type (FILE/SUPPLIER), print type (ORIGINAL/REPRINT/REVISED),
  backend, status (SUCCESS/FAILED), Drive file ID, timestamp.
* A print counts as SUCCESS only after the full job (including cut) is sent. A FAILED attempt does not count; the retry is still the ORIGINAL, not a REPRINT.

**Owner answers (2026-09-27)**
* Keep the existing "Thermal Statement (80mm) PDF" (print dialog) alongside direct printing, as a backup.
* Other (office) printers stay PDF-then-print (owner, 2026-09-28): only the thermal printer is connected directly. An automatic print-dialog option was offered and declined for now.
* A4 copy colour: keep today's navy #1f3a5f. Bluetooth warning threshold: 120 lines (a normal slip is ~55, ~71 with the QR). Schema proposal approved.
* The slip lists the same content as today's statement: invoices, credit memos, the checks (number, date, amount) and totals.
* Slip layout (owner, 2026-09-27: "exclude" the date received; "make it more readable and not confusing"; "remove check issued, dated..."; "if detailed information is needed then that is the use of QR code"): the slip is a SUMMARY -- small tables with column headings (# / date / amount), dates written out (Jan 08, 2027), one line per item, invoices show the date RECEIVED (not the due date -- owner, later the same day), credit memos listed when the batch has any, no status lines, no memo reasons; only what is counted is listed (cancelled invoices and cancelled / replaced checks are left off -- they are in the A4 copy behind the QR), so every amount on the slip adds up to the SUMMARY block (invoices, less credit memos, NET AMOUNT DUE, less checks issued, REMAINING BALANCE); "Scan for the full statement:" above the QR. The thermal PDF and the A4 statement are unchanged.

### Data
Add whatever tables/columns are needed for statement versions, print records, Drive file IDs/links, the "Needs reprint" flag, and printer config.
**Propose the schema changes to the owner before applying them** (migration + preview/--apply script, tested on a copy, as for every schema change).
Printer config can use the existing `settings` key/value table (category `thermal_printer`) -- no schema change needed for it.

### Build order
1. Layout and formatting against the python-escpos Dummy printer (no hardware), including a text preview in the UI.
2. A4 PDF generation + Drive upload + sharing/revocation.
3. Versioning, "Needs reprint" flag, reprint vs revised logic.
4. Audit logging and the Bluetooth guardrail.
5. Real backends (Win32Raw / Serial / Network) -- after the printer arrives.

### Acceptance criteria
* Printed total always equals the sum of the statement's lines.
* No "None" or false 0.00 values anywhere on the slip or PDF.
* QR scans on a phone and opens the matching A4 PDF without sign-in.
* After a revision, the old QR shows access denied; the new QR works.
* A batch edited after unlock shows "Needs reprint" until reprinted.
* One button click produces exactly one print and one audit entry per copy.
* A long statement over Bluetooth triggers the guardrail warning.

---

## Bank Statement Import and Cash Insights

Started 2026-09-27 (owner: "I do not have the time to reconcile which checks are cleared ... then from there we can use AI business
intelligence to provide insights"). The owner downloads the Statement of Account from Metrobank MBOS and drops it in the app; the app does
the matching. Also the foundation for the owner's future goal: suggested check dates under cash constraints.

**Owner decisions (2026-09-27):** insights local first (nothing leaves the laptop; an AI write-up later, optional); **show both** -- the
app's totals keep "cleared on its check date", the bank-confirmed date is shown beside it (switching the totals is a later decision);
insights wanted: cash position & forecast, checks not yet cleared, collections / inflow trends, unusual items, working capital starting
with cash vs payables (receivables / inventory later).

**MBOS statement (real sample, Jan-Feb 2026, 65-page PDF):** one table per page, 11 columns: Transaction Date, Posting Date, Time,
Check Number (10 digits, 0000000000 = none), Transaction Description, Debit Amount, Credit Amount, Balance, Branch/Channel, Subscriber
Number/TIN, Sequence Number. Headers are printed sideways (pdfplumber reads the words backwards, sometimes in reverse order); dates
MM/DD/YYYY; long amounts wrap inside the cell ("1000000.0 0"). Descriptions seen: IM IN- CLRNG- BATCH / MO CHK IN-CLRNG / MO ENCSHMNT
(our checks clearing), MO CSH DEP / MO LOC CHK DEP / ET CR IBFT / WA CR / MO CM (money in), DE DM RET CHK (returned deposited check),
MO DM PAY UTLTY (bills payment), MO DM (debit memo). Every running balance added up (655 lines).

**Phase 1 (done):** `engines/bank_statement_service.py`: `parse_statement()` (PDF + CSV/Excel with the same headings; never raises;
running-balance check; masked account), `classify()`, `import_statement()` (file hash unique = never twice; `line_key` unique = overlapping
periods never double up; keeps a copy in `bank_dir()` = C:/AP_Data/bank, outside git; audit `BANK_STATEMENT_IMPORTED`), `match_checks()`
(number via `norm_check_number` = migration 047's 7/10-digit rule + amount -> `checks_issued.bank_cleared_date` / `bank_txn_id`;
exceptions: amount_mismatch, cancelled_check_cleared, not_in_system; returned_check), `preview_matches()` (read-only), `exceptions()`,
`not_yet_cleared()` (numbered active checks dated up to the last imported bank date, not cleared), `statement_coverage()`, `last_import()`,
`mark_reviewed()`. Schema: migration 050 (`bank_statement_imports`, `bank_transactions`, `checks_issued.bank_cleared_date`, `bank_txn_id`)
-- test database only so far; live via update_database.bat. UI: `views/bank_cash.py` "Bank and cash" (Daily work): Import tab (drop file ->
preview: period, lines, closing balance, balances check, kinds, how checks would match -> Import) and Reconciliation tab (confirmed / not yet
cleared / need a look, mark looked-at with a note, not-in-system list, checks not yet cleared with days waiting). Payment run reminds when
the last import is over 7 days old; a batch's check table shows "Bank cleared". The sidebar menu is `expanded=True` (13 pages).
**First finding** (test copy import of the Jan-Feb sample): check 3992527921, First Rubber Manufacturing (LEGACY-MS-ACCESS-54), is in the
system as Oct 29, 2026, PHP 6,409 and still active, but the bank cleared it Jan 29, 2026 for PHP 6,408 -- likely a legacy typo; left for
the owner to correct. The system's own checks start Sep 20, 2026, so matching needs a statement from then on.
Tests: `tests/test_bank_statement_import.py` (made-up MBOS-layout statement; real statements never go into the project).

**Phase 2 (done) -- local insights** (`engines/cash_insights_service.py`, tabs on "Bank and cash"; only the open tab is computed; nothing
leaves the laptop): `cash_position()` (last bank balance + date), `expected_daily_inflow()` (average money in per weekday over the last 8
imported weeks), `scheduled_outflows(start, end)` (our active checks NOT bank-cleared on their check date -- or on `start` if already past --
weekend -> Monday via `next_banking_day`; plus Bank Loan / Credit Card dues from `get_payment_schedule_all_tracks()`), `forecast(days)`
(day-by-day from today; cash buffer in settings `cash_buffer`; lowest day, first day under the buffer), `float_summary()` (not cleared: past
the last bank date by age bucket / payee vs dated ahead), `inflow_trends()` (weekly / monthly / by type; partial weeks at the edges of the
imported period are shown light blue and not counted in the average or the slow flag; slow = under 75% of the average), `unusual_items()`
(returned checks, bank debits that are not our checks, lines > 5x the median of their own type, mismatched clearings),
`working_capital()` (cash vs payables due in 7/30/60/90 days, cover, left, days of cash, DPO). Every insight tab warns when the latest bank
balance is over 14 days old. Tests: `tests/test_cash_insights.py` (hand-worked figures). Altair note: nested `alt.condition` / chained
`alt.when` on boolean fields failed here -- use a category column + `alt.Color(scale=domain/range)`. Restart the test app after editing views
(it does not always reload them).

**Bank adjustments -- never edit the book (2026-09-28, owner: "the bank statement is the ultimate truth. if bank cleared an incorrect
amount then make it as adjustment to the system"; "not direct edit the record but tag it as adjustment due to incorrect recording in the
book"; adjust the batch off too; mirror it when the bank took more; dates too).** Table `bank_adjustments` (schema.sql + migration 053;
live via update_database.bat): kind CHECK_AMOUNT (signed bank - recorded) / CHECK_DATE (recorded vs bank date, only when the bank paid
BEFORE the recorded date) with reason "Incorrect recording in the book", and BATCH_BALANCE (same signed amount, statement_id or
loan_note_id) "Bank clearing difference"; `reversed_at` instead of deleting. `bank_correction()` = preview (diff, date_differs, warn: "only
if the stub shows it is the same check"); `adjust_check_to_bank()` replaces the old edit-based `correct_check_to_bank()` -- the check row is
never changed; then `link_to_check(how="Adjusted to the bank")`. `reopen()` reverses a line's adjustments (audit BANK_ADJUSTMENT_REVERSED).
`adjustment_totals()` feeds `statement_rules.get_batch_balance()` (both sides, so what is left is unchanged; OperationalError-safe before
053). UI: "Adjust to the bank" panel (recorded / bank / adjustment) and an "Adjustments to the bank" list on the Reconciliation tab.
Times via `datetime(created_at, 'localtime') AS created_local`.

**Smarter suggestions + first real matching check (2026-09-28).** Live Sep 20-25: 30 of 31 check clearings matched automatically
(PHP 1,881,981.35, amounts all agreed); 5 pre-system leaves tagged Legacy record. Fixed on live (backup
ap_disbursement_before_check_fixes_20260928.db): check 3992527921 = Jan 29, 2026 per owner (date changed in the app 08:55) -> corrected to
the bank PHP 6,408 + linked (its legacy batch now shows PHP 1 owed); wrong accepted link bank ...673 (Sep 21) -> our ...678 (dated Sep 29)
reopened. `suggestions()` now gives `confidence` High / Medium / Low + `reasons` (sorted best first; a check dated after the bank paid it is
always Low; `reason` = the old rule text, unchanged). `series_hints()`: a leaf not in the system whose neighbour leaves (+/-5) are ours for
the same payee and amount -> "probably an unrecorded <payee> check"; `tag_series()` resolves it "Not recorded in the system" (no check
created). UI: badges + Tag button in Suggested matches. Early-pay discounts dropped from the list (owner: not practiced in the PH).

**Data health (2026-09-28, owner-approved).** `engines/data_health_service.py` `findings(include_fine, today)`: one read-only scan in four
groups -- Checks (same number twice, bank paid the number before our check date, printed with no number, stale 180+ days not bank-cleared,
dated 1+ year ahead, zero amount), Invoices and batches (due before invoice date, year outside 2020..this year+2, zero amount, not in any
batch, empty batch, overpaid batch via `get_batch_balance`, check payee != batch supplier), Bank vs our records (open amount_mismatch /
cancelled_check_cleared / cleared_twice lines; checks not cleared `stale_days()` after their date), Suppliers (same name twice -- a bank as
BANK_LOAN + CREDIT_CARD is by design; inactive but owed; SUPPLIER with no terms -- banks / lenders left out). Each finding carries
`page` / `state` for `go_to`. "It's fine" = `mark_fine(keys, note)` / `show_again(keys)`, stored in `duplicate_dismissals` with a `health|`
key (no new table), audited DATA_HEALTH_FINE / DATA_HEALTH_SHOW_AGAIN. Page `views/data_health.py` (System menu, `data-health`); Payment
run shows one line with the open count (`open_count()`). First live run: 21 printed checks without numbers (printed Sep 26, dated
2027) + check 3992527921 (owner confirmed correct). Tests: `tests/test_data_health.py`.

**Bank is the truth + capture check + double-entry guard (2026-09-28, owner: "Always remember bank statement is ultimate truth and have a
pass test to ensure system captures the bank statement well"; "put a guard rail for double entry").** MBOS CSV / Excel downloads have 9-11
title lines above the headings (and spacer columns in .xls): `_parse_sheet` finds the headings row and reads the account number; CSV/XLS
read the same 3,770 lines as the PDF in 1 s / 0.2 s vs 40 s. `capture_check(parsed, known_last4)`: every transaction row read (`raw_rows`),
every line has a balance, running balance adds up, opening + in - out = closing, no exact duplicate lines, same account -- ANY fail blocks
`import_statement()` (returns `blocked`) and the Import button; after saving, `_stored_check()` proves every line is in the database with
the same sums or the whole import is rolled back (`CaptureCheckFailed`); a PASS is audited `BANK_STATEMENT_CAPTURE_CHECK`. Double entry:
same file (hash), same line (line_key), and now a check cleared a second time -> `cleared_twice` (never overwrites the first clearing).
One-click "Correct our check to the bank" (`bank_correction()` / `correct_check_to_bank()`, amount via `apply_check_correction`, then linked
"Corrected to the bank"); refused when the bank cleared the leaf before our check date (check 3992527921: the owner's stub confirmed Oct 29, 2026 /
PHP 6,409 and the same number; the bank's Jan 29, 2026 / PHP 6,408 line is the odd one, resolved as Legacy record). `_set_aside_before_system` also tags
unmatched debits ("Expected bank debit") and bounced checks ("Customer bounced check") dated before the first check. Tests:
`tests/test_bank_capture_check.py` + additions in the import / recon v2 tests.

**Reconciliation v2 (done 2026-09-28, plan phase 1 of 4 -- next: Dashboard panel + Overview bank section, Reports quick wins, "My
reports" builder).** `match_checks()` only touches still-open lines (`reviewed = 0` and status NULL / amount_mismatch / not_in_system -- a
re-import never undoes a hand link), then `_match_other_debits()` (DEBIT_MEMO = a `bank_loan_installments` amount within +/-5 days of its due
date -> `matched_loan` + `matched_installment_id`; BILLS_PAYMENT / DEBIT_MEMO = a `cc_statements` total_amount_due or unpaid_balance_next
within +/-10 days -> `matched_card` + `matched_cc_statement_id`; else `unmatched_debit`) and `_set_aside_before_system()` (not_in_system
lines dated before MIN(check_date) -> resolved "Before the system"). Resolving: `resolve(txn_id, reason, note)` (RESOLUTIONS list; audit
`BANK_LINE_RESOLVED`), `link_to_check(txn_id, check_id)` (`matched_manual`, sets bank_cleared_date; refused if the check is cleared by
another line; audit `BANK_LINE_LINKED`), `reopen(txn_id)` (undo: un-clears a hand-linked check, re-matches; audit `BANK_LINE_REOPENED`),
`link_candidates()`, `resolved_lines()` (owner's first, "Before the system" last). `suggestions()` = same amount AND (number one digit off
or two neighbouring digits swapped -- `_one_digit_apart` -- or our check has no number and is dated within 7 days); never automatic;
`reject_suggestion()` stores `rejected_check_ids`. Per statement: `update_reconciled()` / `imports_status()` -> Open / Reconciled
(`reconciled_at`). Import times are stored UTC by CURRENT_TIMESTAMP -- read them through `local_times()` / `datetime(x, 'localtime')`.
`engines/bank_recon_report.py`: `recon_statement(import_id)` = bank balance at the last bank date - outstanding checks (Active, numbered or
printed, dated on or before it, not bank-cleared by then) = available; plus the bank items not in the system with how each was resolved;
`generate_recon_pdf()`. It is **bank vs issued checks** (owner's choice: the system keeps no cash book). `engines/bank_alerts.py`
(`all_alerts()`, shown on Bank and cash and Payment run, which no longer has its own old-statement reminder): low cash under the buffer
within 7 days (only when the bank balance is at most 14 days old), large unexplained debits (> `recon_debit_alert_factor` x the median of
that kind, default 5), checks not cleared `stale_deposit_days` after their check date (default **7** -- owner first said 1, then 7 because
the statement is uploaded weekly), last import over 7 days old. Settings via `engines/settings_store.py`. UI: Reconciliation tab
(statements with status, suggested matches Accept / Reject, Need a look + "Deal with" panel: link / open our check (batch Corrections or
loan note Checks) / tag reason + note, Resolved expander with Reopen, alert settings) and a new "Reconciliation statement" tab (screen +
PDF). Schema: migration 051 (bank_transactions.resolution / matched_installment_id / matched_cc_statement_id / rejected_check_ids,
bank_statement_imports.reconciled_at, and `saved_reports` for phase 4) -- test database only so far. Also fixed: text in
`account_history_pdf_service` table cells is XML-escaped ("S&P" broke PDFs). Tests: `tests/test_bank_recon_v2.py`.

**Phase 2 (done 2026-09-28) -- bank figures on the Dashboard and in the overall report.** Dashboard: a "Bank and cash" panel after the third
metrics row (`cash_insights_service.dashboard_bank_summary()`: bank balance + date, % of numbered active checks dated within the imported
statements that the bank cleared, float past their date, open lines, lowest forecast in 30 days vs the buffer -- only while the balance is
<= 14 days old) + `bank_alerts.all_alerts()`. "Stale checks" = `dashboard_kpi_service.get_stale_checks()` (Active, > 180 days, NOT
bank-cleared). **Due today = still owed** (owner): `get_total_ap_outstanding()` (Dashboard total, `>= today`) and
`get_combined_ap_summary()` (lender checks subtracted only when dated before today; bank-loan installments `>= today`). Only these two
totals changed -- about 30 other places (v_effective_checks' "Cleared", statement / loan-note paid amounts, analytics, bank-loan remaining)
still treat a check as cleared ON its check date; changing those is the owner's call. Overall report: `overview_report_service._bank()` ->
`cash_insights_service.period_bank_summary(start, end, prev_start, prev_end)` (coverage from the imports' own periods: full / part / none;
balance at the period end, money in vs previous (only if the previous period is fully covered), cleared vs dated checks, float at the end,
bounced deposits, open lines), `bank_rows()` / `bank_note()` shared by screen and PDF, a "Bank:" highlight before the watch-list line. The
PDF gives Bank and cash its own page and is built twice (`_TocDoc.afterFlowable` records the page of each `_section(..., toc=key)`), so
the cover's contents show the real page numbers (they were hard-coded and wrong when a section ran long). Tests:
`tests/test_bank_dashboard_overview.py`.

**Phase 3 (done 2026-09-28) -- Reports quick wins.** `engines/export_service.py`: one list of friendly column names (`COLUMN_LABELS`,
`label()`), money / date / date-time columns by name (`MONEY_COLUMNS`, `DATE_COLUMNS`, `DATETIME_COLUMNS`, `kind_of()`), `csv_bytes()`
(friendly headers, money 2 decimals, ISO dates, UTF-8 BOM) and `excel_bytes()` (one sheet per table, #,##0.00, Mmm dd, yyyy, frozen header).
`views/shared.py`: `show_table(df, labels, hide)` (friendly names + ₱ + MMM DD, YYYY on screen), `table_config()`, `export_buttons(df or
{sheet: df}, name, key)` (CSV + Excel side by side; the files are built only when clicked -- `download_button(data=callable)`). `views/reports.py`
rewritten as one function per report (`REPORTS` dict, same widget keys) using them everywhere; heavy queries cached 60 s (`c_*` wrappers,
cleared by refresh()); `_daily_view()` shared by Schedule / Parked, `_status_pivot()` by both rundowns; the batch rundown reads once and
narrows with `reports_service.filter_rundown()`. Schedule of payments: Track / Payment date (All dates, each month, Custom range) / Payee
filters via `reports_service.filter_schedule()`, also used by `generate_payment_schedule_pdf(tracks, month, date_from, date_to, payees)` so
screen, files and PDF match (widget key `sched_period_filter`). Overview shows the trend chart and check activity on screen. PDFs:
names in title bands / section headings / subtitles are escaped ("S&P"); `generate_rundown_pdf` now uses `widths` to share the room
between text columns when the table is too wide. Tests: `tests/test_reports_quickwins.py`.

**Phase 4 (done 2026-09-28) -- "My reports" builder** (Reports page switch "Standard reports / My reports"; `views/my_reports.py`,
`engines/report_builder.py`). A report is a JSON spec checked by `validate()` against a fixed `DATASETS` list (checks, invoices, payables
all tracks via `_cash_rows`, credit memos, loan-note installments, bank-loan installments, card statements -- last 4 only, never
`card_number_full` -- bank lines, audit log); **no free SQL**. Spec: dataset, date_field + `preset` (`PRESETS`, worked out on the day it runs
by `preset_range()`, so saved reports roll forward; Last/Next N days; Custom range), filters (`OPS` per field kind: contains / is one of /
is not one of / between), columns (in order), group_by (up to 2; dates by day / week / month / quarter / year), aggregates (count, sum,
avg, min, max), sort, top_n, chart (bar / line, grouped reports only). `run(spec, today, data, override_range)` -> `ReportResult` (table
with Subtotal / Grand total rows, kinds, detail_all for "Export all rows", criteria in words, headline, chart data);
`generate_report_pdf()` (rundown PDF + chart via the new `before_table` flowables). Saved in `saved_reports` (migration 051): `save_report`
(unique name, any case), `duplicate_report`, `delete_report` (audited REPORT_SAVED / REPORT_DELETED), `set_flags` (pin_dashboard,
include_in_overview), `mark_run`, `pinned_results()` (Dashboard "My reports" cards: headline + chart + Open), `overview_results(start, end)`
(Overview screen + its own PDF page, run for the overview period when the report has a date; contents page number included). View notes:
criteria widgets live under `mr_*` keys; `_persist()` keeps them alive while hidden; `_mr_pick_next` selects a report before the picker is
drawn (Save, Duplicate, the Dashboard's Open). Export helpers take `kinds` (money / number / count / date / datetime). Tests:
`tests/test_report_builder.py`.

**Standard reports combined, 13 -> 9 (owner, 2026-09-28: "clean redundant standard reports ... if it can be integrated or combined to a
similar report then do so"; take them off the menu, keep the code).** Parked checks is now the "Checks not yet printed (parked)" view of
Schedule of payments (`sched_show`); the batch and invoice rundowns are "Supplier — rundown" with By batch / By invoice (`rpt_rundown_view`);
the three master rollups are "Master rollups — aging and exposure" (`rpt_rollup_view`). `OLD_REPORTS` maps the old menu names to the new
report + view (a tab left open on an old one lands in the right place). Nothing was deleted: every screen function, PDF and query remains.

**Due today = still owed, EVERYWHERE (2026-09-28, owner picked it after phase 2).** Something dated today is not paid / cleared yet;
it is paid once its date has PASSED. SQL `<= DATE('now','localtime')` (paid) became `<`, `>` (owed / scheduled) became `>=`, in 46 places:
`v_effective_checks` ("Cleared" -- migration 052, view re-created by update_database.bat), statement_rules (invoice Paid status),
loan_schedule_service (Settled), bank_loan_service.CLEARED_SQL (+ interest_service, bank_loan_report_service, views/bank_loans), batch
rundown / lender master / servicing, dashboard KPIs (DPO too), analytics, overview `_cash_rows`, report_builder, views/order_statement,
views/lenders. Also fixed: the AP aging used JULIANDAY('now') (UTC time of day), so an invoice due today fell in no bucket -- now
`JULIANDAY(DATE('now','localtime'))`, due today = Current. Tests: `tests/test_due_today_rule.py`. Keep this rule for anything new.

**Check-date planner (2026-09-28, the owner's long-term goal; rules agreed that day).** Page "Check-date planner" (Daily work, after Bank
and cash; `views/planner.py`, `engines/check_date_planner.py`). Scope: invoices with NO check yet (`open_items()`: batches not cancelled /
locked whose net total isn't covered by active checks; the uncovered amount spread over open invoices, any shortfall -- credit memos,
older unlinked checks -- taken off the OLDEST invoices, so rows always add up to the batch's remaining). Rules (settings
`planner_max_days_late` default 7, `planner_daily_limit`, `planner_daily_max_checks` (0 = none), `planner_priority_suppliers` (ids),
cash buffer = the forecast's `cash_buffer`, 0 until the owner sets one): never before the due date; bank days only (no weekends, no
`calendar_blocked_dates`); forecast balance >= buffer from that day on; daily limits count what is already going out; never two checks to
one supplier on one day (house rule) -- a batch's invoices on the same day become ONE check; priority suppliers first, then due date, then
older batch. Short cash -> moved later up to max_days_late; else "Doesn't fit" on the last allowed day with the shortfall.
`summary["already_short"]` = the day existing checks / dues alone go under the buffer. Cash: the bank balance if <= 14 days old
(`cash_insights_service.scheduled_outflows`), else the owner types today's cash (then outflows = the payment schedule from today). Money
in: average per weekday from the statements, or a typed amount per bank day. Output: per invoice (date, days late, status, reason in
words), per check, per day. `send_to_batch()` writes the batch's checks into its Issue-checks grid as a park draft ("By number of
checks", audited PLANNER_DATES_SENT) -- nothing is parked; the page then clears that batch's grid widgets and opens it. Tests:
`tests/test_check_date_planner.py`. Next step of the goal: the AI layer (plain-language explanation / what-ifs on top of this engine).

**Hover help ("?") on the relevant fields (2026-09-28, owner: "not all, just the relevant ones").** 72 `help=` texts on entry fields,
settings and computed figures across admin, analytics, dashboard, bank and cash, bank loans, checks, credit cards, creditors, lenders,
order statements, shared check-number widgets and thermal printing. Plain labels (Supplier, From / To ...) were left alone. New fields:
add a help text when the field has a rule, a catch or a computed meaning.

**Where things live (2026-09-28, owner asked "where should I store my system?").** Live data `C:\AP_Data` (database, `bank\`, `drive\`
sign-in files) -- stays on the local disk, never in a syncing folder (BitLocker recommended). Daily backups -> Google Drive
`H:\My Drive\Claude9OS\AP_Backups`, now pinned by `local_settings.bat` (`AP_BACKUP_DIR`, not in git) so they stay off-site even if the
program folder moves. **Program: MOVED to `C:\AP_System` on 2026-09-28** (from `H:\My Drive\Claude9OS\Project_Checke`, now retired --
a git history inside a syncing folder can collide); the owner copied it and re-pointed the "AP System Daily Backup" task (daily 18:00,
runs only while signed in, no password) and the desktop Start / Stop shortcuts; update_database.bat was then run from here (migrations
050-052 live). `.claude/launch.json` points here. **Private GitHub copy (code only) since 2026-09-28:** `origin` =
https://github.com/Jrcr01/slicks-ap-system (PRIVATE -- keep it that way: notes mention real supplier names and check numbers); the branch
was renamed `master` -> `main` to match GitHub Desktop. Sign-in is the owner's (Git Credential Manager / GitHub Desktop). After each
commit, `git push` (or Push in GitHub Desktop) keeps the off-site copy current. Data never goes there (.gitignore). Card numbers: every live / copy / backup database was checked on 2026-09-28 -- no full card
number anywhere (the 2026-09-27 clean-up covered them all). History audit before any push: no databases,
statements, CSVs or sign-in files ever committed; `excel_invoice_entry/invoice_entry_master.xlsm` holds the supplier list + terms only;
docs / tests mention some real supplier names and check numbers (fine for a PRIVATE repository only). The test app
(`run_test_streamlit.bat`) now listens on 127.0.0.1 like the live one -- the test copy holds real data.

---

## Phase F — Foundation: cloud server, logins, safe for a few users

Scoped 2026-09-28 (owner). **Status: scoped, not started. Gated: finish each step, stop, summarize, wait for the owner before the next.**
Sits between AP (done, live) and Sales/AR (next module).

### The owner's situation (the reasons for every decision below)
* One module at a time; AP owns the shared master data until Sales starts; then Sales/AR shares the same database.
* Sales/AR will be used by staff on their own computers (tasks delegated). **Fewer than 5 users in total.**
* Hosting: **a small cloud server** (owner, 2026-09-28).
* The owner runs the system alone and is not an IT person -> keep the moving parts few, the monthly cost low, and every risky step
  behind a preview / `--apply` script with a backup first (the existing rule).

### Decisions (locked)
* **Stay on SQLite, moved to the server.** Staff use a browser; only the app on the server opens the database file, so the
  "many PCs sharing one file" problem does not exist. For < 5 users SQLite is enough with: **WAL mode** (reading while someone saves),
  `busy_timeout` (wait instead of error), and **`BEGIN IMMEDIATE` for every write transaction** (saves happen one at a time).
* **PostgreSQL deferred, not dropped.** F1 makes it a one-setting switch later. Move only when one of these happens:
  regularly ~10+ people at once; another program (BI / reporting tool, a second app) must connect to the database directly;
  or the owner wants the provider to run the database and its point-in-time backups.
* **Streamlit stays.** The Sales front end is decided at Sales phase 0, not here.
* Sales/AR goes in the **same database** with its own table prefixes (`sales_`, `ar_`); shared: parties (creditors now), `bank_transactions`,
  settings, audit.
* Business dates follow **Asia/Manila**, never the server clock (cloud servers run on UTC).
* Real data never in git; never modify the live database from a session (unchanged).

### Found in the code (2026-09-28 scan) -- handle each, don't rediscover
* 44 files import `sqlite3`; 102 direct `sqlite3.connect(...)` calls beside 82 `common.get_conn()` calls.
* **Foreign keys are only enforced on the `get_conn()` path** (it runs `PRAGMA foreign_keys = ON`; SQLite has them off by default), so the
  102 direct connections write with no link checks -> the live data may hold orphan rows (e.g. an allocation pointing to a deleted invoice).
* 98 uses of `date.today()` / `datetime.now()` and 69 SQL lines with `'localtime'` / `JULIANDAY` (24 files) -> on a UTC server "today" is
  yesterday from 00:00 to 08:00 Manila time: the **due-today-is-still-owed** rule, the planner and the aging buckets would be wrong.
* OS / LN numbers are `MAX(existing) + 1` (`db_engine.promote_draft_statement`, `loan_note_rules`) -> two users promoting at once collide.
* Payable-ceiling checks (`issue_check`, `issue_checks_grid`, `apply_check_correction`, `lock_statement`) read the balance, then write ->
  two users issuing on the same batch at the same moment could both pass and overpay.
* `audit_logs` has no user column -> with staff, "who did it" is lost.
* Laptop-only pieces: thermal printing (USB / Bluetooth / office LAN cannot be reached from a cloud server), `C:\AP_Data` paths (bank
  statement copies, Drive sign-in files), `.bat` / `.ps1` / Windows Task Scheduler (backup, link sweep, start / stop).
* Google Drive sign-in expires every 7 days (OAuth app in Testing) -> on a server nobody is there to re-run `drive_connect.bat`.

### Build order
**F1 -- One door, one clock, safe writes (on the laptop; no visible change for the owner).**
*Progress (split into gated steps 2026-09-28): **F1.1 done** -- `fk_check.py` (read-only, runs `PRAGMA foreign_key_check` on a backup-API
copy; exit 0 clean / 1 found / 2 unreadable; tests `tests/test_fk_check.py`): live and TEST = 33 tables, 41 declared links, **0 broken**.
Next: F1.2 one door (connections + WAL / busy_timeout; check backup / restore / export scripts that copy the raw file, since WAL keeps
recent writes in `-wal` until a checkpoint), F1.3 one clock, F1.4 safe writes + `doc_counters`, F1.5 `audit_logs.user_email`.*
* Every connection through `engines/common.get_conn()` (and one `df_query` for DataFrames). No `sqlite3.connect` outside `common.py`.
  `get_conn()` sets: `foreign_keys = ON`, `journal_mode = WAL`, `busy_timeout = 10000`.
* Write helper `common.write_tx()` (context manager: `BEGIN IMMEDIATE` ... commit / rollback). Every function that checks then writes
  (payable ceiling, lock, promote, check-number assignment, allocations, imports) does both inside ONE `write_tx()`.
* Numbering: a `doc_counters` table (series, year, last_no), incremented inside the promote transaction -- never `MAX + 1`.
* One clock: `common.today()` / `common.now()` with `ZoneInfo("Asia/Manila")`; SQL gets today as a **parameter**, never
  `DATE('now','localtime')`. Replace the 98 Python calls and 69 SQL lines. `tests/test_due_today_rule.py` must still pass, plus a new
  test that fakes the clock at 07:30 Manila (= 23:30 UTC the day before) and checks due-today, the aging bucket and the planner.
* `audit_logs.user_email` (nullable; existing rows = the owner). Every write function takes the acting user (the owner until F2).
* Foreign keys: read-only `fk_check.py` (`PRAGMA foreign_key_check` on a copy of live) lists orphan rows for the owner; fixes go through a
  preview / `--apply` script.
* Portable SQL for new or touched code (keeps PostgreSQL a switch later): `INSERT ... RETURNING id` instead of `lastrowid`,
  `ON CONFLICT` instead of `INSERT OR IGNORE/REPLACE`. Untouched old queries are left alone.
* New test: two threads issue checks on the same batch at once -> exactly one passes the ceiling; two promotions -> two different numbers.
* Acceptance: full suite green; no `sqlite3.connect` outside common.py; no `'localtime'` or `date.today()` in engines / views;
  `fk_check.py` on a copy of live reports zero orphans.

**F2 -- Logins and roles.**
* `st.login()` with Google sign-in (tells the app WHO; roles are ours). Table `app_users` (email, name, role, is_active, created_at).
* Roles: `owner` (everything), `sales` (Sales/AR pages only -- none exist yet), `viewer` (read-only reports). Unknown email = no access.
* Every page declares who may open it; `st.navigation` shows only allowed pages; engine write functions check the role too
  (a hidden button is not a permission). Audit rows carry the user's email.
* Admin page: add / deactivate users, change role (owner only, audited). Works on the laptop first, before any server exists.

**F3 -- The cloud server.**
* Region: **Singapore** (closest to the Philippines). One small Linux VM (1-2 GB RAM is plenty for < 5 users and this data size).
  Candidates: DigitalOcean, AWS Lightsail, Vultr (all have Singapore) -- owner picks. No separate database service is needed.
* Only HTTPS (443) open to the internet, through a reverse proxy with an automatic certificate (e.g. Caddy) on the owner's own domain.
  **Domain follows the business name, Slick's Tire Supply** (owner, 2026-09-28), e.g. `slickstiresupply.com` / `.com.ph` / `.ph`
  (owner checks availability and registers). The system lives on ONE subdomain for every module, e.g. `app.slickstiresupply.com`
  (not `ap.`), so Sales/AR joins the same address and the bare domain stays free for a public website later. The TEST site:
  `test.slickstiresupply.com`.
  SSH by key only. The app runs as a service that restarts on failure and after a reboot.
* The database file lives on the server's disk outside the app folder (the server's `AP_Data`), never inside git or a synced folder.
* Secrets (Google login client, Drive credentials) in a server environment file, never in git.
* Backups, three layers:
  1. Nightly `backup_live_db.py` on the server (SQLite online backup, safe while the app runs), newest 30 kept on the server.
  2. **Off the server every night -- decided (owner, 2026-09-28): today's setup.** A laptop task downloads the newest server backup into
     the existing Google Drive `AP_Backups` folder (newest 30). Guardrail, because this only runs while the laptop is on: the server
     records each successful download, and Payment run warns the owner when no copy has left the server for 2 days.
     Cloud object storage (runs without the laptop) is **deferred** -- add it when the setup needs it (e.g. the laptop is often off,
     or Sales staff depend on the system daily).
  3. The provider's weekly whole-server backup (a few dollars; lets the whole server be rebuilt).
* The monthly restore test moves to the server: restore the newest backup into a scratch copy, run the schema check and the test app on
  it, must say PASSED.
* Server jobs replace the Windows task: nightly backup, `expire_statement_links`, daily link sweep.
* A TEST site on the same server with its own database copy (replaces port 8502), login required, owner only.

**F4 -- Laptop-only features.**
* Thermal printing -- **decided (owner, 2026-09-28): download and print first.** The app gives the slip as a PDF (and the raw `.bin` job);
  the laptop prints it. Audit / version / reprint rules unchanged: a print is recorded when the owner confirms "Printed" after the
  download (a download alone is not a print -- same rule as "viewing is never a print"). A print helper on the laptop (one-click,
  direct to the printer) is **deferred** until the printer is bought and the download step proves tedious.
* QR copies -- owner picks: keep Google Drive (needs a sign-in that does not expire weekly), or let the app serve each statement PDF on an
  unguessable link with the same expiry rules (no Google dependency, no 7-day sign-in; the server is on the internet anyway).
* Bank statement files and other uploads: server storage outside the web root, included in the backups.
* Chrysanth export and all downloads: unchanged (the browser downloads the file).

**F5 -- Cutover.**
* A quiet evening: stop the laptop app -> final backup -> copy the database file to the server -> the owner checks a list of known
  figures (open batches, AP total, bank balance, a few checks) -> switch. No conversion: it is the same SQLite file.
* The laptop copy is kept read-only for 30 days. `CLAUDE.md` "How it runs" rewritten for the server; the laptop `.bat` files archived.

### Decisions for the owner (ask before the step that needs them)
* Decided 2026-09-28: domain follows the business name (Slick's Tire Supply), one `app.` subdomain for all modules; off-server backup =
  today's setup (laptop -> Google Drive), cloud storage later if needed; thermal = download and print first.
* Still open -- F3: provider (DigitalOcean / AWS Lightsail / Vultr, Singapore); the exact domain once availability is checked.
* Still open -- F4: QR copies on Google Drive or served by the app.
* Before Sales: customer data will be on a cloud server -> check the Data Privacy Act (RA 10173) obligations with the accountant / counsel.

### Acceptance criteria (whole phase)
* Business "today" is Manila's today at any hour of the server clock.
* Two users promoting / issuing checks at the same moment never collide on a number and never overpay a batch.
* Nobody can open the app without a login; a `sales` user cannot open or call any AP function.
* Every audit row says who did it.
* The same figures on the server as on the laptop after the cutover.
* A backup reaches Google Drive every night the laptop is on; the app warns after 2 days without one; the monthly restore test PASSES.
* A thermal slip downloaded but not confirmed "Printed" is not recorded as a print.

---

## Current Priority / Scope

Phased roadmap — work **one phase at a time**, and **ignore the code of other phases** while a phase is active (don't touch, extend, or polish anything belonging to a later phase):

1. **Supplier — Order Statement.** Includes Checks, Credit Memo, Invoices, Creditor Master (shared registry). Considered acceptable as of 2026-09-05 — two known schema-level gaps (checks-must-belong-to-a-batch constraint; Creditor Master type set cleanup) explicitly deferred to a later migration phase, not forgotten. **Update 2026-09-06:** the checks-must-belong-to-a-batch gap is closed (migration `018_checks_batch_not_null.sql`) — only the Creditor Master type set cleanup remains deferred.
2. **Private Lender — Loan Notes (Repayment Schedule).** Accepted 2026-09-06 — no known deferred gaps.
3. Bank Loan — Monthly Dues. Implemented 2026-09-06 against a real bank amortization schedule (see Phase 3 section below) — no computation, pure data entry (manual grid + spreadsheet import), untracked cash settlement. Replaced the pre-existing `bank_loans`/`bank_amortization_schedules` ADA-only stub.
4. Necessary Reports. Scoped and partially built 2026-09-06 — see Phase 4 section below.
5. Dashboard — KPI. Started 2026-09-06 — see Phase 5 section below.
6. Others. Check printing (Chrysanth-style engine + the park-then-print "check run" workflow) started 2026-09-06/07 — see Phase 6 section below. Redesigned 2026-09-07 (Phase 6 Redux: check numbers move to print-time assignment with multi-booklet auto-sequencing) — see Phase 6 Redux section below. Simplified again 2026-09-13 (Phase 6 Redux v2: auto-sequencing dropped in favor of manual per-booklet dropdown selection; design finalized, implementation deferred behind a stabilization pass) — see Phase 6 Redux v2 section below.

**Next (2026-09-28, owner):** Phase F — Foundation (cloud server, logins, safe for a few users; see its section above) -> then the **Sales/AR module** (same database, own table prefixes `sales_` / `ar_`, shares parties + `bank_transactions`). One module at a time; AP owns the shared master data until Sales/AR starts, then it is consolidated.

## Update Protocol

* When you complete a backlog item, check it off (`[x]`) in this file and add a one-line note under it if the implementation deviated from the spec below.
* When you ask me a clarifying question during a session and I answer, log that decision **immediately, in MEMORY.md** — don't wait until the end of the session. A clarification that changes behavior and isn't written down is lost the moment the session ends.
* When you make a decision not covered by this file (a field name, an edge case, a UI choice), log it in MEMORY.md the same way. If it changes something stated in this file, update this file too — MEMORY.md is the log of *why*, this file is the current *what*.
* **Note:** this section was dropped from the file in a prior session and has been restored — if it's ever missing from a version you're reading, that's a bug, not intentional; restore it rather than working without it.

## Core System Configuration

* Primary Bank Account: Metrobank Account 1 (Branch Code 399). **This system does not track bank cash/balance at all** (abolished 2026-09-06, Phase 5 round 2, migration `016_drop_bank_balance.sql`) — `bank_accounts` only identifies which physical account a check is drawn against (bank name, account name/number, branch code), nothing more. There is no available-balance figure anywhere in the UI and no manual balance-adjustment feature; the real spending limits in this app are the payable ceilings (see the hard-rule bullet below), which this has no effect on.
* Database: ~~`data/ap_disbursement.db`~~ — **corrected 2026-09-27:** the live database is `C:\AP_Data\ap_disbursement.db` (moved out of Google Drive 2026-09-20; see Current State).
* UI Engine: Streamlit (`app.py`).
* **Regression test suite (added 2026-09-14):** `tests/` — run with `pytest` from the project root (`pytest.ini` sets `testpaths = tests`). 49 tests across `test_due_date.py`, `test_invoices.py`, `test_payable_ceiling.py` (the system-wide hard rule above), `test_creditor_master.py`, `test_credit_cards.py`, `test_duplicate_guard.py`, plus `test_smoke.py` for the fixture itself. Every test runs against a throwaway SQLite DB built fresh from the real `schema.sql` (`tests/conftest.py`'s `db` fixture) — **never the live database.** Two things a future session needs to know before adding more:
  * **No shared DB_PATH.** `app.py` and every one of the 17 `engines/*.py` modules hardcodes its own `DB_PATH = "data/ap_disbursement.db"` module-level constant — there's no shared config. The `db` fixture patches all of them; a new engines module needs adding to `conftest.py`'s `_DB_PATH_MODULES` list or its functions will silently keep hitting the real database during tests. **Corrected 2026-09-27:** the hand-kept list is gone — `conftest.py` now imports every `engines.*` module and repoints every module with a `DB_PATH` automatically; the suite is ~270 tests.
  * **Known coverage gap:** the Creditor Master grid's type-change safeguard (blocks changing `creditor_type` once a creditor has transaction history — see the 2026-09-14 Credit Cards/Creditor Master entries below) lives inline in the Streamlit tab's Save-button handler in `app.py`, not in a standalone function, so it isn't unit-tested here. `creditor_reference_counts()` itself (the piece that decision depends on) is tested. Extracting the guard into a small pure function (e.g. `creditor_type_change_allowed(creditor_id, new_type, old_type)`) would close this gap — flagged, not done, since it's a refactor beyond "add tests."
* Branding (added 2026-09-14): the company running this system is **Slick's Tire Supply** — logo at `assets/slicks_logo.jpg`, theme colors in `.streamlit/config.toml` (`primaryColor #015088`, brand blue; `secondaryBackgroundColor #EAF2F8`; `textColor #0B2545`), both sampled directly from the real logo file's pixels. Wired via `st.set_page_config(page_icon=LOGO_PATH, ...)` (browser tab favicon), `st.logo(LOGO_PATH, size="large")` (small nav-corner mark — capped ~32px by Streamlit's own design, not a full banner), and a full-size `st.image(LOGO_PATH, width=180)` at the top of the sidebar for a legible wordmark.
* Check Printer: Chrysanth Check Writer.
* Legacy reference (MS Access, current live system): "Order Statement" form/report confirms the structure — header (Statement ID, Supplier, Issued flag, Notes) + Orders subtab (invoice lines: DocRef, DateReceived, Terms, Amount, Less%, NetAmount, DueDate) + Payment subtab (check lines: DueDate, Check Number, Amount). Use this as the ground-truth data shape when building the new system, not a reinvention. **However: this is a reference for understanding existing structure, not a blueprint to copy verbatim — the point of the rebuild is to correct the old system's mistakes, not replicate them. When legacy structure conflicts with a cleaner design (e.g., a manual field that should be computed instead), flag it and ask rather than defaulting to matching the old schema.**
* Creditor Master: a payee registry separate from the transactional tables, covering **Supplier, Lender, Bank Loan, and exactly one Internal Account type: Encashment.** Credit card payees are explicitly excluded — those stay in the separate credit card track (`cc_service.py`). Real legacy data also had Insurance/Consultant/Accountant/Misc payees (1 each) — these default to **Supplier** unless told otherwise; they're functionally just AP payees for non-inventory expenses. **Explicitly not payees anymore:** the legacy internal-control payees for error/spoiled checks, adjustments, and one-off "cancelled check for X" entries — those are exactly the messy patterns the new system's structured mechanisms (spoiled check log, credit memo, cancel/replace workflow) already replace. Don't recreate them as Creditor Master entries.
* App IA: the current "Supplier" area is overloaded/unfocused (legacy system mixes everything into one view). New system should split it into distinct, focused tabs: **Order Statement, Checks, Credit Memo, Invoices** — plus the separate **Creditor Master** tab above.
* Batch = invoices grouped per supplier. One batch can contain multiple invoices and multiple checks. **Every check must belong to a batch — no exceptions.** Legacy data has standalone checks with no batch link (Cash, Encashment, even some real supplier checks) — this is a mistake being corrected, not a pattern to keep. This applies to Encashment too: even a cash-out disbursement goes through create-batch → (invoice-equivalent line for the amount) → issue-check like everything else.
* Two distinct adjustment types, different timing:

  * Credit memo: exists/applied *before* checks are issued — reduces the batch total that checks get cut against.
  * Correction: applied *at the time checks are issued* — a check-level fix, separate from the pre-issuance credit memo.
* Check replacement: due to working capital constraints, checks are often postdated further out than the invoice's actual due date. This routinely leads to checks being voided and replaced/reissued — replacement history needs to be tracked per batch/check.
* **Hard rule, system-wide (not just one track): a check can never be issued, corrected, or left in place for more than what's actually payable** — a supplier batch's net total, a lender installment's principal+interest, or a lender loan's total_payable. Enforced at the backend (`issue_check()`, `issue_checks_grid()`, `apply_check_correction()`), not just a UI hint — and a batch/loan can never be locked while overpaid (`lock_statement()` refuses; correct or cancel a check first). This applies equally to Supplier and Lender checks — confirmed explicitly by the user after an initial ask that sounded lender-specific.
* Batch lifecycle — three named workflows (this is the priority to get right before other edits):

  * **Normal:** (1) select supplier → create batch, (2) input invoices [+payable] and credit memos [−payable], (3) issue checks against net payable, (4) batch is balanced when total payable (net of credit memos) = total checks issued, (5) generate the Batch Statement report, (6) supplier collects. **Lock is automatic** — the batch locks itself once it's balanced and the report has been generated for collection; it is not a manual button in the normal path.
  * **Check replacement** (supplier returns a check): (1) unlock the batch, (2) replace the check(s), (3) regenerate the report, (4) supplier collects.
  * **Invoice correction**: (1) unlock the batch, (2) edit the invoice in place (true in-place edit of the saved row — this overrides the earlier "add-only, no edit" build decision), (3) adjust the checks issued accordingly (a check may need to be replaced to restore balance), (4) regenerate the report, (5) supplier collects.
  * Both exception paths end in a fresh report generation + re-collection — the report is not a one-time artifact, it gets reissued whenever the locked batch changes.
  * Editing an invoice while unlocked must log old value → new value + timestamp automatically (no manual reason required, consistent with the no-friction unlock) — an edited dollar amount on a batch that may already have checks cut against it is exactly the kind of change that needs an audit trail, even in a single-user tool.
* Unlocking is a simple confirmation prompt — "Are you sure you want to unlock this batch?" — no reason/approval step required (single-user, internal tool). Unlock is the gate correction, replacement, and invoice-edit workflows all sit behind.
* One-off checks with no real invoice (not Encashment — Encashment is specifically for withdrawing cash from the bank to fund a cash settlement on another track, e.g. Bank Loan/Credit Card; a one-off check is a real payment to a real payee, just undocumented): still goes through the normal batch flow, no exception. Create a batch under that payee, add one invoice line with a placeholder Doc Ref (e.g. "One-off — reason") for the amount, then issue the check — `invoices.doc_ref` is a plain `TEXT` field with no uniqueness constraint and only non-blocking anomaly warnings, so this already works with zero code changes. Confirmed 2026-09-06 rather than building a separate lighter path.
* Spoiled checks (misprints, jams, etc.): recorded against an internal control payee — "Error Checks Only (for control)" — rather than discarded untracked, so the check number sequence stays accounted for. Amount and date are always null (no real transaction occurred). Not tied to any supplier or batch; this is a check-number-sequence control, separate from batch-level cancel/replace.
* Check history: every check ever tied to a batch is kept on record with its status (issued, cancelled/voided, replaced), forming a full audit trail per batch — not just the current/active checks. Spoiled checks are logged separately under the internal control payee (see above), not as part of a supplier batch's history.
* Check "cleared" status: not a stored field. Implied automatically once the check's date is in the past relative to today — no manual flag, no reconciliation step in this system. (Legacy Access had a manual `Cleared?` checkbox; intentionally dropped as a mistake to correct, not something to replicate.) **2026-09-27 assessment:** this is the system's main accounting-control gap (a bounced or unpresented check still reads as paid) — a Metrobank-statement reconciliation is the recommended next build, not yet agreed. **Built 2026-09-27/28** (Bank Statement Import section): `checks_issued.bank_cleared_date` holds the bank's date; totals still use the check date (show-both rule).
* **Invoice Paid/Partially Paid/Unpaid status requires the covering check(s) to have actually cleared** (`check_date <= today`), not merely been issued — a postdated check doesn't mark an invoice Paid just because it exists. This is deliberately separate from the batch lock/export decision, which stays issuance-based (checks handed to the supplier to collect is what "balanced" means for locking purposes, regardless of clearance) — only the invoice-level status display switched to a clearance basis.
* Batch Statement (aka "Order Statement"): printable record of a batch listing its invoices and checks; printed in two copies — one for internal filing, one for the supplier upon collection.
* Batch total = sum of invoice amounts, less any pre-issuance credit memos applied to that batch. Checks are issued against this net total; corrections and replacements are handled at/after issuance. **Total amount is never a manual input field** — it's always calculated from the invoice lines added to the batch, never typed in directly on the Order Statement creation form.
* Terms: the *agreed* standard set per supplier is **COD, 30, 60, 90, 120** — should be a picklist at the supplier level (`creditors.terms`), not free text. However, real due dates get "leeway" beyond the agreed term on a case-by-case basis (legacy data shows 130/140/150/170+ regularly) — so `DueDate` on an individual invoice line stays independently editable/overridable, it is not strictly locked to `date_received + terms`. The Terms picklist constrains the *agreement*, not the *actual due date*. **Superseded 2026-09-14**: the user clarified Due Date is purely a computed reference field they never edit — it now always equals `date_received + terms` on every save, and the grid's Due Date column is read-only. **Refined 2026-09-27 (bank rule):** whole-month terms (30/60/90/120/150) land on the same day of the month rather than an exact day count (`add_terms_bank_rule()`); existing invoices change only when saved again.
* `order_statements.total_amount` is a denormalized column, still recalculated via `recompute_statement_total()` after every invoice insert/update/delete in the Python code (left in place, now redundant) — **but as of migration `017_recompute_total_trigger.sql` (2026-09-06) it is also kept in sync by a DB trigger** (`trg_invoices_ai/au/ad_recompute_total`) on every `invoices` INSERT/UPDATE/DELETE, so a new code path that mutates invoices directly can no longer let the total silently drift even if it forgets to call the Python helper.
* **Gotcha (found live 2026-09-13): `q()`/`q_live()` (`app.py`, wrapping `engines/db_engine.py`'s `df_query()`) coerce every SQL NULL to `''` app-wide.** A plain `pd.isna()`/`pd.notna()`/`.isna()` check on a column read through either of them is wrong the moment that column can legitimately be NULL (`''` is not NaN, so `isna()` never matches it and `notna()` always does) — this silently broke "is this check unnumbered/unprinted" logic in three places before being caught. Use the `has_value(v)` helper (now in `engines/common.py`, imported into `app.py`; `not (pd.isna(v) or v == "")`) instead of `pd.isna()`/`pd.notna()` on any nullable column that came from `q()`/`q_live()`. Functions that read via raw `sqlite3`/`pd.read_sql_query` (e.g. most of `engines/*.py`) aren't affected — NULL stays NaN there.

## Implementation Backlog

* [x] Full SQLite DDL schema with 4 AP tracks (`schema.sql`).
* [x] Check engine with Metrobank 399 branch code rules. **(Resumed and implemented 2026-09-07 — see the Chrysanth item below for the full design. `engines/check_engine.py`: `generate_metrobank_check_pdf()` prints date/payee/amount-numeral/amount-in-words onto blank check stock, calibrated per bank account. Wired into the UI via a shared `render_check_print_section()` helper in both Order Statement → Checks sub-tab and Lenders → Checks sub-tab.)**
* [ ] Multi-format Vendor SOA Matcher (`soa_parser.py` + `soa_matcher.py`). **(PAUSED — do not resume without explicit go-ahead)**
* [x] Credit Card due date countdown engine & direct cash settlement service (`cc_service.py`).

  * **Re-scoped 2026-09-06** to exactly five things the user said are the only ones that matter: due date, amount due, whose card it is, making sure every statement gets recorded, and an annual fee waiver reminder. Cash2Go/Cash-It-Easy cash conversions — live in code despite already being marked dropped above — actually removed this time: `cc_cash_conversions` table dropped, `engines/credit_to_cash_service.py` deleted outright, its UI section removed.
  * New `cardholders` registry table (name, department, contact number) — "there is a need of credit card holder registry," since the app only tracked which bank issued a card, not who inside the company holds it. `credit_cards` gains `cardholder_id` (nullable FK, existing cards backfilled via a new "Edit a card" form) and `annual_fee_due_month` (nullable, month only — see below).
  * **`cc_statements` dropped `statement_ref` and `statement_date` entirely** ("remove statement reference; no need for this" / "also remove statement date... make this a permanent information of the card i'll just provide the nth day every month"). `statement_ref` was never used beyond being typed in (unlike `order_statements.statement_ref`, used for search/promote/lock elsewhere). `statement_date`'s day-of-month doesn't vary per card, so it moved to `credit_cards.statement_day` (a permanent card property, alongside `annual_fee_due_month`) instead of being re-typed on every statement. `cc_statements` now only carries what actually varies per cycle: `due_date` and the amount. `get_statement_gap_alerts()` switched its recency signal from `MAX(statement_date)` to `MAX(due_date)`. Card-selection dropdowns throughout the tab now show `"{card_name} - {last4}"` for disambiguation, since multiple cards can share a name (e.g. two different cardholders both named their card "Gold").
  * **"Add card" form clears on submit** (`clear_on_submit=True`) — the Cardholder dropdown was staying on whatever was last selected after adding a card, which caused a real live data mistake (a second card meant for a different cardholder got submitted still assigned to the previous one). Fixed the data by hand (single-field correction, confirmed with the user first) and fixed the form so it doesn't happen again.
  * **"Add statement" now asks Cardholder first, then Card** — "1 card holder = many cards," so the Card dropdown is scoped to whichever cardholder is picked first (a plain selectbox outside the `st.form`, since a form's own widgets can't dynamically filter each other before submit — the cardholder picker triggers a normal rerun, the form inside just reads its already-filtered options).
  * **`annual_fee_due_day` added then removed same session** (migration `013_cc_annual_fee_month_only.sql`). The real anniversary is the card's original issuance date, which the user doesn't have on file for existing cards ("i cannot give the issued date") — asked whether to switch to tracking that date instead (more accurate, one field), but since it's not knowable, kept it simple: month-only precision, "annual fee due sometime in March." `get_annual_fee_alerts()` reports `months_until_due`/`when` ("This month"/"Next month"/"In N months") within a 2-month lookahead, not a day-level countdown.
  * `engines/cc_service.py` gained `get_statement_gap_alerts()` (flags a card with no statement at all, or none in the last 35 days — "making sure all credit card statements are recorded") and `get_annual_fee_alerts()` (days-until-due for upcoming annual fee anniversaries within a 60-day window, wrapping to next year once a date has passed). Statements and the Cards table now show cardholder name directly, satisfying "whose card it is" at the point where amount/due date are shown, not just on a separate Cards list.
  * Migration `011_credit_card_simplify.sql` — full `credit_cards` rebuild (new nullable FK + columns) plus a plain `DROP TABLE cc_cash_conversions`. Hit a real SQLite gotcha applying it: dropping `credit_cards` while `v_cc_due_alerts` still referenced it failed with "no such table" because SQLite's non-legacy `ALTER TABLE RENAME` eagerly re-validates every view in the schema, even ones unrelated to the table being renamed — fixed with `PRAGMA legacy_alter_table = ON` around the rename.
  * **Follow-up: "how can i solve forgetting recording credit card... it should be track that remind possible this is credit card statement is overlooked."** `get_statement_gap_alerts()` already existed but only surfaced inside the Credit Cards tab — the same forgetting failure mode could just as easily mean forgetting to open that tab. Surfaced it on the Dashboard (Tab 1) instead, the screen seen every time the app opens: a red `st.error` banner naming every flagged card when any exist, plus a "Possibly missed CC statements" count in the top metrics row alongside the other at-a-glance KPIs. No email/push notification exists in this Streamlit app, so "unmissable on the landing screen" is the practical ceiling within this system.
  * **Follow-up: "drop but dont remove direct cash settlement, for now let be once the due date is pass considered it paid."** Same auto-cleared-by-date convention as everywhere else in this app, but — unlike Bank Loans, where the manual settle button was removed outright — here it's explicitly kept for recording an actual/early payment; only the *default* case (nobody bothers to click through settlement every month) now needs no action. `cc_statements.status` itself is untouched; an `effective_status` is computed wherever it's displayed/queried (`CASE WHEN status IN ('Paid','Cancelled') THEN status WHEN due_date < today THEN 'Paid' ELSE status END`), mirroring `v_effective_checks.effective_status`. `v_cc_due_alerts` (migration `012_cc_due_alerts_auto_paid.sql`) drops the `OVERDUE` bucket entirely (an overdue statement is now auto-paid, not overdue) and excludes any past-due-date row from its `WHERE` clause so it stops alerting forever once the date passes. Dashboard's `ap_outstanding` KPI and the "Direct cash settlement" dropdown both switched to the same past-due-date exclusion, so a statement already auto-considered paid neither inflates outstanding AP nor shows up asking to be settled again.
  * **Follow-up: "hide this"** (Direct cash settlement) — tucked into a collapsed `st.expander` rather than an always-open container; still fully usable, just not taking visual priority now that it's a fallback path rather than the default way statements get settled.
  * **Credit Cards module accepted 2026-09-06** — user: "ok i am ok for now the credit cards module." No known deferred gaps raised at this checkpoint.
  * **Follow-up (2026-09-14) — card dropdown shows issuer; tab UI decluttered.** Two small fixes: (1) the "Add statement"/"Edit a card" card dropdown now reads `f"{card_name} - {card_number_last4} ({issuer})"` instead of just name+last4, disambiguating once a cardholder has 2+ cards from different banks (via the existing `creditors.creditor_name` join, aliased `issuer` — no schema change). (2) User: "fix the credit cards tab, fix UI. it is scattered" — reorganized from one long scroll (setup expanders, cards table, edit form, two separate alert sections, statements table, add/settle forms, all interleaved) into: a "Needs attention" box up top (statement-gap warnings + annual fee reminders together, surfaced first like the Dashboard does) followed by two sub-tabs — **Statements** (the statements table, Add statement, Direct cash settlement — daily-use actions) and **Cards & cardholders** (cards table, Edit a card, Add issuer/card, cardholder registry — setup, used rarely). No logic changed, purely a reflow of existing pieces into clearer groups. One thing preserved carefully: "Direct cash settlement" stays inside a collapsed `st.expander`, not a plain container — per the explicit "hide this" decision two bullets above (it's a fallback path, not the default way statements get settled), which the first draft of this reorg almost undid before being caught and fixed.
* **Dashboard: Payment calendar with grey-out dates (2026-09-14).** User: "everytime i issue/park i always look to the all tracks schedule of payments... in current system i need to generate everytime." Went through three explicitly-compared UI approaches before landing here — full detail in `MEMORY.md`'s 2026-09-14 entries. The one kept: a calendar heatmap on the Dashboard (`get_payment_schedule_daily_totals()`, already existing) — darker cell = more payable that day, hover for the exact total, click a day to open an itemized breakdown below (`get_payment_schedule_all_tracks()` filtered to that date) plus a mark/unmark control. Two sibling attempts (a sidebar date-lookup, and an inline hint on the Order Statement issue-check grid) were built, compared live, and explicitly superseded once the calendar won — code removed, not just abandoned in place.
  * **Grey-out dates**: new `calendar_blocked_dates` table (migration `024_calendar_blocked_dates.sql`) — `blocked_date, label, kind ('holiday'|'custom')`. Seeded with fixed/computable Philippine regular holidays for 2026-2027 (New Year's, Maundy Thursday/Good Friday via the Easter algorithm, Araw ng Kagitingan, Labor Day, Independence Day, Ninoy Aquino Day, National Heroes Day as the actual last Monday of August, Bonifacio Day, Christmas, Rizal Day). **Deliberately not seeded**: Eid'l Fitr/Eid'l Adha — lunar-sighting + presidential-proclamation dates with no reliable formula; add those (or anything else missed) via the same mark-a-date control once announced. Marking/unmarking happens in the click-breakdown panel, not by overloading the click itself (click already means "show breakdown").
  * **Two real bugs caught during live testing, not shipped blind**: (1) Vega-Lite's click-selection returns a temporal field as milliseconds-since-epoch in UTC — converting that back to a date landed one calendar day off the actually-clicked cell (a real UTC/local timezone mismatch, confirmed live: click the 15th, get the 14th). Fixed by selecting on the plain integer `day` field instead and reconstructing the date from the already-known displayed month/year, avoiding the round-trip entirely rather than fighting timezone math. (2) Altair 6 dropped support for nesting `alt.condition()` calls (`_Conditional() got multiple values for keyword argument 'condition'`) — caught immediately by the `AppTest` clean-render check this project always runs before going live, not discovered live. Fixed with the current chained `alt.when().then().otherwise()` API.
* **Reports tab (Tab 7): redesigned from 2 always-open reports into a 9-report picker (2026-09-14).** User: "i am overwhelmed by the report tab. I prefer it is focus only on generating reports," then, asked to elaborate: "all kinds of report that can be extracted from the system like (all batches or checks or invoices related to a supplier, lender, bank loan, statement of credit cards. you ask more." Scoped via `AskUserQuestion` before writing code (per this session's standing pattern) — user picked: a single "Report" dropdown showing one report's filters/table/exports at a time (replacing the two always-open bordered containers), all 4 per-entity drill-down reports (supplier, lender, bank loan, credit card — pick one entity, see its full history), plus the existing master rollups (supplier AP aging, private lender exposure, bank loan servicing) surfaced here for the first time; a "check register" option was offered but not chosen. See Phase 4 section below for the full report list and technical detail.
* **Follow-up same day: real flaw in the drill-downs' summary metrics, plus a broader redesign.** User flagged, from a live screenshot of a Cancelled supplier batch: the "Total invoiced"/"Total paid" metric cards summed every row regardless of status, so a fully Cancelled batch (Cancelled invoices, Cancelled/Replaced checks) still showed ₱34,000 invoiced / ₱38,000 paid. Then reframed the whole feature's purpose: "the purpose of these reports are whenever i am asked for history of transactions. plus it is restricted to me make it more user-parameters friendly" — plus, asked what parameters to add: "include pdf reports just a simple reports (like it is a simple statement of account, for example in order statement combine all batches (make it have an option) and like." See the matching Phase 4 entry below for the fix and the 4 new date-range/status/PDF features this drove across all 4 drill-downs.
* ~~Multi-bank Cash2Go/Cash-It-Easy cash injection service (`credit_to_cash_service.py`).~~ **(DROPPED — out of scope; actually removed 2026-09-06, see Track 4 note above — this bullet predates the real removal and is kept only as history.)**
* [x] ReportLab dual-copy PDF export engines. **(VERIFIED: `build_dual_copy_check_pdf` only prints the check face — it does NOT satisfy Batch Statement export. Built a separate `engines/batch_statement_service.py` for that.)**
* [x] Streamlit multi-tab web application (`app.py`).
* [x] Batch Statement export: per-supplier batch, listing all invoices + checks in that batch, printed dual-copy (filing copy + supplier copy). Toggle option at print time: show full check history (incl. cancelled/replaced) vs. active-only checks. (`engines/batch_statement_service.py`, Order Statement tab → "Batch Statement export")

  * Deviation (2026-09-05): dual-copy is no longer the only option — a "Copies to print" choice (Both / Filing only / Supplier only) was added since printing is often needed for one side only. Defaults to Both, unchanged for existing callers.
  * Addition (2026-09-05): a second, separate 80mm thermal-printer format (`generate_batch_statement_thermal_pdf()`) was added for a thermal roll printer (80mm wide, \~3276mm max continuous length) — a single continuous strip auto-sized to content, not the Letter-size layout. Single copy only; same auto-lock-on-balanced behavior as the Letter export.
  * **Addition (2026-09-14): opt-in "Payment Application" ledger, built on the same-day per-invoice check allocation feature.** User, looking at a real generated statement: "in the report, add an option to include how the payments are applied and include running balance." First draft added an "Applied To" + "Balance" column directly onto the existing Checks table — user redirected mid-build: "i think is it better to have additional table? invoices to the left then cehcks are applied then there is running balance." Rebuilt as a separate **new** "Payment Application" section (Letter-size PDF only, not the thermal format) below the unchanged Checks table: one row per (invoice, check) application — Invoice | Check Number | Check Date | Amount Applied | Balance — in the order checks were issued, running balance starting at the batch's net total and decrementing by each row's applied amount. Only **Active** checks are included (a Cancelled/Replaced check's money never left, so it doesn't belong in a "how it was paid down" ledger — it still shows in the plain Checks table above, unaffected); a check issued before per-invoice tracking existed (or on a batch that's never had an allocated check) contributes one "Not itemized" row for its full amount, so the ledger still reconciles to the real balance either way. New `generate_batch_statement_pdf(..., show_payment_application: bool = False)` param, off by default — a new toggle in the Order Statement tab's "Export Batch Statement" expander, next to the existing full-history toggle. Verified against two real batches: "Elle" (₱4,000 net, one check split across 2 invoices plus a check spanning into a still-uncleared future date, correctly showing "Partially Paid" on the invoice whose full allocation hasn't cleared yet) and "Pia Supply" (₱6,000 net, one check fully covering 3 invoices) — both ledgers ran down to exactly PHP 0.00, and both PDFs plus the live UI toggle were exercised with no errors.
* [x] Credit memo deduction: apply pre-issuance credit memos to reduce the batch total *before* checks are cut for the net amount. (`credit_memos` table + `v_batch_net_totals` view; Order Statement tab → Credit Memo sub-tab)
  * **Addition (2026-09-14): Credit Memo Deduction Guard (migration 026).** A credit memo silently reduces the batch's net total the moment it's added — this guard requires an explicit human "Confirm" (or removing the memo) before checks can be issued, so that reduction doesn't go unnoticed. New `order_statements.credit_memos_confirmed` column, reset to 0 whenever a memo is added or an existing one's *amount* changes (a ref/date/reason-only edit doesn't move the net total, so it's left alone); auto-set back to 1 if a delete leaves the batch with zero memos. `get_batch_memo_status()`/`confirm_credit_memos()` (`app.py`, alongside the other credit memo functions — no separate `batch_service.py` exists in this project). UI: a warning banner + "✓ Confirm"/"Remove memo" in Order Statement → Checks, right before "Park Metrobank checks," which is `disabled` while the guard is blocking. **Deviation from the original ask**: `delete_credit_memo()` stays a real (hard) delete rather than a soft-delete — this app's only precedent for "keep history after removal" is a status field (checks, invoices), never a soft-delete flag, and a soft-deleted memo would also need `v_batch_net_totals` updated to keep excluding it, which is a second schema-adjacent change beyond this one feature. 6 tests (`tests/test_credit_memo_deduction_guard.py`, one more than requested — added a delete-clears-the-guard case). Verified live: a real test batch + memo showed the banner with the right numbers and a visibly disabled Park button, Confirm cleared both, batch deleted afterward.
* [x] Batch lock/unlock: batch locks automatically once checks are issued; unlock via a simple "Are you sure?" confirm dialog — must happen before any correction or replacement can proceed. (`order_statements.is_locked`; Order Statement tab → "Batch lock / unlock")
* [x] Check correction handling: apply check-level corrections at the point checks are issued (distinct mechanism from pre-issuance credit memos), gated behind unlock. Action lives inside the Order Statement view, not a separate screen. (`check_corrections` table; Order Statement tab → Checks sub-tab, next to that batch's check list)

  * Deviation (2026-09-05): Check Number is now correctable through this same grid (previously locked, forcing Cancel+Replace for a mere typo). Still fully audited via `check_corrections`/`audit_logs`, still gated behind unlock, still enforces the same check-number uniqueness rule Replace already enforced.
* [x] Check cancel/replacement workflow: void + reissue checks, common when postdating (working capital driven) pushes a check date past the original due date; track old→new check linkage per batch, gated behind unlock. Cancel and replace actions must be done from within the Order Statement view (next to that batch's check list), not a standalone/separate screen. (`checks_issued.replacement_check_id`/`replacement_reason`/`replaced_at`; Order Statement tab → Checks sub-tab)
* [x] Check history log: per-batch record of every check ever issued against it — status of each (issued, cancelled/voided, replaced) — full audit trail, not just current active checks. (Order Statement tab → Checks sub-tab table; Lenders tab → Checks sub-tab table)

  * **Update (2026-09-06):** the duplicate cross-batch view that used to also live in the Checks & checkbook tab (an active/full toggle over all checks) was dropped at the user's request in favor of a planned global search bar — see Phase 4-adjacent UI note below. Per-batch history remains the one documented home for this, consistent with this file's own rule that cancel/replace and its history belong inside the batch view, not a standalone screen.
* [x] Surface check history in the UI: the "Issue check" screen currently shows no record of checks already issued against the selected Order Statement after encoding — it must display the already-issued list (from the check history log) right there, not just a blank issuance form. This is the same data that should populate the "Checks" tab under the Supplier area. (Order Statement tab → Checks sub-tab, updates immediately via `refresh()` after issuing)
* [x] Scope invoice lists strictly per batch: any invoice list shown while working within an Order Statement must only show invoices belonging to that batch — no cross-batch leakage. (Order Statement tab → Invoices sub-tab, "Invoices in this order statement", `WHERE statement_id = ?`)
* [x] Batch/Order Statement browser: a list/table view of all existing batches (filterable by supplier, date, issued status) that lets you open and navigate between them. (Order Statement tab → "Order statement browser": filters + click-to-open row selection, backed by `st.session_state["selected_statement_id"]`)
* [x] Spoiled check handling: route misprinted/jammed checks to the internal "Error Checks Only (for control)" payee, amount and date always null, so the check number sequence stays accounted for; keep this log separate from per-batch/supplier check history. (`spoiled_checks` table; Checks & checkbook tab → "Log a spoiled check")
* [x] Checkbook gap audit: register a checkbook's leaf range (e.g. 1-100), and skipping a number when issuing checks (e.g. 1, then 3, skipping 2) flags the skipped number as a gap. (`engines/checkbook_service.py`, `audit_checkbook_gaps()`; Checks & checkbook tab → "Checkbook gap audit")

  * **Bug fixed 2026-09-06**: crashed outright (`ValueError: DataFrame index must be unique`) if two different check_number strings represented the same leaf (e.g. "1" and "01") — a realistic mistake now that check numbers are fully manually typed, not auto-sequenced. Now detects and flags this as a separate "CRITICAL: DUPLICATE CHECK NUMBER" state (with both conflicting text values shown) instead of crashing, alongside the existing missing-gap detection — surfaced in the UI as a new "Duplicates" metric next to "Missing gaps."
  * **Follow-up: "i just want to properly account all checks. i should be warned or notified otherwise."** The audit itself was correct (once fixed) but purely manual — nothing prompted anyone to actually run it. Same fix as the credit-card statement-gap reminder: new `get_checkbook_alerts()` runs the gap audit for every Active checkbook and surfaces any with a real problem (gap or duplicate) as a red banner on the Dashboard (Tab 1), plus a "Checkbooks with issues" count in the top metrics row — unmissable on the screen seen every time the app opens, not buried behind a manual "Run gap audit" click. Considered (and rejected, per explicit user agreement) merging checkbook registration with a full per-leaf check register — the existing derive-on-demand audit already answers the actual need ("properly account for checks") without the overhead of pre-creating and maintaining a status per leaf.
  * **Follow-up: "checks and checkbook please organize the UI."** The tab had grown into one long flat scroll mixing two distinct concerns: checkbook management (inventory, register, gap audit, spoiled checks) and check-ledger operations (issued ledger, cancel, reprint, batch history). Split into two native `st.tabs()` sub-tabs — "Checkbook" and "Checks" — mirroring the same focused-sub-tabs precedent already used for Order Statement's Invoices/Credit Memo/Checks split, rather than inventing a new UI pattern. Pure reorganization: every existing widget, form, and query moved as-is into one sub-tab or the other, nothing added/removed/renamed. Verified live in the browser (both sub-tabs render, all sections — inventory, register, gap audit, spoiled checks / ledger, cancel, reprint, batch history — work correctly) plus `AppTest` render pass clean.
  * **Follow-up, same session: "drop checks in checks and checkbook. because we will make global search bar."** The "Checks" sub-tab just added above is now removed entirely — the tab has gone back to a single flat view (Checkbook inventory, Register, Gap audit, Spoiled checks), no sub-tabs, since only one section remains. Before dropping, checked each piece for a surviving equivalent elsewhere: **Cancel a check** and **Check history by batch** are safe to drop since Order Statement's own Checks sub-tab (and Lenders' own Checks sub-tab) already provide per-batch cancel/void/replace and history, which is the *documented* correct home per this file's own rule ("Cancel and replace actions must be done from within the Order Statement view... not a standalone/separate screen") — the dropped copy here was actually a rule-violating duplicate, not a unique capability. **Re-print dual-copy check PDF** is different: `build_dual_copy_check_pdf()` had exactly one caller in the whole app (this section) — confirmed via `grep` before asking. Flagged this to the user via `AskUserQuestion` rather than assuming; user chose to drop it too (recommended option), accepting that single-check PDF reprint is unavailable until the planned global search bar (or another spot) covers it. `build_dual_copy_check_pdf()` itself is left defined in `app.py` (unused for now, not deleted) since it's a small, real capability likely to be wired back in once the search bar exists. Verified live: tab now shows only Checkbook content, no leftover sub-tab bar. `AppTest` render pass clean.
* [x] Chrysanth Check Writer integration: check engine output must match Chrysanth's print/format requirements. **(Implemented 2026-09-07 — user supplied a real Chrysanth sample check PDF + its Options dialog (Printer/Alignment/Font/Style/Locale tabs), resolving the two open design questions: (1) direct PDF generation, not a CSV export for Chrysanth to import — confirmed via `AskUserQuestion`; (2) the real sample gave enough reference to build against, replacing the old placeholder-coordinate draft. MICR generation and the "A/C PAYEE ONLY" crossing are explicitly out of scope — both are pre-printed on the physical check stock by the bank, not something this software draws (same reasoning as the check number, which was already correctly left off). The one blocker that's still genuinely open — no physical Metrobank leaf has been measured — is handled via calibration, not guessing: `bank_accounts` gained `check_width_mm`/`check_height_mm`/`check_offset_x_mm`/`check_offset_y_mm` (migration 019), exposed as an Admin & Audit → "Metrobank Account 1" → "Check print calibration" form, mirroring Chrysanth's own per-account Alignment tab (shift right/down after a real test print). See MEMORY.md for the full design derivation.)**
* [x] Creditor Master tab: master registry of payees tagged supplier/lender/bank loan (excludes credit card, which stays in its own track). New top-level tab; unified add/list/filter/terms-update across all three types; removed the now-redundant per-type add-forms from Suppliers/Lenders/Bank loans tabs.
* [x] Restructure the Supplier area into focused tabs: Order Statement, Checks, Credit Memo, Invoices — replacing the current messy all-in-one view. Implemented as a hub-and-sub-tabs layout: "Order Statement" is the top-level tab (batch browser, create/promote, lock/unlock, export); Invoices/Credit Memo/Checks are nested sub-tabs that all follow whichever batch is currently open via `st.session_state`.

  * **Follow-up (2026-09-06): "UI concern, it is possible to hover a tab... i can choose enter data, or navigating a batch. recommend a UI setup our goal is simplicity yet impactful"** — Streamlit's `st.tabs()` has no native hover-dropdown; the idiomatic equivalent is a widget at the top of the tab body. Applied only to Order Statement (the one hub tab explicitly asked about, not proactively extended to Lenders/Bank Loans): an `st.segmented_control("What do you want to do?", options=["🔍 Browse Batches", "➕ New Batch"], key="os_mode")` at the top of the tab replaces the old always-visible browser + separate "Other actions" expander with two mutually-exclusive modes — Browse (statement filters/list + "Promote statement status" moved into an expander here) vs. New Batch (just the create-draft form).
  * Hit a real Streamlit constraint building this: `st.session_state["os_mode"] = ...` cannot be set after the `os_mode` widget has already been instantiated in the same script run (`StreamlitAPIException`), which is exactly what happens when a successful "Create draft" tries to switch the mode back to Browse — caught via live browser testing (a manual click-through reproduced it; `AppTest`/`ast.parse` didn't). Fixed with the standard staging-key pattern: the success handler sets `st.session_state["_os_mode_next"]` instead, consumed and applied to `os_mode` at the very top of the tab, before the real widget renders, on the next rerun. Re-verified live afterward: Create draft no longer errors, mode auto-switches to Browse Batches, and the new batch is open below.
  * **Follow-up: "do for the other tracks."** Applied the identical Browse/New segmented-control pattern (plus the same `_<x>_mode_next` staging-key fix, built in from the start this time rather than discovered by a crash) to the two other hub-style tabs: Lenders (`ln_mode`: "🔍 Browse Loan Notes" / "➕ New Loan Note") and Bank Loans (`bl_mode`: "🔍 Browse Bank Loans" / "➕ New Bank Loan", with the "Loan beneficiary registry" expander moved inside New Bank Loan mode alongside the create form). Renamed the Bank Loans form's local `bl_mode` variable (the "Mode of payment" text input) to `bl_mode_of_payment` to avoid colliding with the new `bl_mode` segmented-control variable name — same identifier, different meaning, caught by `grep`ping for `bl_mode\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\b` before considering the change done.
  * Verified live end-to-end for both: created a test loan note and a test bank loan through New mode, confirmed no `StreamlitAPIException`, confirmed the mode auto-switched back to Browse and the new record was open/selected below, then deleted both test records via their existing delete flows to leave the live DB clean.
  * **Real pre-existing bug found (not introduced by this change) and left unfixed, flagged for later**: `create_loan_note()`'s form has no validation preventing a ₱0.00 principal from being submitted, which then fails the database's `CHECK(principal_amount > 0)` constraint with an unhandled `sqlite3.IntegrityError` shown raw to the user. The old UI had the identical gap (same unconditional `create_loan_note(...)` call once a Lender is picked) — this predates the toggle restructuring and is a Phase 2 form-validation gap, not a regression from this UI work.
* [x] Fix Order Statement creation form: remove the manually-inputtable "Total amount" field. Draft is created from Supplier selection alone (starts empty); total is a read-only field that recalculates as invoice lines are added to the batch. (`recompute_statement_total()`, called after every invoice insert)
* [x] Fast multi-invoice entry: adding invoices to a batch must support a spreadsheet-style grid (multiple editable rows entered in one flow, tab/keyboard-driven), not a one-at-a-time modal/form per invoice. The legacy Access datasheet grid is the speed bar to match or beat. (`st.data_editor`, Order Statement tab → Invoices sub-tab → "Add invoices to a batch")
* [x] AI-assisted invoice entry from photos/PDF scans, to reduce manual encoding at high supplier/invoice volume: upload one file per invoice, best-effort extraction of Doc Ref/Date/Amount, staged into an editable review grid — nothing saved until confirmed, then goes through the same `add_invoice()` path as manual entry. (`engines/invoice_ocr_service.py`; Order Statement tab → Invoices sub-tab → "Extract invoices from images/PDFs (local OCR)")

  * Note: originally scoped as Claude vision (Haiku 4.5) for accuracy across varied supplier layouts, but switched to fully local OCR (Tesseract + pdfplumber) before shipping — user explicitly declined the small ongoing per-call API cost. Requires the Tesseract OCR engine installed on the machine (separate from the `pytesseract` Python package); accuracy on photographed/scanned invoices is lower than the cloud-vision approach would have been, hence the extraction result always lands in a review grid rather than auto-saving. Digital (text-layer) PDFs bypass OCR entirely via `pdfplumber` and are extracted essentially perfectly.
  * Deferred decision (2026-09-05): Claude vision was scoped and budget-confirmed ($5.30 credit is more than enough) but the user wants to wait until the entire system is finished before adopting it — this is sequencing, not a rejection. Do not proactively re-propose it; revisit only when raised again or the system is declared done.
* [x] Bulk invoice import from a spreadsheet (CSV/Excel), as a third invoice-entry mode alongside manual entry and local OCR — more reliable than OCR since it's parsing real structured data instead of a photo. Shares the same review-staging grid as the OCR path (unified into one "Add invoices from a photo, PDF, or spreadsheet" expander with two tabs). (`engines/invoice_import_service.py`; Order Statement tab → Invoices sub-tab)

  * Flexible header matching (case/whitespace-insensitive synonyms for Doc Ref/Date/Amount/Less %) rather than requiring exact column names. A row missing a field gets a default (today's date / ₱0) plus a per-row warning rather than being silently dropped, consistent with the OCR path's graceful-degradation contract. Fully blank trailing rows (common in exported sheets) are skipped, not flagged as errors.
* [x] Enforce every check belongs to a batch: add a NOT NULL constraint (or equivalent) on the check's batch/order-statement reference — no standalone check path anywhere in the new system, including Encashment. **(migration `018_checks_batch_not_null.sql`, 2026-09-06 — `CHECK(statement_id IS NOT NULL OR loan_note_id IS NOT NULL)` on `checks_issued`, full table rebuild since `checks_issued` has two valid batch parents depending on track, not one. Confirmed zero standalone rows existed in the live DB before adding it. See MEMORY.md for full verification.)**
* [ ] Update Creditor Master to the real type set: Supplier, Lender, Bank Loan, Internal Account (Encashment only). Remove/reject any attempt to add other "internal account" style payees (error checks, adjustments, one-off cancelled-check entries) — those route through the existing structured mechanisms instead.
* [x] Make Terms a picklist at the supplier level (COD/30/60/90/120) instead of free text, while keeping each invoice's DueDate independently editable for negotiated leeway beyond the standard term. **(Stale checkbox fixed 2026-09-05 — this was actually implemented earlier: `SUPPLIER_TERMS_OPTIONS`, `app.py`, used in both Creditor Master add/update-terms forms; DueDate stays independently editable as specified.)**

  * **Follow-up (2026-09-14): "Why due date is not uldated when i edit the default date."** DueDate's smart default was static (computed once from `date.today() + terms` when a row was created/rendered) and never reacted to a later edit of Date Received. First implementation kept DueDate overridable (recompute unless the user also edited DueDate that round), but the user then clarified their real preference and **this supersedes the "DueDate stays independently editable" line in the checkbox item above**: they never edit DueDate at all and want it purely computed. **Final behavior**: `resolve_due_date(invoice_date, terms)` (`app.py`) unconditionally returns `invoice_date + terms` — no override path, no prior-value comparison. Applied at Save time (not live-while-typing — a real Streamlit 1.62.0 limitation: a server-side session_state mutation doesn't propagate into `st.data_editor`'s returned dataframe for an already-mounted grid, confirmed by checking the saved DB row after a live attempt) on the Invoices grid (both new-row and existing-row paths) and the OCR/spreadsheet staging commit. The Due Date column is now `disabled=True` (read-only) in both grids, labeled "Due Date (computed, recalculated on save)" — typing into it would be silently discarded anyway.
* [x] Implement the three-workflow batch lifecycle: (1) Normal path auto-locks on balance + report generation, no manual "promote"/"lock" button in the happy path. (2) Check replacement path: unlock → replace check → regenerate report. (3) Invoice correction path: unlock → true in-place edit of the saved invoice row → adjust/replace checks to restore balance → regenerate report. (4) Invoice edits while unlocked auto-log old→new value + timestamp, no manual reason field. **(Stale checkbox fixed 2026-09-05 — all four sub-items confirmed already implemented in prior work: auto-lock-on-export logic, `replace_check()`, the unified invoice CRUD grid's in-place `edit_invoice()` path, and its automatic audit logging.)**

## Phase 2 — Private Lender: Loan Notes (Repayment Schedule)

Scoped 2026-09-05 against two of the user's real lender spreadsheets (their actual LN reference documents, not a hypothetical design) — see MEMORY.md for the full derivation. Key facts, since there's no legacy Access reference for this phase the way Phase 1 had:

* Interest is **not** a flat total agreed upfront — it's a fixed **monthly rate** (`loan_notes.monthly_interest_rate`, e.g. 2%) applied to the **declining outstanding balance**. `total_interest`/`total_payable` are computed from the schedule, never typed in.
* The schedule is built **progressively** (due date + principal decided as real payment commitments are made, not fully planned at signing) — matches how invoices/checks work, not a pre-generated amortization table.
* No placeholder "day-zero" row in `loan_installments` — `loan_notes.issue_date`/`principal_amount` themselves are the day-count anchor for the first real installment's interest (`suggest_installment_interest()` falls back to them when the schedule is empty). A visible placeholder row was tried first (matching the blank-Principal/Interest first line of the user's reference sheets) but removed same day — it displayed as a "Paid" installment for money going *out*, not a payment collected, which read as confusing/wrong rather than helpful.
* Interest per row is a **suggested** value (`suggest_installment_interest()`: rate × outstanding-balance-before × days-since-previous-row ÷ 30) but always freely editable — some lenders accept a flat monthly amount instead of day-proration for a clean \~30-day span; this isn't a special mode, just an editable cell, same principle as every other "smart default, stays overridable" field in this app.
* A check links to one specific installment (not a generic balance paydown) — `loan_installments.check_id`, flips that row's status to `Active` (same word `checks_issued.status` already uses for a live/uncancelled check — not `'Paid'`, since this app never claims a check has actually cleared, only that one is currently issued against it). Cancel/replace on that check correctly unlinks/retargets the installment (`unlink_installment_for_check`/`retarget_installment_check`, `engines/loan_schedule_service.py`).
* Loan note status: Settled fires automatically once Outstanding (uncleared) actually hits ₱0 — every installment needs a check issued against it *and* that check needs to have actually reached its date (`check_date <= today`, same "cleared" convention as `v_checks_ledger.effective_status`). A postdated/uncleared check does not count as settling the loan, even though it's `Active`. Defaulted stays a manual override (business judgment, not derivable).
* "Record lump-sum payment" is not a separate mechanism — it's a schedule row whose principal equals the current outstanding balance, closing it to ₱0; the UI just pre-fills that value via an explicit button for discoverability.
* Printable Loan Note / MOU report (`engines/loan_note_service.py`, `generate_loan_note_pdf()`) mirrors the user's actual reference document layout: LN No./Lender/Date/Principal/Monthly Interest header, a Loan Schedule vs. Payment Schedule vs. Difference reconciliation, the repayment schedule table, and the checks-issued table.
* Verified the suggested-interest formula against both real examples: LN No.4 matched exactly on 4 of 5 rows (the 5th was the lender's own manual flat-rate simplification for a clean \~31-day span, expected to differ and exactly why the field stays editable); LN No.3 turned out to be a different lender's flat-rate-only convention throughout (not a formula bug — confirmed by testing, see MEMORY.md) and simply overrides the suggestion on every row, which is exactly the designed behavior.
* [x] Loan note repayment schedule engine + UI: progressive schedule entry, suggested-but-editable interest, computed balance/totals, auto-Settled status, lump-sum support, PDC issuance linked to a specific installment (with cancel/replace correctly unlinking/retargeting), Loan Note/MOU PDF export. (`engines/loan_schedule_service.py`, `engines/loan_note_service.py`, `migrations/005_loan_installments.sql`; Lenders tab)
* [x] LN No. draft-placeholder + promote flow (mirrors Order Statement refs exactly: `DRAFT-<timestamp>` at creation → "YYYY - LN XXXX" on promotion), check numbers for Lender PDCs made fully manual (checkbook auto-sequencing dropped — "not yet on that stage," matches how supplier checks already work), and schedule import from CSV/Excel (`engines/loan_schedule_import_service.py`, "Import schedule from spreadsheet"). An auto-generate-N-even-installments mode was also built and tested working, then explicitly dropped same day at the user's request ("it's complicated... i'll use the import method") — the function was deleted outright, not just hidden.
* **Bugfix (2026-09-15): `create_loan_note()` crashed live** — user hit a raw, uncaught `sqlite3.IntegrityError: CHECK constraint failed: principal_amount > 0` clicking "Create loan note" with Principal left at its untouched 0.0 default. The function had no validation of its own (unlike its sibling `edit_loan_note_terms()`, which already checked this correctly) and returned a bare `int` instead of the `(bool, message, id)` pattern used everywhere else in this app. Fixed to validate internally and return that tuple; the UI now shows a clean error instead of a crash. Same audit turned up the identical unguarded pattern in `add_credit_card()`/`edit_credit_card()` (`credit_cards.credit_limit` has the same kind of CHECK constraint, and the "Edit a card" form had no client-side guard the way "Add card" did) — fixed both the same way. `create_bank_loan()`'s `principal_amount` has no CHECK constraint at all, so it was never actually crash-prone — left alone. 5 new tests (`tests/test_loan_notes.py` new, 2 added to `tests/test_credit_cards.py`); full suite 75/75.
* [x] Hard payable ceilings, system-wide (Supplier and Lender checks alike): a check can never be issued or corrected for more than what's actually payable (batch net total / installment principal+interest / loan total_payable), and a batch/loan can never be locked while overpaid. Enforced at the backend (`issue_check()`, `issue_checks_grid()`, `apply_check_correction()`, `lock_statement()`), not just a UI hint. Surfaced and fixed a real pre-existing bug in the process: the auto-lock-on-export logic had been treating "overpaid" as "covered" (a negative remaining balance satisfied `<= 0.005`), silently auto-locking overpaid batches.
* [x] Loan note delete/cancel + status filter: a draft/note with zero checks issued can be hard-deleted outright (`delete_loan_note()`); once it has checks, only `cancel_loan_note()` applies (soft-cancel mirroring `cancel_order_statement()` exactly — cancels every active check, restores bank balances, marks the note permanently Cancelled). Required a schema migration (`migrations/006_loan_note_cancel_status.sql`, full table rebuild since SQLite can't ALTER a CHECK constraint) to add `'Cancelled'` to `loan_notes.status`, which Order Statements already had from day one but Loan Notes didn't. Loan Notes browser also gained a status filter multiselect (Active/Settled/Defaulted/Cancelled), mirroring the Order Statement Browser's filter pattern.
* [x] Cleared-vs-issued distinction, extended from Loan Notes to Order Statements too: invoice Paid/Partially Paid/Unpaid now requires the covering check(s) to have actually reached their date (`check_date <= today`), not merely been issued (`sync_invoice_payment_status()`) — a postdated check no longer marks an invoice Paid prematurely. Deliberately left the batch lock/export decision issuance-based (checks handed to the supplier to collect, per the documented normal-path workflow) — only invoice-level status switched to a clearance basis; confirmed via explicit scope question before touching Phase 1 again. Order Statement browser also gained the same "Outstanding (uncleared)" column as Loan Notes.
* [x] `edit_loan_note_terms()`: Principal/Monthly Interest Rate can now be edited after creation (only while Active) — the one real gap found auditing this phase before declaring it done, mirroring how Phase 1's "I'm ok with it" checkpoint surfaced two schema gaps. Guarded by the same negative-balance check as a normal installment edit (can't drop principal below what's already scheduled); existing installment rows' own interest values are untouched by a rate change, only future suggestions pick up it.

**Phase 2 accepted 2026-09-06** — no known deferred gaps (unlike Phase 1's two, this audit found one real gap and it was fixed immediately rather than deferred).

## Phase 3 — Bank Loan: Monthly Dues

Scoped 2026-09-06 against a real Metrobank "Payment Plan" amortization schedule PDF (a sample layout the user provided to establish the real column shape, not this company's own loan — see MEMORY.md for the full derivation and the `AskUserQuestion` decisions behind each point below). This phase looks structurally different from Phase 2, not a copy of it:

* **Just Due Date + Amount per installment — no computation, no breakdown.** Two rounds of correction landed here: first "there is no computation needed here just help of inputting the data given by bank" (dropped the amortization-formula idea entirely), then "actually i need only the Due date and amount" (dropped Principal/Interest/MRI/FIP/Accrued Interest/Running Balance too — the full real-document column set from the first implementation was more than actually wanted). `bank_loan_installments` is just `due_date` + `amount`.
* **"Paid" is not a stored field.** Same standing convention as a check's cleared status elsewhere in this app (see Core System Configuration above, and `v_checks_ledger.effective_status`): a due date is automatically treated as paid/cleared once it's in the past (`due_date <= today`), computed at query time — no manual status toggle, no `paid_date` field. User: "paid status is the same, be cleared automatically once the date is past the due date."
* **Two data-entry mechanisms, both pure entry:**

  * **Manual grid entry** — a single unified `st.data_editor` (add/edit/delete together, like the Invoices/Credit Memo grids) for typing each row's Due Date and Amount straight off the bank's paper/PDF.
  * **Spreadsheet import** — bulk-paste the whole schedule at once from an Excel copy of the bank's document, for when typing 60 rows by hand isn't worth it.
  * Both land in the same schedule table — which mechanism entered a row isn't tracked as a separate data shape, and rows stay freely editable afterward (a cleared/past row can still be edited, just not deleted — deleting a row already treated as paid needs the row corrected in place, not removed).
* **No DRAFT-ref/promote flow.** The loan gets its real reference number directly at creation — there's no meaningful draft period, since the whole schedule arrives complete in one import rather than filling in over multiple sessions the way Order Statements/Loan Notes do.
* **Settlement is untracked cash by default — confirmed explicitly, not assumed.** Paying a due date does **not** go through `checks_issued`, a batch, or the Encashment internal-account pattern (even though CLAUDE.md's own Encashment rule would have suggested that route) — per the point above, it's simply the row's `due_date` passing. No check of any kind is involved in this phase.
* **The bank's own "Mode of Payment" field (e.g. "ADA") is stored as a single informational field on the loan header only.** It describes what the bank's paperwork says, not what actually happens — it drives no tracked transaction, no settlement logic, nothing code-path-relevant.
* **MRI renewal, Principal/Interest/MRI/FIP breakdown, and formula-based generation were all scoped at various points and then explicitly dropped** in favor of the Due Date + Amount shape above — see MEMORY.md for the full back-and-forth; this section only describes the final, current shape.
* **The existing pre-phase-discipline stub is being replaced, not extended.** `bank_loans`/`bank_amortization_schedules` (`schema.sql`) and their UI (`app.py`, roughly lines 1167-2690 — create loan, manual per-row entry, an ADA-only "Settle installment" button, servicing report) predate both this scoping conversation and the phased-discipline pattern; they don't have the real document's columns and hard-assume ADA settlement, which this session confirmed isn't how it's actually paid.

### Backlog

* [x] Schema (final shape, after two rounds of simplification — `migrations/008`, `009`, `010`): `bank_loans` gains `account_number`, `repricing_date`, `mode_of_payment` (informational only), drops `monthly_installment`. `bank_loan_installments` (renamed from `bank_amortization_schedules`) is just `installment_id`, `bank_loan_id`, `installment_no`, `due_date`, `amount`, `created_at` — no principal/interest breakdown, no status/paid_date columns (cleared is computed, per above). No `check_id`/`checks_issued` link — settlement is untracked cash. `outstanding` is computed live in queries, never a stored column.
* [x] Manual schedule entry: a single unified `st.data_editor` grid (add/edit/delete together, like the Invoices/Credit Memo grids) — just Due Date and Amount as inputs, a read-only computed Status column, `installment_no` auto-sequenced by due date.
* [x] `engines/bank_loan_import_service.py` — bulk schedule import from CSV/Excel (Due Date + Amount, flexible header synonyms), mirroring `loan_schedule_import_service.py`.
* [x] Bank Loan tab rebuilt (`app.py`, Bank loans tab): browser with click-to-select (mirrors Loan Notes), create-loan form, header card with delete-if-empty, the schedule grid + import expander, servicing report. `engines/bank_loan_service.py` holds all writes — `_sync_loan_status()` auto-Closes the loan once every installment's due date has passed and auto-reopens it if a future-dated row is added back (a correction). `is_installment_cleared()`/`CLEARED_SQL` centralize the `due_date <= today` rule.
* [x] Old ADA-only "Settle installment" flow removed entirely, including the line that debited `bank_accounts.current_balance` — untracked cash never touches the tracked bank balance. Deleting an already-cleared row is refused (correct it in place instead); deleting a future row is allowed.
* [x] **Loan beneficiary attribution** ("for bank loans, i need also to see to whom the bank loan it is attributable to... a person, affliates that the business are the one using and paying for it"). Same idea as the credit card cardholder registry but broader — a person OR an affiliate company, kept as its own registry (`loan_beneficiaries`, `beneficiary_type` CHECK IN ('Person','Affiliate')) rather than reusing `cardholders`, confirmed via `AskUserQuestion` (registry over free-text, to avoid name drift across loans). `bank_loans` gains nullable `beneficiary_id`. Surfaced everywhere a loan is viewed: the browser table ("Attributable To"/"Type" columns), the header card caption, a new "Edit attribution" control, `get_bank_loan_servicing_report()`, and the Bank Loan PDF's info panel.
* [x] Printable Bank Loan PDF (`engines/bank_loan_report_service.py`, `generate_bank_loan_pdf()`) — same treatment as the Batch Statement (Order Statements) and Loan Note/MOU (Loan Notes) reports: header block (Loan Ref, Bank, Account Number, Start/Maturity, Principal, Mode of Payment) plus the Due Date/Amount schedule table, Status computed the same `due_date <= today` way as everywhere else. "Export Bank Loan PDF" button added to the header card next to Delete.

**Implemented 2026-09-06, then simplified twice more in the same session** as the user corrected scope down from a full bank-document column set to just Due Date + Amount, then from a manually-toggled Paid status to the same auto-cleared-by-date convention checks already use. Each round was verified functionally against a throwaway DB copy (create loan, add/edit/delete installments, auto-Close/auto-reopen, negative-amount and delete-cleared guards) and via `streamlit.testing.v1.AppTest` (no exceptions on initial render) before moving on. Two real bugs caught and fixed along the way, both in `app.py`'s Dashboard KPI query (Tab 1), which twice lagged behind a schema rename until the render test caught it — and one self-inflicted near-miss where an `Edit` meant for the bank loan report's `.fillna(0.0)` line matched the unrelated supplier aging report instead (both functions end with the same three lines), caught only by re-testing both reports afterward.

## Phase 4 — Necessary Reports

Scoped 2026-09-06. A "Reports" tab (Tab 7) already existed from before the phased-discipline pattern, with four CSV-downloadable per-track reports (Supplier AP aging, Private lender master, Bank loan servicing, Credit card due alerts) plus separate per-entity PDFs elsewhere (Batch Statement, Loan Note/MOU, Bank Loan PDF). Rather than assume what was missing, asked what gaps actually mattered — user picked **"a combined AP summary and separate tracks"** (i.e., add a combined view, keep the existing per-track ones) plus **"existing reports need fixing first."**

* **Audited the four existing reports against live data before asking what "needs fixing" meant** — found two real, confirmable staleness bugs rather than guessing: `get_private_lender_master_report()`'s exposure figure still used the pre-this-session issuance-based check sum (`status IN ('Active','Pending')`, ignoring clearance) instead of the cleared-vs-issued convention established earlier this session for Loan Notes' own display — overstated Ramon Mendoza's outstanding exposure by exactly the postdated-check amount (₱520,266.67 shown vs. ₱420,266.67 real). `get_supplier_ap_aging_report()` silently excluded every `'Partially Paid'` invoice from all totals/buckets (`JOIN ... AND i.status = 'Unpaid'`) — not currently visible (no live invoice is in that state) but a live landmine once one exists. Both confirmed with the user before fixing (`AskUserQuestion`: fix now vs. note and defer — chose fix now).
* **Fixed both**: the lender report now computes `outstanding_uncleared` the same way (`total_payable - SUM(cleared active checks)`) and also surfaces `total_payable` itself (previously missing from the report entirely). The aging report now includes `'Partially Paid'` invoices using their full amount — the best available signal, since invoices don't track a per-invoice paid amount (a batch's checks pay the net total, not individual lines), consistent with how Outstanding is computed at the batch level everywhere else in this app.
* **Follow-up: "it is not possilbe to have an integration of these 4 tracks for reporting or as schedule of payments"** — confirmed it's very possible (all 4 tracks already have a due date, amount, and payee — just no shared "check number" concept) and built `get_payment_schedule_all_tracks()`/`get_payment_schedule_daily_totals()` as a `UNION ALL` across Supplier/Lender checks (`checks_issued`), Bank Loan (`bank_loan_installments`), and Credit Card (`cc_statements`) — one row per obligation with due_date/payee_name/track/reference/amount, `reference` holding whichever identifier applies per track (check number, loan ref, or card name+last4, since no single concept spans all four). Wired in above the checks-only report as "Schedule of payments — all tracks," with the same daily-totals-then-detail layout plus a track filter on the detail table. Verified per-track sums exactly match `get_combined_ap_summary()`'s figures (Lender ₱420,266.67, Bank Loan ₱475,000, Credit Card ₱88,400) and that same-day items from different tracks correctly combine into one daily total (Oct 6, 2026: a Bank Loan due + a Lender check together = ₱113,000).
* **New "Uncleared checks by due date" report** (`get_uncleared_checks_report()`, `get_uncleared_checks_daily_totals()`), modeled directly on a real legacy "All Time Payee Checks" PDF the user shared — every not-yet-cleared check (`status='Active'`, `check_date` in the future, the standard cleared convention), grouped by due date with a per-day count/amount subtotal plus a full flat breakdown (Due Date, Payee, Track, Check Number, Amount). Deliberately **Supplier and Lender checks only** — Bank Loan and Credit Card dues aren't real check instruments in this system (both untracked cash per this session's redesign), and the reference document's own scope was strictly checks with real check numbers, so extending it to non-check dues would misrepresent what the report claims to show.
* **Follow-up: "consider that all due date falls on a weekend and holidays should be paid the next banking day."** Confirmed scope first (`AskUserQuestion`): reports-display only, not a change to any stored due_date or to the auto-cleared-by-date rule elsewhere in this app; weekends only for now (no PH holiday calendar exists in this system and holidays change every year — deferred, not forgotten). New `next_banking_day()` (`engines/reports_service.py`) shifts a Saturday/Sunday due date to the following Monday. All four report functions (`get_uncleared_checks_report/_daily_totals`, `get_payment_schedule_all_tracks/_daily_totals`) gained an `actual_payment_date` column and now group by it instead of the raw `due_date` — the daily-totals functions were refactored to derive from their own detail function (single source of the shift rule) rather than a separate SQL `GROUP BY`. Both PDFs show the raw Due Date per row (marked `*` when it differs from the group's payment-date heading) alongside the payment-date grouping, with a footnote explaining the convention.
* **Follow-up (mid-task): "why checks from order statement are not included"** — checked the live data before assuming a bug: every Supplier-linked check in `checks_issued` is dated Sept 4-5, 2026, and "today" is Sept 6 — under this app's standing cleared rule (`check_date <= today`), all of them have already cleared, so there are genuinely zero uncleared Supplier checks to show. Correct behavior, not a missing-track bug; the Supplier branch of the report's `UNION ALL` is intact and will surface rows the moment a real postdated supplier check exists.
* **Follow-up: "i think the only i need here is schedule of payments - all tracks. include also a filter date i usually export monthly."** Reports tab (Tab 7) trimmed down to just "Schedule of payments — all tracks" — Combined AP Summary, Uncleared Checks by Due Date, Supplier AP Aging, Private Lender Master Report, Bank Loan Servicing Report, and Credit Card Due Alerts all removed from this tab (the underlying `reports_service.py`/`payment_schedule_report_service.py` functions themselves were kept, not deleted — a scope-narrowing statement, not an explicit "no need for this" like `statement_ref` got; Supplier AP Aging and Bank Loan Servicing Report are still shown elsewhere — Dashboard and the Bank Loans tab respectively — and kept their imports for that). Added a "Filter by month (payment date)" selectbox alongside the existing track filter, both applied together to the daily totals, full breakdown, CSV, and PDF exports — `generate_payment_schedule_pdf()` gained a `month: str | None` ("YYYY-MM") parameter for the usual monthly export.
* **Follow-up: "why i cant print seperatle for tracks"** — the on-screen "Filter by track" multiselect only affected the displayed dataframe; the CSV download and PDF export both ignored it and always exported all 4 tracks regardless. `generate_payment_schedule_pdf()` gained an optional `tracks` filter (subtitle updates to name the selected track(s)), and the CSV/PDF filenames + content now follow whatever's currently filtered on screen — what you see is what gets exported.
* **Follow-up: "compress further"** — rebuilt both PDFs from many small boxed per-day tables (each with its own repeated header bar and margins) into a single continuous table per report (column header repeats on every page via `repeatRows`, date rows are inline spanning cells, subtotal rows inline) — matches how the reference document itself reads and scales to hundreds of rows across many months without ballooning into dozens of pages of mostly whitespace.
* **PDF export added for both new reports** (`engines/payment_schedule_report_service.py`, `generate_uncleared_checks_pdf()` / `generate_payment_schedule_pdf()`) — asked why these two shipped as CSV-only when the reference document itself was a formatted, printable PDF; user wanted the PDF too. Same grouped-by-date/subtotal/grand-total shape as the reference report, styled to match this app's own existing PDFs (title band, section table, ACCENT color) rather than cloning the legacy report's visual theme — the data structure is what mattered, not the old system's exact look. "Generate PDF" buttons added next to each report's CSV downloads.
* **New `get_combined_ap_summary()`**: one row per track (Supplier / Private Lender / Bank Loan / Credit Card) plus a TOTAL row, each using that track's own already-established "still owed" definition — Supplier sums Unpaid+Partially Paid invoices, Lender sums Outstanding (uncleared) for Active+Defaulted notes only (Cancelled/Settled aren't live obligations), Bank Loan and Credit Card both sum whatever hasn't reached its due date yet (their standing auto-cleared-by-date convention). Deliberately has **no single cross-track "Overdue" column** — Bank Loan and Credit Card auto-clear by date so "overdue" doesn't mean anything for them anymore, while Supplier and Lender can be genuinely overdue (a postdated check not yet covering a due date); forcing one column across all four would be misleading for at least two of them. Verified every figure against its own source report before shipping (Supplier ₱84,950, Lender ₱420,266.67, Bank Loan ₱475,000, Credit Card ₱88,400, TOTAL ₱1,068,616.67 — all traced back to the individual per-track numbers).
* **Reports tab redesigned into a one-report-at-a-time picker (2026-09-14).** User: "i am overwhelmed by the report tab. I prefer it is focus only on generating reports" — the tab itself only ever held the two reports from the 2026-09-06 "compress to just Schedule of payments" follow-up above (that trim removed the *aggregate* reports, not future ones), so "overwhelm" meant density, not clutter from unrelated features: each of the two sections stacked filters, a daily-totals table, a full-breakdown table, metrics, and 3 export buttons, all expanded at once. Asked what else should be reachable here ("you ask more" — explicit ask for more clarifying questions) before writing code; user wants "all kinds of report that can be extracted from the system like all batches or checks or invoices related to a supplier, lender, bank loan, statement of credit cards." Scoped the redesign via three rounds of `AskUserQuestion`:
  * **Layout**: a single `st.selectbox("Report", REPORT_OPTIONS)` at the top of the tab; picking one shows only that report's own filters/tables/exports (an `if/elif` chain keyed on the selection), replacing the two always-open `st.container(border=True)` blocks. The two original reports (Schedule of payments — all tracks, Parked checks) moved into this chain unchanged internally.
  * **New per-entity drill-down reports** (pick one specific supplier/lender/bank loan/credit card, see everything for just that one — not a grouped "all entities" listing, per the user's explicit choice): "Supplier — invoice & check history" (batches/order_statements + invoices + checks for one supplier, with running totals), "Private lender — loan note & check history" (loan notes + checks for one lender), "Bank loan — installment history" (header info + every installment, Paid/Outstanding derived the same due-date-in-the-past convention as `get_bank_loan_servicing_report()`), "Credit card — statement history" (header info + every statement for one card). New `engines/reports_service.py` functions: `get_supplier_history()`, `get_lender_history()`, `get_bank_loan_history()`, `get_credit_card_history()` — each takes one id and returns 2-3 DataFrames (or a header row + detail rows), plain `sqlite3`/`pd.read_sql_query` like every other function in this module. New `app.py` picker helpers: `creditor_options_all(creditor_type)` (like the existing `creditor_options()` but *without* the `is_active = 1` filter — a report picker must still find a supplier/lender that's since been deactivated, unlike a data-entry dropdown), `bank_loan_options()`, `credit_card_options()`.
  * **Master rollups, exposed in this tab for the first time**: "Master rollup — supplier AP aging," "— private lender exposure," "— bank loan servicing" wire up the already-existing `get_supplier_ap_aging_report()` / `get_private_lender_master_report()` / `get_bank_loan_servicing_report()` (previously only shown standalone on the Dashboard and inside the Bank Loans tab, never as a company-wide exportable table in Reports) — no new backend needed, just a CSV-downloadable table added here. A "check register" option (`get_uncleared_checks_report()`, built in Phase 4 but never wired into any tab after the 2026-09-06 trim) was offered as an alternative and explicitly not chosen — still unused/available for later.
  * Verified before touching the live server (this session's standing habit): `py_compile`, then all 9 report options clicked through via `streamlit.testing.v1.AppTest` against the empty test-fixture DB (re-fetching the selectbox from `at` fresh on every iteration — reusing one captured `Selectbox` object across multiple `.set_value().run()` calls hit a stale-node `KeyError` inside AppTest's own widget-state serialization, an AppTest-internal quirk unrelated to the app), the full 49-test `pytest` suite (unaffected — no other tab touched), then a full live click-through in the browser against the real populated database confirming all 9 reports (including all 4 new drill-downs and all 3 master rollups) render correct real figures with no console errors.
* **Follow-up same day: fixed a real metrics bug, then added date/status filters + PDF export to all 4 drill-downs.** User caught it from a live screenshot: a supplier ("Berto") whose one batch was entirely Cancelled (both invoices Cancelled, all 3 checks Cancelled/Replaced) still showed "Total invoiced ₱34,000.00" / "Total paid (checks) ₱38,000.00" — the metric cards summed every row's `amount` with no status filter at all. **Fixed**: both metrics now exclude Cancelled invoices / non-Active checks unconditionally (`sup_invoices_live`/`sup_checks_live`, filtered before the `.sum()`), with a `help=` tooltip on each card spelling out the exclusion — re-verified live against the exact same "Berto" case: now correctly shows ₱0.00 / ₱0.00.
  * User then reframed the feature's actual purpose — "the purpose of these reports are whenever i am asked for history of transactions. plus it is restricted to me make it more user-parameters friendly" — and, asked which parameters, answered Date range + Status filter (both `AskUserQuestion`-recommended) plus a free-text addition: "include pdf reports just a simple reports (like it is a simple statement of account, for example in order statement combine all batches (make it have an option) and like." Also confirmed via `AskUserQuestion`: the summary metrics should **always** exclude cancelled/replaced (not a toggle) — matches the fix already shipped.
  * **Added to all 4 drill-downs** (Supplier, Lender, Bank Loan, Credit Card): a shared `history_date_range()` helper (`app.py`) renders one From/To date picker per report, defaulting to the full span of dates actually on file for the picked entity (so it starts unfiltered) — keyed by the entity's own id (e.g. `f"rpt_sup_date_{sup_id}"`) so switching entities resets the range instead of carrying over a stale one that might not even overlap the new entity's dates (a real Streamlit gotcha: a stale `date_input`/`multiselect` session_state value outside the new widget's options/min/max range raises, not silently resets). A parallel `filter_by_status()` helper renders a per-table status multiselect (defaulting to every status present, so also unfiltered by default) — Supplier gets 3 (batches/invoices/checks), Lender gets 2 (notes/checks), Bank Loan and Credit Card get 1 each. The status filter only narrows what's displayed/exported for review; it does **not** affect the summary metrics, which always apply the fixed cancelled/replaced-exclusion rule regardless — the `help=` tooltips say so explicitly to avoid the two numbers looking contradictory.
  * **New `engines/account_history_pdf_service.py`** (new file, mirrors the existing per-PDF-file convention already used by `batch_statement_service.py`/`payment_schedule_report_service.py` rather than sharing a base module): `generate_supplier_statement_pdf()`, `generate_lender_statement_pdf()`, `generate_bank_loan_statement_pdf()`, `generate_credit_card_statement_pdf()` — a simple "Statement of Account" layout (title band, section tables, bold totals block) built from whatever DataFrames are currently filtered/shown on screen (WYSIWYG, same convention as the Schedule of Payments PDF/CSV). Supplier and Lender each take a `group_by_batch`/`group_by_note` flag, wired to an on-screen checkbox ("Break out each batch/note separately (instead of one combined statement)") — default off means one combined statement across every batch/note (the "combine all batches" the user asked for); switching it on breaks the statement into a section per batch/note with its own subtotal instead. Bank Loan and Credit Card have no such toggle (already a single entity, nothing to combine).
  * Verified before live: `ast.parse` on all 3 changed/new files (`py_compile` intermittently hit a Windows file-lock from the live server's own autoreload holding the `.pyc` — worked around by parse-checking instead of fighting the lock), the full 49-test `pytest` suite (still green, unaffected), then a real-data script exercising every one of the 21 real entities on file (12 suppliers, 2 lenders, 2 bank loans, 5 credit cards) through both PDF generators' flag states with no exceptions, then a live browser re-run of the exact reported bug case ("Berto," default date range) confirming ₱0.00/₱0.00 and a successful "Generate PDF" → "Download Supplier Statement of Account PDF" with no console errors.
  * **Immediate follow-up: "for creditcard, it is grouped by the owner of the card, then it can further drill down."** The Credit Card drill-down's single "Credit card" selectbox (`{card_name} - {last4}`) made it hard to tell cards apart once a cardholder has 2+ (e.g. Berta has "Gold - 7000" and "Platinum - 9000") without knowing the last4 already — same underlying "1 card holder = many cards" fact already handled the same way in the Credit Cards tab's own "Add statement" flow (Cardholder picked first, Card scoped to it). Applied the identical two-level pattern here: new `cardholder_options_with_cards()` (only cardholders with >=1 card, plus a synthetic `None`/"Unassigned" entry when any card has no `cardholder_id`) and `credit_card_options_for_holder(cardholder_id)` (scoped query, `cardholder_id IS NULL` branch for Unassigned). Reports tab now shows Cardholder → Credit card as two chained selectboxes, the second one's key suffixed by the chosen holder's id so switching holders doesn't carry over a stale card selection from a different holder's list. Verified live against real data: the picker correctly lists Bert, Berta, and Unassigned (2 real unassigned cards on file), and picking Berta correctly scopes the second dropdown to exactly her two cards.
  * **Follow-up: "include all an option for to combine all statements/select all card statement of the owner."** Added an "All cards" choice at the top of the Credit card dropdown (alongside the individual cards) — picking it switches to a combined view across every card that cardholder owns: new `get_cardholder_all_cards_history(cardholder_id)` (`engines/reports_service.py`, `cardholder_id=None` covers the "Unassigned" bucket) returns a cards table plus a combined statements table (with a Card column to tell rows apart), and `generate_cardholder_statement_pdf()` (`engines/account_history_pdf_service.py`) mirrors the Supplier/Lender "combine by default, `group_by_card` breaks it into a per-card section with its own subtotal" pattern. Verified live: Bert (1 card) shows "All cards (1)" correctly; Berta (2 cards, Gold + Platinum) shows combined ₱100,000 credit limit and a working PDF export in both combined and per-card modes.
* **Per-invoice check allocation (2026-09-14, migration 025).** User, after the walkthrough of what Invoice/Batch status values mean: "i think it would be better to track per invoice for i know which are invoice that already paid. i can pin point which checks cover the amount." Real gap confirmed by reading the code first: `sync_invoice_payment_status()` (`app.py`) only ever set the *same* status on every invoice in a batch, based on the batch's total cleared checks vs its net total — there was no link at all from a check to a specific invoice (`checks_issued` only has `statement_id`), so a batch with 3 invoices and 1 cleared check showed all 3 identically "Partially Paid" with no way to tell which one was actually covered.
  * Scoped via two rounds of `AskUserQuestion` before writing any code (explicit "dont code yet. ask me"): **many-to-many** allocation (one check can pay several invoices; one invoice can be paid across several checks — both requested), invoice status becomes **auto-computed** from linked, cleared allocations (replacing the manual/blanket status), **going-forward only** (no backfill of existing checks/invoices — "it is not the real live data yet. we will plan a migration later"), credit memos **stay batch-level** (unchanged), allocating a check is **mandatory** for every new check from here on, and the per-invoice amounts are **auto-filled oldest-due-date-first** (adjustable) rather than starting blank.
  * **New table** `invoice_check_allocations` (migration `025_invoice_check_allocations.sql`, also appended to `schema.sql`): `invoice_id`, `check_id`, `amount` — plain many-to-many join table, no status/flag of its own.
  * **`sync_invoice_payment_status()` rewritten with a fallback branch, not replaced**: if a batch has *never* had an allocated check (`invoice_check_allocations` empty for that `statement_id`), it keeps the exact original blanket batch-level computation — a purely legacy batch behaves identically to before. The moment any check in a batch gets allocated, every invoice in that batch switches permanently to per-invoice computation (its own cleared allocations vs its own `amount`). **Disclosed, accepted tradeoff** (since not live data yet): a legacy check's amount becomes invisible at the per-invoice level once a batch switches to per-invoice mode — only its contribution to the batch-level balance (`get_batch_balance`, used for locking) stays intact. A real migration plan for existing production data is explicitly deferred, per the user.
  * **New `app.py` functions**: `get_invoices_with_remaining(statement_id)` (this batch's non-cancelled invoices with how much of each is still unallocated — subtracts every existing allocation regardless of clearance, since allocating reserves the invoice amount immediately, preventing the same dollar being double-booked to two different checks); `waterfall_allocate_preview(check_rows, invoices_df)` (pure function — auto-fills which invoice(s) each about-to-be-issued check pays, oldest due date first, draining one invoice before moving to the next and moving to the next check once the current one runs out — one row per (check, invoice) pair); `allocate_check_to_invoices(check_id, allocations)` (the actual write path, re-validates against each invoice's live remaining balance even though the UI already enforces it).
  * **"Issue a check" (Order Statement → Checks sub-tab, the bulk multi-row grid) gained a mandatory "Allocate to invoices" step** between the check grid and the "Park Metrobank checks" button: an editable preview table (Check #, Check Date, Invoice, Allocated) seeded by the waterfall, re-signature-keyed the same way the check-splitting grid above it already is (so it resets cleanly when check amounts or the open-invoice set changes, rather than silently keeping a stale allocation). The submit button is disabled until every check's allocated total matches its own amount exactly *and* no invoice is allocated more than its own remaining balance. On success, `allocate_check_to_invoices()` runs once per newly-issued check (matched positionally to the grid rows, since `issue_checks_grid()`'s returned `check_ids` are in the same order), then `sync_invoice_payment_status()` re-runs.
  * **6 new regression tests** (`tests/test_invoice_check_allocations.py`): waterfall splitting one check across two invoices and one invoice across two checks; the full issue → allocate → sync flow producing correct independent Paid/Partially Paid statuses (confirmed a ₱12,000 check against a ₱10,000 + ₱5,000 invoice pair correctly marks the first Paid and the second Partially Paid with ₱3,000 still remaining — the exact scenario that was impossible before); the legacy-batch fallback still produces the old identical-blanket-status behavior; `allocate_check_to_invoices()` rejecting over-allocation; `get_invoices_with_remaining()` excluding Cancelled and fully-allocated invoices. All 55 tests pass (49 prior + 6 new).
  * **Verified live against the real (non-production) database**: created a fresh test batch (2 invoices, ₱10,000 + ₱5,000), confirmed the waterfall preview auto-filled exactly as designed, issued the combined ₱15,000 check through the real UI, confirmed both invoices flipped to `Paid` with matching `invoice_check_allocations` rows, then deleted the scratch batch/invoices/check afterward. Incidentally also confirmed working on a real, independently-issued check (supplier "Pia Supply," batch `2026 - OS 0013`, 3 invoices doc-ref'd MIA-01/02/03) that got allocated through the same new UI — left that one alone since it's genuine (non-test) usage, not something to clean up.

## Phase 5 — Dashboard: KPI

Started 2026-09-06. User request: "1 no of check returns due to long overdue, 2 average days payable outstanding... specific to suppliers, heatmap of suppliers (highest paid supplier or with highest payable to), line graph... of total payables monthly with an option to change to year, quarterly... i am a visual person. so suggest necessary visuals and KPI." Two of the four asks didn't map onto anything that exists in the system as literally worded — checked live data and confirmed scope via `AskUserQuestion` before building rather than guessing:

* **"Check returns due to long overdue"** — this system has no bank-return/NSF/reconciliation concept anywhere, and `cancel_check()` has no reason field to query. Confirmed this instead means: checks that were cancelled/replaced (voided) while already sitting well past their obligation's due date — computed purely from stored dates (`due_date` → `check_date` lag), not a manual reason. **"Real bank returns"** was offered as the literal alternative and explicitly declined as a bigger, separate feature.
* **"Average DPO specific to suppliers"** — confirmed measured as invoice due date → check date (the standard "are we paying on time" definition), not invoice-received-date → check date (full cash-cycle length).
* **"Heatmap of suppliers"** — checked live data first: only \~4 months of real check history across 8 suppliers, mostly 2-8 checks each. A true supplier×month grid would render mostly empty right now. Confirmed a ranked bar chart instead (covers "highest paid" and "highest payable to" via one toggle).
* A supplier check pays a batch's **net total**, not one invoice, so there's no single per-check due date to measure lag against. Built one shared anchor instead: each batch's invoice-amount-weighted average due date (`SUM(due_date_julian * invoice_amount) / SUM(invoice_amount)`), used identically by both the DPO metric and the overdue-cancelled-check count so they can't drift out of sync with each other.
* [x] `engines/dashboard_kpi_service.py` (new): `get_supplier_check_lag()` (one row per supplier check with `lag_days`, the shared anchor above), `get_average_dpo_suppliers()` (mean lag over cleared checks — `status='Active' AND check_date <= today`, same cleared convention as everywhere else), `get_overdue_cancelled_checks(threshold_days=30)` (Cancelled/Replaced checks with `lag_days` past an adjustable threshold — exposed as a Dashboard number input, not hardcoded, matching this app's "smart default, stays overridable" pattern used for suggested interest etc.), `get_top_suppliers(metric, limit)` (ranked bar-chart data, "outstanding" = same Unpaid/Partially-Paid invoice sum as the aging report, or "paid" = sum of cleared checks all-time), `get_payables_trend(granularity)` (all 4 tracks combined, grouped by month/quarter/year — deliberately includes past-cleared *and* future-scheduled items together, unlike `get_payment_schedule_all_tracks()` which excludes cleared items on purpose for its own "still owed" use case; a payables trend needs the full history, not just what's outstanding).
* [x] Dashboard (Tab 1) gained a "Supplier payment health" section (DPO + overdue-cancelled metrics, adjustable threshold, a detail table when any are flagged) and a two-column "Top suppliers" (`st.bar_chart(horizontal=True)`, toggle) / "Total payables trend" (`st.line_chart`, Month/Quarter/Year toggle) row, using Streamlit's native Altair-backed charts — no new dependency added (checked first: `plotly`/`matplotlib` aren't installed, `altair` is bundled with Streamlit and is exactly what `st.bar_chart`/`st.line_chart` already use).
* Verified every function against live data before wiring into the UI: found and traced a real-looking anomaly (Elle's batch shows a −120-day lag) back to genuine stored data (invoices due 2027-01-03, checks dated 2026-09-05 — test data from earlier sessions, not a calculation bug) rather than assuming the formula was wrong. Confirmed live in the browser: both new toggles (Top Suppliers metric, Trend granularity) re-render correctly with the expected figures; `AppTest` render pass clean.
* Not built, flagged for later if wanted: a true supplier×month heatmap (declined — sparse data right now); real bank-return/NSF tracking (declined — a separate reconciliation feature, not a dashboard tile); a cash-balance-over-time line (no historical balance table exists in this system — `bank_accounts.current_balance` is a single live figure, not a time series — would need a new snapshot table to build).
* **Follow-up: "remove metrobank account balance."** The Dashboard's top metrics row had "Metrobank Account 1 balance" duplicating the sidebar's "Available balance" metric, which is already visible on every tab, not just Dashboard — removed the redundant Dashboard copy only. `bank_df`/`bank` stayed in scope (still used by the sidebar and the Admin & audit tab), so nothing else broke. `AppTest` render pass clean.
* **Follow-up, round 2 (2026-09-06): "1. trend payables be a bar graph then with Simple weighted average line, 2. abolish metrobank cash i do not want to trace bank's cash, 3. suggest relevant kpi."** Scoped fully via `AskUserQuestion` (logged in MEMORY.md) before writing any code, then implemented in the same session:

  * [x] **Payables trend is now a bar+line combo**, not a plain line chart: `get_payables_trend()` gained a `moving_avg` column (3-period trailing rolling mean, `min_periods=1` so it draws from the first period rather than waiting for 3 periods of history — a period count, not a calendar span, so it's unaffected by the Month/Quarter/Year toggle). Rendered via `st.altair_chart` (bars layered with a line, since `st.bar_chart`/`st.line_chart` can't combine two mark types) — first explicit use of raw Altair in this app instead of Streamlit's native chart wrappers, still the same bundled dependency (no new package).
  * [x] **Bank cash tracking abolished, not just hidden.** Investigated first: `bank_accounts.current_balance` had no actual hard block anywhere (real spending limits are the payable ceilings) — it was purely an unenforced debit/credit ledger. Confirmed via `AskUserQuestion`: dropped the column from the schema entirely (`migrations/016_drop_bank_balance.sql`, `ALTER TABLE ... DROP COLUMN` — SQLite 3.35+ supports this directly, no full-table-rebuild needed; verified no view/index/trigger referenced the column first). Removed every `UPDATE bank_accounts SET current_balance...` call site (`issue_check`, `issue_checks_grid`, `cancel_check`, `apply_check_correction`'s Amount branch, `engines/cc_service.py`'s `settle_cc_statement_via_cash`), the `adjust_bank_balance()` function and its Admin & Audit "Manual balance adjustment" form, and both display metrics (sidebar "Available balance", Admin "Balance"). Encashment (CLAUDE.md's Core System Configuration) is explicitly kept as a workflow per the same `AskUserQuestion` — it still goes through create-batch → invoice-line → issue-check for record-keeping, it just no longer debits a balance since nothing does anymore.
  * [x] **Two new Dashboard KPIs**: `get_upcoming_cash_obligations(days)` (reuses `get_payment_schedule_all_tracks()`'s own per-track "still owed" definition, just filtered by a due-date cutoff — two metrics, 7-day and 30-day, all 4 tracks) and `get_supplier_concentration(limit=5)` (top-5 suppliers' share of current outstanding payable, reusing `get_top_suppliers("outstanding")`'s definition — "who am I most exposed to today," not historical paid volume).
  * [x] **Sidebar quick-search** (`engines/search_service.py`, new): the user's real usage is searching a check number or invoice doc ref and jumping to the batch it belongs to, confirmed via multiple rounds of `AskUserQuestion` rather than building a generic full-text search. Priority order: check number (any status, including cancelled/replaced — full history stays searchable) → spoiled-check log (its own explanation, not a generic not-found) → invoice doc ref (not guaranteed unique in this system, so a pick-list is a real possibility even here) → Order Statement / Loan Note / Bank Loan ref or supplier/lender name (secondary, checked last). A single match auto-jumps straight to the record's specific sub-tab (Checks for a check number, Invoices for a doc ref); multiple matches show a pick-list; zero matches show a plain warning.

    * **Technical unlock**: this Streamlit version (1.62.0) added `key=`/`default=`/`on_change=` to `st.tabs()` — confirmed via `inspect.signature`/docstring before assuming it was possible. With `on_change="rerun"`, a tab's current selection is readable/settable via `st.session_state[key]`, same as any other widget, as long as it's set *before* that specific `st.tabs()` call executes in a given script run (same rule as this app's existing `_os_mode_next` staging-key pattern) — no rearchitecting of the 9-tab top-level nav was needed. Added `key="main_tab"` to the top-level `st.tabs(tab_labels)`, `key="os_subtab"` to Order Statement's nested Invoices/Credit Memo/Checks tabs, and `key="ln_subtab"` to Lenders' nested Repayment Schedule/Checks tabs. Trade-off accepted: `on_change="rerun"` means clicking a tab header now triggers a full server rerun (previously free/client-side under the default `on_change="ignore"`) — acceptable for a single-user internal tool that already reruns fully on most interactions.
    * Verified via `streamlit.testing.v1.AppTest`: check-number search → Order Statement tab, correct batch, Browse mode, Checks sub-tab, no exception; invoice doc-ref search → Order Statement tab, Invoices sub-tab, no exception; Bank Loan ref search → Bank Loans tab, correct loan, Browse mode, no exception; spoiled-check number → correct "logged as spoiled" message, no navigation; no-match term → plain warning, no navigation. Also hit a real (pre-existing, not introduced by this work) Streamlit/AppTest quirk: setting a `st.segmented_control` key's session_state value ahead of a widget that also has a `default=` logs a scary-looking `_LOGGER.warning` stack dump — reproduced it independently with the app's own pre-existing `_os_mode_next` flow (unrelated to this session's changes) to confirm it's a benign, already-present warning rather than a real exception (`at.exception` was empty in both cases).
    * **Not visually verified in a real browser this session** — the `claude-in-chrome` extension wasn't connected. Everything above was verified via `AppTest` (widget tree + session_state assertions) and direct function calls against the live DB; recommend a real click-through before relying on this for daily use.
  * **Addition (2026-09-15): Unified Debt Interest Tracking.** User handed this in as a fully pre-written implementation ticket (schema, function signatures, UI code, tests) rather than a conversational ask — but the ticket's own schema (`ap-system` folder, `cc_statements.amount_due`, `bank_loans.loan_id`/`bank_name`/`annual_interest_rate`, a brand-new `bank_loan_installments` table) didn't match this project at all (wrong folder name; real PKs are `cc_statement_id`/`bank_loan_id`; no `bank_name` column — the bank is a `creditor_id`; `bank_loan_installments` already exists with a different shape). Confirmed with the user this is still the same project (ticket was drafted with claude.ai, without seeing the real codebase) before adapting it wholesale to the actual schema/conventions rather than the ticket's literal spec. Scoped via `AskUserQuestion` + one live follow-up:
    * CC statements: a real rolling balance (previous + new charges + interest = total, minus amount paid = next month's previous balance) **replaces** the old "due date passed = auto-Paid regardless of what's owed" shortcut — "new rule wins": a statement with a real unpaid balance stays Unpaid/Partially Paid past its due date; a legacy statement (unpaid_balance_next defaults to 0) behaves exactly as before.
    * The CC statement form always shows the full breakdown (not an optional toggle), per the ticket's own design.
    * The unified interest view lives on the Dashboard tab, not Reports.
    * Private loans show their full real interest history (the data already exists and is accurate); bank loans/credit cards naturally only have data from whenever tracking starts for them.
    * **Bank loans: explicitly NOT the ticket's mandatory-amortization-table workflow.** Live correction mid-build: "for bank loan still simplified but i need to get the portion of interest so i can track properly... i think this can be add to our KPI." Dropped entirely: `has_amortization`/`amortization_notes` columns, the CSV/paste-and-validate amortization UI, the new-loans-must-have-a-schedule requirement, the optional-backfill screen. Kept: one optional `interest_portion` column added to the *existing* `bank_loan_installments` table (migration 028) — typed straight off the bank's document exactly like Amount already is, defaulting to 0, no computation, in the same manual grid that's always been there. Phase 3's original simplification stands untouched for anyone who doesn't fill it in.
    * **New table columns**: `cc_statements` gains `previous_balance`/`new_charges`/`interest_charged`/`amount_paid`/`unpaid_balance_next` (migration `027_cc_rolling_balances.sql`; `total_amount_due` itself is kept, not renamed/dropped, since it's already read by yesterday's Reports-tab drill-downs and PDF exports — it's just computed differently now). `bank_loan_installments` gains `interest_portion` (migration `028_bank_loan_interest_portion.sql`). Both migrations also folded into `schema.sql` for fresh builds. `v_cc_due_alerts` (the Dashboard's CC alert source) was dropped and recreated unpaid_balance_next-aware in the same migration; the Dashboard's `ap_outstanding` KPI and `get_combined_ap_summary()`'s credit-card sum (`engines/reports_service.py`) both got the identical CASE-expression fix — all three previously shared the exact same "past due date = silently excluded from outstanding" blind spot.
    * **New `engines/interest_service.py`**: `get_monthly_interest_summary(year, month)` (per-source breakdown + unified total, each source using its own already-established "paid" convention — private loans via the linked check's cleared date, bank loans via due-date-passed, credit cards via the statement's due-date month) and `get_yearly_interest_trend(year)` (12-row DataFrame feeding the Dashboard's 6-month trend chart). Registered in `tests/conftest.py`'s `_DB_PATH_MODULES` — missing this would have let the new test file silently read/write the real production database instead of the test fixture (the exact documented gotcha this list exists to prevent).
    * **Credit Cards tab "Add statement" rebuilt** from a single typed-in total into the live-calculating rolling-balance form (converted from `st.form` to plain widgets, since a form doesn't rerun on every keystroke and the total/unpaid-next figures need to update live as you type) — previous balance auto-fills from `get_last_cc_statement_unpaid_balance()`. `settle_cc_statement_via_cash()` (`engines/cc_service.py`) also now zeroes `unpaid_balance_next`/sets `amount_paid` on a full manual settlement, so the two don't drift out of sync.
    * **9 new tests** (`tests/test_unified_interest_tracking.py`): CC rolling-balance math, previous-balance chaining across statements, the outstanding-past-due-date fix, per-source interest extraction (all three), the unified total, a bank loan installment with no interest recorded contributing exactly 0, and the yearly trend shape. All 70 tests pass (61 prior + 9 new).
    * **Verified live against the real database**: added a real CC statement (₱5,000 new charges + ₱200 interest, ₱3,000 paid) through the actual form — watched the total/unpaid-next auto-calculate live, confirmed Total AP Outstanding rose by exactly the new unpaid balance, confirmed a previously-"possibly missed" card alert cleared, confirmed the Dashboard's Debt Interest Tracker showed exactly ₱200.00 for that card and that month — then deleted the test statement. Verified the bank loan interest pipeline end-to-end via a direct DB round-trip (set a real installment's `interest_portion`, confirmed `get_monthly_interest_summary()` picked it up for the right month and correctly excluded it for a not-yet-due month, reverted). The Bank Loans repayment-schedule grid's new "Interest (optional)" column itself wasn't clicked through live (this session's browser tool couldn't reliably scroll that specific data_editor horizontally) — the underlying `add_bank_loan_installment()`/`edit_bank_loan_installment()` code path is covered by the new tests and by direct DB verification instead.

## Phase 6 — Check printing

Started 2026-09-06/07, under roadmap item 6 ("Others"). Two related pieces of work, both fully verified live in the browser (`claude-in-chrome` connected this time):

* **Chrysanth Check Writer-style print engine** (`engines/check_engine.py`) — see the Chrysanth backlog item below for the full design. Prints date/payee/amount-numeral/amount-in-words onto blank check stock, calibrated per bank account (`bank_accounts.check_width_mm`/`check_height_mm`/`check_offset_x_mm`/`check_offset_y_mm`, migration 019). No MICR, no printed check number, no "A/C PAYEE ONLY" crossing — all three are pre-printed on the physical check stock by the bank, not something this software draws.
* **The "current check run" workflow** (`engines/check_run_service.py`, migration 020) — the user's real workflow: pick a batch, issue checks (dates + split amounts, already fully recorded in `checks_issued` the moment they're issued), do this across *multiple* batches without printing any of them yet ("park" the printing), watch a running total of everything committed-but-not-printed across all those batches, then print the whole run at once with a consolidated summary report. Confirmed via several rounds of discussion: a run spans multiple batches (Supplier and/or Lender); "parked" means already a real `checks_issued` row (not a lighter draft state); the running total needed a new dedicated view (not the existing Payment Schedule report); the end-of-run report is a new consolidated summary (not the existing per-batch Batch Statement).

  * `checks_issued.printed_at` (nullable DATETIME) is the only new column — `NULL` means parked, stamped automatically the moment a check's PDF is generated (single-check print or as part of a run), no separate "mark as printed" step. No separate `check_runs` table — "the current run" is simply derived on demand as `status='Active' AND printed_at IS NULL`, same precedent as the checkbook gap audit rejecting a fuller per-leaf register.
  * New "Current check run" section at the top of the Checks & checkbook tab: pending-checks table + running total, a multiselect (defaults to all) for which of the pending checks to include in this print pass — so a run can be partially printed, leaving the rest parked for later — and a "Print this run" button generating both the merged check-print PDF and the new Check Run Summary PDF.
  * **A real modeling mistake caught while reviewing for this**: `build_dual_copy_check_pdf()` (`app.py`, still dead code / zero callers) merges *two identical copies* of the same check onto one PDF — correct for a Batch Statement (filing + supplier copy of a document), wrong for an actual check (a unique negotiable instrument — you don't print the same one twice). Left untouched since it's unused; the new bulk-print function (`generate_check_run_pdf()`) merges one page per *distinct* check_id instead.
  * **A real bug caught during testing**: `generate_check_run_summary_pdf()` originally sourced its rows from the same pending-only query used for the run view — meaning if it were ever called *after* `mark_checks_printed()` (a plausible reordering), the summary would come up empty. Fixed by giving it its own by-check_id query (`get_checks_by_ids()`), independent of `printed_at`, so calling order can't break it.
  * Verified end-to-end live: parked checks across the real Supplier and Lender batches already in the live DB, printed a 25-of-26 subset (deliberately leaving one out), confirmed the success message's total, both PDFs, and that the left-out check correctly remained the only one still pending on reload — while the 25 printed ones (including one printed earlier via the single-check path) all correctly dropped off the list.
* [x] Chrysanth Check Writer integration: check engine output must match Chrysanth's print/format requirements. **(Implemented 2026-09-07 — user supplied a real Chrysanth sample check PDF + its Options dialog (Printer/Alignment/Font/Style/Locale tabs), resolving the two open design questions: (1) direct PDF generation, not a CSV export for Chrysanth to import — confirmed via `AskUserQuestion`; (2) the real sample gave enough reference to build against, replacing the old placeholder-coordinate draft. MICR generation and the "A/C PAYEE ONLY" crossing are explicitly out of scope — both are pre-printed on the physical check stock by the bank, not something this software draws (same reasoning as the check number, which was already correctly left off). The one blocker that's still genuinely open — no physical Metrobank leaf has been measured — is handled via calibration, not guessing: `bank_accounts` gained `check_width_mm`/`check_height_mm`/`check_offset_x_mm`/`check_offset_y_mm` (migration 019), exposed as an Admin & Audit → "Metrobank Account 1" → "Check print calibration" form, mirroring Chrysanth's own per-account Alignment tab (shift right/down after a real test print). See MEMORY.md for the full design derivation.)**
* [x] Check run (park-then-print) workflow. **(Implemented 2026-09-07 — see above. `engines/check_run_service.py`, migration 020 (`checks_issued.printed_at`). New "Current check run" section, top of Checks & checkbook tab.)**

**Not yet validated against a real physical Metrobank check leaf.** Recommend a real test print before relying on this for actual disbursement — the calibration numbers in Admin & Audit will very likely need adjusting once that happens.

## Phase 6 Redux — Check Numbers Move to Print-Time Assignment (2026-09-07)

Redesigned the same day Phase 6 (above) shipped, once the "current check run" park-then-print workflow surfaced a real gap: a parked check had no number at all yet, and multiple physical booklets in simultaneous use needed independent range tracking. Full derivation in MEMORY.md's 2026-09-07 "Phase 6 Redux" entry; this section is the *what*.

* **Check number assignment moves from issue-time to print-time.** Issuing a check now only locks date + amount (`checks_issued.check_number` becomes nullable, migration `021_check_number_nullable.sql`); the number is assigned automatically from the active booklet the moment the check's PDF is generated (single-check print or as part of a bulk run).
* **`check_booklets` replaces the old passive `checkbooks` range-registry** (rename/rebuild + data migration, `022_check_booklets.sql`) — a stateful table tracking each physical booklet's range, current `next_available_number`, and spoiled numbers, since multiple booklets (e.g. Booklet A 1000–1050, Booklet B 2000–2050) are normally active at once and each needs independent auto-sequencing. The old table's one real feature (gap audit) ports over as `audit_booklet_gaps()`.
* **Parked-check date/amount edits bypass the batch-lock gate entirely** — a batch can auto-lock once balanced while every check in it is still parked (no physical leaf assigned yet), and there's nothing physical yet to justify unlock friction. `apply_check_correction()` now refuses to touch a check until it has a real printed number instead.
* **`get_uncleared_checks_report()` excludes parked checks** (`printed_at IS NOT NULL` added) — a parked check isn't a real check instrument yet; it belongs to the new Parked Checks report. The all-tracks Schedule of Payments deliberately keeps including parked checks (shown as `Pending`), since "is this obligation real" and "has a physical leaf been assigned" are different questions.
* Verified via a 13-scenario functional test against a throwaway DB copy plus a full `AppTest` render pass; migrations `021`/`022` applied to the live DB after a backup — the one real live booklet (12501–12550) migrated with `next_available_number` correctly re-derived as 12519 (a genuine pre-existing gap), 20 historical checks backfilled with `booklet_id`.

**Superseded 2026-09-13** — see Phase 6 Redux v2 below. The schema built here (`check_booklets`, nullable `check_number`, `printed_at`) stays in place; only the *auto-sequencing* behavior is replaced with manual dropdown selection.

## Phase 6 Redux v2 — Check Printing (Simplified, manual booklet dropdown)

Scoped 2026-09-13, after the user confirmed Phase 5 (Dashboard/KPI) complete and reviewed real workflow needs against the Redux design above. Full derivation in MEMORY.md's 2026-09-13 entry; this section is the current *what*.

* **Auto-sequencing is dropped in favor of manual dropdown selection.** The Redux design above (issue → park with no number → later print with auto-sequence from the active booklet) added real implementation complexity — spoiled-number skipping, booklet-exhaustion tracking, partial-run bookkeeping — that this user's actual workflow (single operator, small volumes, manual booklet management already fine in practice) doesn't need. **[x] Implemented 2026-09-13**: `render_assign_number_section()` (`app.py`, shared by Order Statement and Lenders' Checks sub-tabs) drops the old "Auto-sequence from a booklet" vs. "Manual / backfill" radio — pick the booklet, then pick one of its still-open numbers from a dropdown (`get_available_booklet_numbers()`), or use a collapsed "type it directly" fallback when no booklet is on file.
* **Check number assignment still happens at print time, not issue time** (unchanged from Redux) — `checks_issued.check_number` is assigned the moment a check's PDF is generated, via the same UI entry points (Order Statement / Lenders tab single-check print, or a bulk run).
* **Manual assignment flow**: select the checkbooklet (e.g. "Booklet A: 1000–1050") → a dropdown lists only that booklet's still-available numbers → pick one → it disappears from the dropdown once assigned. Prevents accidental duplicate check numbers by construction, without any auto-sequence logic. `assign_check_number_from_booklet()` (`app.py`) verifies the chosen number is in-range and available, then advances the booklet's `next_available_number` "suggested next" cursor only when the chosen number is at or past it — picking an earlier, backfilled number doesn't disturb the normal forward case.
* **Booklet management (`check_booklets`, unchanged schema from Redux)**: each physical booklet still tracks its range, current position, and spoiled numbers; usually 2 booklets are active at once, each printed from independently. Backfilling a number from an exhausted-but-not-fully-used booklet is rare enough that manual selection already covers it — no separate backfill mechanism needed. The old bulk auto-sequencer (`assign_check_numbers_auto()`) was deleted outright once nothing called it anymore; the bulk "Assign check numbers" grid in the Checks & checkbook tab kept its manual/typed-in mode for entering several backfilled numbers at once.
* **Deviation from the original design note**: the note as first written ("issue → immediately assign → print → done, no intermediate parked state") implied removing the parked/print-independent architecture Redux built. That architecture (a check can be issued, printed, and numbered in any order/timing — `printed_at` and `check_number` are independent nullable fields) was left in place rather than torn out — it already works, is tested, and isn't what was actually adding complexity; only the *auto-sequencing* was. Reducing scope to "drop auto-sequencing, keep everything else" was the lower-risk way to satisfy the real complaint (implementation complexity, not the parked-state concept itself).
* **Chrysanth Check Writer integration is unchanged** — still direct PDF generation via `engines/check_engine.py`, calibrated per bank account (`check_width_mm`/`check_height_mm`/`check_offset_x_mm`/`check_offset_y_mm`, Admin & Audit tab). MICR, the check number, and the "A/C PAYEE ONLY" crossing stay off the generated PDF — all pre-printed on the physical check stock by the bank.

**Status: implemented and verified 2026-09-13** — see MEMORY.md's 2026-09-13 "stabilization queue implemented, then Phase 6 Redux v2" entry for the full verification trail (functional tests + live browser click-through, including a real pre-existing bug found live and fixed along the way — `q()`/`q_live()` coerce NULL to `''`, which broke a plain `pd.isna()`/`.notna()` check on `check_number`/`printed_at` in three places; see the `has_value()` helper note in Core System Configuration below).

### Stabilization backlog (blocked Phase 6 Redux v2 implementation — now cleared)

Found while reviewing the app for this phase, unrelated to check printing itself but flagged as blocking. All three implemented 2026-09-13, in this order, before Phase 6 Redux v2 itself:

* [x] **Double-click / duplicate-submission guard.** Streamlit reruns the whole script on every interaction with no built-in re-submit protection — a real duplicate creditor was created live by a fast double-click on "Add creditor." **Implemented**: `guard_duplicate_submit()` (`app.py`) fingerprints a submission's field values and swallows an identical resubmission within a short cooldown — applied to every create/insert action app-wide (all `st.form_submit_button` writes plus the plain-button create paths: issuing checks, bulk invoice/installment commits, invoice/credit-memo grid saves), not just Creditor Master.
* [x] **Creditor Master edit/delete.** Creditors could only be added, never edited or deleted. **Implemented**: `creditors.is_active` (migration `023_creditor_active_flag.sql`) backs a soft-delete — `delete_or_deactivate_creditor()` hard-deletes a creditor with zero references anywhere, or soft-deletes (excluded from new-transaction dropdowns, still visible/reactivatable in Creditor Master) when real history exists. New "Edit a creditor" form updates name/TIN/contact/email. **Superseded 2026-09-14** — see below; this expander-based UI (plus the separate "Update a creditor's payment terms" and "Delete / deactivate a creditor" expanders) was replaced by one unified grid, though the underlying `delete_or_deactivate_creditor()` logic described here is unchanged and still used.
* [x] **Clean up existing duplicate creditors** created by the double-click bug. **Done**: found exactly one duplicate pair in the live DB (`MALAPIT NA MAGAWA`, ids 412/413) — 412 was the zero-reference orphan and was deleted after a timestamped backup; 413 (the one with real history) was kept.

**Not yet validated against a real physical Metrobank check leaf.** Recommend a real test print before relying on this for actual disbursement — the calibration numbers in Admin & Audit will very likely need adjusting once that happens.

**Creditor Master UI consolidated into one unified data_editor grid (2026-09-14)** — replaces the 4 separate expanders above (Add new creditor / Edit a creditor / Update a creditor's payment terms / Delete-deactivate a creditor) with a single `st.data_editor` grid, following the same add/edit/delete-via-Save-button pattern already used by the Invoices and Credit Memo grids elsewhere in this app. Columns: `creditor_id` (read-only), `creditor_name`, `creditor_type` (dropdown: SUPPLIER/LENDER/BANK_LOAN), `tin`, `contact_person`, `contact_number`, `email`, `terms` (dropdown, same `SUPPLIER_TERMS_OPTIONS` as before), `is_active` (checkbox), `created_at` (read-only). A blank row at the bottom + fill Name/Type + Save adds a creditor; editing any cell + Save updates it; unchecking Active + Save deactivates (reversible — check it again + Save to reactivate); using the grid's own row-delete control removes a row outright only if it has no transaction history yet (`delete_or_deactivate_creditor()`), otherwise it's deactivated instead. One deviation from a literal "just rewire the existing helpers" implementation: `creditor_type` is now edited via the same `update_creditor()` call as the rest of a row's fields (extended to take type and terms too, replacing the now-removed standalone `update_creditor_terms()`) _but_ a type change is rejected (with an inline error, rest of the row's edits still applied) if `creditor_reference_counts()` shows the creditor already has transaction history — preserves the original "type changes could silently reclassify existing history" safeguard from the expander-based version instead of dropping it, since the whole point of that guard was real risk, not just leftover caution. Verified via 7 functional-test scenarios against a DB copy (add, type-change allowed pre-history, reference-counts detection, deactivate, reactivate, soft-delete-with-history, hard-delete-without-history) plus live end-to-end on the running app: added a real test row through the grid, edited a real field and toggled Active off/on on an existing creditor (BDO Unibank Commercial Loans), confirmed each via direct DB read, then reverted/cleaned up.

**Follow-up same day:** added a **Search** text box next to the existing type filter (matches against `creditor_name`, `tin`, `contact_person`, `contact_number`, `email`, case-insensitive substring, combined with the type/show-inactive filters — same "filter the source dataframe before it reaches `st.data_editor`" approach as the type filter, so it's safe with the grid's add/edit/delete diffing). Also **promoted the Creditor Master tab** to right after Dashboard (was 3rd, after Order Statement) — `tab_labels` order swapped, `TAB_ORDER_STATEMENT` index updated from `tab_labels[1]` to `tab_labels[2]` accordingly; `TAB_LENDERS`/`TAB_BANK_LOANS` untouched since their positions didn't shift.







---



\## Phase 7 — Invoice Extraction Tool ("Snip and Apply" / Form Extraction Mode)



**Scoped 2026-09-14.**



User request: "Can i have datasnipper like? Especially the extracting invoice like I snip one then it snips the others as well with the same invoice"



\### Purpose & Architecture



- **Standalone Streamlit app** (NOT built into the main AP system) — dedicated invoice extraction tool, runs independently

- **Input:** Digital PDFs + scanned images (mixed batches)

- **Extraction fields:** Invoice number, Invoice date, Total amount only (3 fields)

- **Batch size:** < 50 invoices per batch (typically 5–20)

- **Manual review:** ALWAYS required before import to AP system

- **Cost rationale:** User: "Im thinking this less cost than using ai" — $0 recurring vs. DataSnipper $50-300/month



\### Workflow (User Confirmed Sequence)



1. Scan/receive invoices from suppliers

2. Upload batch (< 50 invoices) to Phase 7 extraction tool

3. **Annotate first invoice:**

&#x20;  - Click-drag to box invoice number → context hint shown (e.g., "Invoice #: INV-2024-001")

&#x20;  - Click-drag to box date → context hint shown (e.g., "Invoice Date: 09/14/2026")

&#x20;  - Click-drag to box amount → context hint shown (e.g., "Total Amount: ₱25,000.00")

4. System learns pattern: bounding-box positions + text-context clues (surrounding words)

5. Auto-extract remaining invoices using learned pattern

6. User reviews editable grid (correct any misses)

7. Export cleaned CSV

8. Create empty batch in AP system (select supplier)

9. **Import CSV via `/api/import/invoices` endpoint** → invoices land as batch line items, total auto-calculates

10. Issue checks (normal AP workflow): date + amount → assign check number from booklet dropdown → print

11. Collect supplier



\### Technology Stack



- **pdfplumber:** digital PDFs — essentially perfect extraction

- **pytesseract:** scanned/image-only PDFs — lower accuracy, why review grid exists

- **Bounding-box + text-proximity heuristics:** deterministic, no ML, no API cost

- **Streamlit UI:** click-drag annotation, context hints, review grid, CSV export



\### Output CSV Schema



- `filename`, `invoice_number`, `invoice_date` (YYYY-MM-DD if parseable, else raw text), `total_amount` (₱/commas stripped, 2dp), `extracted_by` (`manual` for the annotated first invoice / `auto` for the rest / `manual_required` when a file couldn't be read at all), `reviewed_by` (filled in the review grid, null until then), `notes` (OCR/read warnings + which fields came back blank)



\### Implementation Backlog



- [x] `invoice_extractor/extractor_app.py` — Streamlit app (batch upload, click-drag annotation UI with context hints, auto-extract with progress bar, editable review grid, CSV export) — implemented 2026-09-14, not `tools/invoice_extractor.py` as originally sketched (kept the whole tool in its own `invoice_extractor/` folder instead, alongside its own `requirements.txt`, to keep it genuinely standalone)

- [x] `invoice_extractor/invoice_extraction_service.py` — pattern-learning engine (bounding-box tracking, text-context extraction, mixed digital/scanned handling) — implemented 2026-09-14 as a standalone module (not `engines/invoice_extraction_service.py`; this tool imports nothing from the main app's `engines/`)

- [ ] Phase 1 import endpoint (see below) — not started; CSV export is done and matches the schema above, ready to feed it whenever the endpoint exists



**Status (2026-09-14):** Implemented and verified — automated test (`invoice_extractor/test_extraction.py`: digital-PDF extraction, OCR image extraction, pattern learning, a 5-invoice same-template batch auto-extracted with 100% field accuracy, CSV round-trip) plus a live browser run (3-invoice batch, real click-drag mouse actions end to end: upload → annotate → extract pattern → auto-extract → review grid, zero blank fields). Implementation notes for future sessions:

&#x20;  - **Click-drag, not the 2-click fallback:** `streamlit-drawable-canvas` (the library the spec suggested checking for) doesn't work with the Streamlit version installed here (1.62.0) — its component-registration API predates Streamlit's newer one and raises `StreamlitAPIException` on import. Used `streamlit-image-coordinates` instead (added to `invoice_extractor/requirements.txt`), which has a `click_and_drag=True` mode giving genuine mouse-down/mouse-up drag coordinates — no 2-click approximation needed.

&#x20;  - **Geometry, not flat text:** the service module's `get_surrounding_text()`/`learn_pattern_from_first_invoice()`/`auto_extract_field()` take a "page dict" (text + per-word bounding boxes + the rendered image, all in one consistent pixel space) instead of a bare string — a box has no meaningful position or "nearby text" against an already-flattened string. `extract_text_from_file()`'s `'pages'` list is exactly this richer structure (each entry still has a plain `'text'` key too).

&#x20;  - Box-matching on a re-extracted invoice restricts to the single printed line closest to the learned box's vertical center (not just anything inside a padded box) — an early version let a generous vertical pad bridge into an adjacent tightly-leaded line and pull in its label text; line-restriction plus a keyword-anchored fallback (search the whole page for a learned label word, read what follows it) fixed that.

&#x20;  - Real-world testing (2026-09-14) against an actual photographed delivery receipt surfaced and fixed: OCR now runs Tesseract in two page-segmentation modes and merges the word lists (one mode alone can miss text a different one catches — confirmed on the real file), the confirm-value box shows an explicit warning when OCR found nothing there instead of silently staying blank, a numeric "Fine-tune box position" control lets a slightly-off drag be corrected precisely, and a "3. Preview extraction" step now runs the learned pattern across the whole batch and shows the results table before anything is committed (mirrors DataSnipper's own live "Preview of extractions" panel). One OCR case (a red rubber-stamped document number, blurry) was confirmed unfixable after trying crop/upscale/color-channel/multiple-PSM combinations — left to manual entry by design, not a bug.

&#x20;  - **"One invoice per page" mode added** (2026-09-14), matching DataSnipper's own per-document vs. per-page toggle: a batch is normally "one file = one invoice" (only each file's first page is ever read), but a `st.radio` right after upload lets the user switch to "one invoice per page" for a batch that arrived as one or more multi-page PDFs holding several invoices back-to-back (e.g. a combined scan) — `process_batch_per_page()` in `invoice_extraction_service.py` then treats every page of every file as its own row (labeled `"file.pdf (page N)"` for multi-page sources, plain filename for single-page ones), while the first page of the first file remains the one annotated/confirmed, exactly as in the default mode. Additive, not a replacement — the default single-invoice-per-file path is unchanged.



---



\## Phase 1 Infrastructure Addition — Invoice Import Endpoint



**Design now, implement with Phase 7.**



Allows Phase 7 extractor to feed directly into AP system.



\### API Design



- **Endpoint:** `/api/import/invoices` (POST, accepts CSV or JSON)

- **Input:** `invoice_number`, `invoice_date`, `total_amount`, `order_statement_id`, optional `notes`

- **Validation:** dates parse, amounts numeric ≥ 0, batch exists and unlocked, deduplication warning (warn don't reject — split payments are real)

- **Response:** JSON (success count, per-row warnings/errors, inserted IDs)

- **Implementation:** thin wrapper around existing `add_invoice()` pathway — reuses audit trail, balance-recompute trigger, hard payable-ceiling enforcement

- **Graceful partial success:** insert valid rows, report errors per row



\### Implementation Backlog



- [ ] `engines/invoice_import_endpoint.py` — CSV parsing, validation, batch insert

- [ ] `app.py` — mount endpoint (Flask side-car vs Streamlit experimental — deferred to implementation, will ask user)

- [ ] Unit test: valid/invalid rows, dedup warnings, batch-not-found handling



**Flagged for later:** Flask side-car (true REST API, cleanest separation) vs Streamlit `@st.cache_resource` internal endpoint (single-process, experimental) — confirm with user when implementation starts.

\## Phase 7 — Invoice Extraction Tool ("Snip and Apply" / Form Extraction Mode)



**Scoped 2026-09-14. Status: DEFERRED — Not implementing now; Vision-based design locked for future.**



User request: "Can i have datasnipper like? Especially the extracting invoice like I snip one then it snips the others as well with the same invoice"



\### Decision (2026-09-14)

- **Not implementing now:** Manual entry via Phase 1's existing Invoices grid + CSV import sufficient for 100 invoices/month

- **When to revisit:** Invoice volume increases, or manual entry becomes tedious (explicit request from user)

- **Locked design (when implemented):** Claude Haiku Vision API, not local OCR

&#x20; - Cost: \~$0.0018 per invoice (\~$0.18/month @ 100/month, well under $5.60 budget)

&#x20; - Accuracy: 95%+ across varied supplier layouts

&#x20; - Simplicity: No preprocessing, pattern learning, or bounding-box logic needed

&#x20; - User workflow: Upload → Vision extracts 3 fields → review grid → CSV export → import to AP system



\### Rationale for Vision over Local OCR

- Local OCR (Tesseract): 60-80% accuracy, requires preprocessing/fallbacks (3+ hours build time)

- Vision: 95%+ accuracy, 1-hour build time, handles poor scans/rotation/layout variation automatically

- Cost objection doesn't hold: $0.18/month vs. $0 is negligible for accuracy gain + user time saved



\### Phase 1 Infrastructure: Invoice Import Endpoint

**Design now, implement with Phase 7** (when Phase 7 resumes).

- Endpoint: `/api/import/invoices` (POST, CSV or JSON)

- Input: invoice_number, invoice_date, total_amount, order_statement_id, optional notes

- Thin wrapper around existing `add_invoice()` pathway

- See Phase 1 section below for full spec



**Status:** Deferred with Phase 7. Not blocking current work.

