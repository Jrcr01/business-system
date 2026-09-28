# Phase F -- Foundation of Slicks ERP

> Moved from the AP CLAUDE.md on 2026-09-28. Status and build order are summarised in `CLAUDE.md`; the full picture of the
> ERP (stack = Django, modules, security, UI) is in `docs/SLICKS_ERP_BLUEPRINT.md`, which updates the parts of this plan that
> assumed "Streamlit stays" (F2 logins, F3 server).

## Original plan (scoped 2026-09-28) and progress

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
* Laptop-only pieces: thermal printing (USB / Bluetooth / office LAN cannot be reached from a cloud server), `C:\Slicks\Data` paths (bank
  statement copies, Drive sign-in files), `.bat` / `.ps1` / Windows Task Scheduler (backup, link sweep, start / stop).
* Google Drive sign-in expires every 7 days (OAuth app in Testing) -> on a server nobody is there to re-run `drive_connect.bat`.

### Build order
**F1 -- One door, one clock, safe writes (on the laptop; no visible change for the owner).**
**Where Phase F is built (owner, 2026-09-28: "important for now is ap system is stable and can be used"):** ALL of Phase F, up to the
Sales/AR module, is built on the git branch **`erp`** in its own folder **`C:\Slicks\Slicks_ERP`** (a git worktree) and tested on its
own sandbox database (`C:\Slicks\Slicks_ERP\data\ap_disbursement_TEST.db`) -- never in `C:\Slicks\AP_System`, which the LIVE app runs from
(branch `main`). F1.2 and F1.3 were first committed on main (0513ff9, 0e2c0b2), then reverted on main the same day before the live app
ever loaded them; they continue on `erp`. Apply to live ONCE when the owner is satisfied (merge erp into main, one backup,
update_database.bat, a checklist of known figures before / after). AP fixes are made on main and merged into erp.

*Progress (split into gated steps 2026-09-28): **F1.1 done** -- `fk_check.py` (read-only, runs `PRAGMA foreign_key_check` on a backup-API
copy; exit 0 clean / 1 found / 2 unreadable; tests `tests/test_fk_check.py`): live and TEST = 33 tables, 41 declared links, **0 broken**.
**F1.2 done** -- the one door is `engines/db_engine.open_db(path)` (the only `sqlite3.connect` in engines / views / app, besides
`backup_service` reading UPLOADED files read-only): `foreign_keys = ON`, `busy_timeout = 10000`, `journal_mode = WAL` (once per file per
run). `common.get_conn(db_path=None)` returns it; the 95 engine connections became `get_conn(DB_PATH)` -- each module passes its OWN
DB_PATH, so tests / `tidy_up_data.py` pointing one module at another file still work. WAL-safe copies: `db_location.safe_copy(src, dst)`
(backup API, copy set to `journal_mode = DELETE`) for the in-app restore's safety copy, `update_database.bat` and `tidy_up_data.py`
backups; the in-app restore copies the upload INTO the live file with the backup API (never over it); the nightly backup file is set to
DELETE mode (no -wal / -shm on Google Drive). The live / TEST folders now hold `<db>-wal` and `<db>-shm` beside the database while the app
runs -- normal, never delete them. Measured on a copy of live: a save ~5x faster, open + read ~1 ms slower. Tests
`tests/test_one_door.py` (settings, the door rule, links enforced, safe_copy includes saves still in -wal, restore with -wal present).
Known, not from F1.2: Arrow "could not convert ''" warning for `principal_amount` on a loans page (df_query's fillna('')).
**F1.3 done** -- one clock: `engines/clock.py` (`manila_today()`, `manila_now()` = naive Manila time via zoneinfo `Asia/Manila`, tzdata
pinned; fixed UTC+8 fallback; `set_fake_now()` for tests). `open_db()` registers SQL functions `manila_today()` / `manila_now()` on every
connection. Replaced ~100 `date.today()` / `datetime.now()` in engines / views / app / backup + maintenance scripts, and every SQL
`DATE('now','localtime')` -> `manila_today()`, `JULIANDAY('now','localtime')` -> `JULIANDAY(manila_now())`, stored UTC times
`datetime(x,'localtime')` -> `datetime(x,'+8 hours')` (checked on live: 533 audit times identical). Views `v_effective_checks` (also fixes
the Stale rule, which used UTC) and `v_cc_due_alerts` use the functions -- migration 054 (live via update_database.bat); those two views
can only be read through the program (an outside SQLite viewer lacks the functions). Tests that read views must use `get_conn`.
Tests `tests/test_one_clock.py` (clock pinned to 07:30 Manila 2030-01-15: due-today, bank-loan cleared rule, aging, planner; no code
reads the computer clock).
**F1.4 done** -- safe writes: `db_engine.write_tx(path)` / `common.write_tx()` = ONE locked step (`BEGIN IMMEDIATE` ... commit, rollback
on error; code inside must not call `conn.commit()`). Converted: `issue_check`, `issue_checks_grid` (payable ceiling now inside the lock),
`apply_check_correction`, `allocate_check_to_invoices`, `assign_check_number`, `assign_check_numbers_from_booklet` / `_manual`,
`retract_check_number`, `lock_statement`, `create_order_statement` / `create_loan_note` (draft ref picked inside the lock:
`new_draft_ref(..., conn)`), `promote_draft_statement` / `promote_draft_loan_note`, bank `import_statement` (re-checks "already imported"
inside the lock). Numbering: table `doc_counters` (series 'OS' / 'LN', year, last_no; migration 055) via
`db_engine.next_doc_number(conn, series, year, in_use)` = max(counter, highest on file) + 1 -- never reused. New / touched INSERTs use
`RETURNING`. Proof: the old code (main), two PHP 800 checks at once on a PHP 1,000 batch -> both issued (PHP 1,600); new code -> one
refused. Tests `tests/test_safe_writes.py` (real threads, a pause injected between check and save). Known, left as is: some
multi-row functions `return False` part-way inside the step and so keep the rows saved before it (same as before F1.4).
**F1.5 done** -- who did it: `audit_logs.user_email` (migration 056); `engines/acting_user.py` (`acting_user()` = the person set by
`acting_as(email)` for a block of work, else `OWNER_EMAIL` -- owner's choice 2026-09-28: their Google email, the same one that becomes
their login; a ContextVar, so two people at once keep their own names). `open_db()` registers the SQL function `acting_user()` and all 51
audit INSERTs write `user_email = acting_user()`, so the save functions need no extra argument (the plan's "every write function takes the
acting user" is done by the context instead); F2 only has to call `acting_as()` per page run. `apply_pending_migrations.COLUMN_BACKFILLS`
fills the old rows with the owner in the same run that adds the column (TEST: 404 rows). "Who" column on Admin -> Audit log and the
Dashboard's recent audit log. All or nothing: `assign_check_numbers_manual` raised nothing before -- its early `return False` inside the
locked step kept the numbers saved before the bad one; now it raises, so the step rolls back (same as the booklet version). The other
multi-row functions noted under F1.4 are left as they are (owner: this step = check-number typing only). Tests `tests/test_audit_who.py`
(incl. a guard: no audit INSERT without user_email).
**Django skeleton done** (2026-09-28; owner's choices: same database file, a plain "it works" page, Django 5.2 LTS) -- `erp/` (see
`erp/CLAUDE.md`): Django only in `.venv` (`--system-site-packages`, pins `requirements-erp.txt`), main Python untouched. Settings:
Manila time zone, SQLite `OPTIONS` = the one door (foreign keys, busy timeout 10 s, WAL, `transaction_mode IMMEDIATE`), `core/db.py`
adds `manila_today()` / `manila_now()` / `acting_user()` on every connection; refuses `ap_disbursement.db` without `ERP_LIVE=1`;
secret key from env or gitignored `erp/.secret_key`; HTTPS-only settings when not DEBUG (`check --deploy` clean); no admin yet.
Home page reads the shared database (creditors, checks not yet due through `v_effective_checks`). `migrate` run on the sandbox DB
only (Django's `auth_*` / `django_*` tables; afterwards apply_pending_migrations = up to date, fk_check = 0 broken). Go-live will
need `manage.py migrate` in update_database.bat (not added yet). Tests `tests/test_erp_skeleton.py`. Next: `core`.*
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
