# Slicks ERP — Django project (`erp/`)

Skeleton built 2026-09-28 (Django **5.2 LTS**, owner's choice). Read the repo `CLAUDE.md` and `docs/SLICKS_ERP_BLUEPRINT.md` first.

* **Run / test only with the ERP environment** `C:\Slicks\Slicks_ERP\.venv` (Django lives only there; the laptop's main Python runs
  the live AP and must never get ERP packages): `.venv\Scripts\python erp\manage.py <command>`, `.venv\Scripts\python -m pytest`.
  Sandbox site: `run_erp_sandbox.bat` → http://127.0.0.1:8505 (preview `slicks-erp-django-8505`). Pins: `requirements-erp.txt`.
* **Layout:** `config/` = settings / urls / wsgi / asgi; one app per module (`core` now; `sales`, `ar`, `inventory` ... later), each
  with its own `CLAUDE.md` once it has rules.
* **One database file, shared with AP** (owner's choice): Django's own tables (`auth_*`, `django_*`) and new module tables (prefixes
  `core_`, `sales_`, `ar_` ...) sit beside AP's. **Django never creates, changes or migrates AP's tables** — AP's schema stays in
  `schema.sql` / `apply_pending_migrations.py`. To use an AP table in Django: `managed = False` models or plain SQL, read-only
  unless the step says otherwise.
* **The one door's rules, Django version** (`config/settings.py`): foreign keys ON, busy timeout 10 s, WAL, `transaction_mode
  IMMEDIATE` (every save takes the lock first — do check-then-save inside one `transaction.atomic()`). `core/db.py` adds the SQL
  functions `manila_today()` / `manila_now()` / `acting_user()` to every connection (AP's views need them).
* **One clock:** business dates from `engines/clock.py` (`manila_today()` / `manila_now()`), never `date.today()` /
  `timezone.now().date()`. **Who did it:** `engines/acting_user.py`.
* **Live guard:** settings refuse a database file named `ap_disbursement.db` unless `ERP_LIVE=1`. Never set it in this workspace.
* **Security defaults:** secret key from `ERP_SECRET_KEY` or the gitignored `erp/.secret_key`; `DEBUG` only with `ERP_DEBUG=1`;
  HTTPS-only cookies / redirect / HSTS when not DEBUG; `check --deploy` must stay clean (a test runs it). No `django.contrib.admin`
  until logins are designed.
* **Going live (later, owner's go-ahead):** `update_database.bat` will also need `manage.py migrate` — not added yet — and the owner
  runs `create_owner` once on the live database.
* UI: every new screen starts as a mock-up the owner approves. Approved look (C1): navy sidebar grouped by module, top bar with the
  Manila date / role / sign-out, white cards, one main blue button (`core/static/core/erp.css`, templates extend
  `core/base.html`). The Tailwind-based kit comes with the Sales screens.

## core (step C1 done 2026-09-28 — users, roles, sign-in)
* `core.User` (`AUTH_USER_MODEL`, table `core_user`): email = sign-in name (and the audit "who"), one `role` of Owner / Sales /
  Viewer, `is_active` (never deleted), `must_change_password` (temporary passwords from the owner). `LoginAttempt` drives the lock.
* **Every page needs a sign-in** (`LoginRequiredMiddleware`; only the sign-in page is `@login_not_required`) and **every page but
  home / password / sign-out declares `@role_required(...)`** (a test checks); the menu is `core/roles.NAV` filtered by role.
* Owner's rules: signed out after **2 h idle** (`SESSION_COOKIE_AGE`, sliding); **5 wrong tries = 15 min lock** per email (the right
  password is refused while locked); passwords >= 10 characters; the **audit log is owner only**; the last active owner can't be
  deactivated or demoted.
* Audit: `core.audit.log(...)` writes the shared `audit_logs` with `acting_user()`; `ActingUserMiddleware` sets the signed-in
  email per request, `anonymous` when nobody is signed in (never the owner by default). Every sign-in / wrong try / lock / sign-out /
  user change is logged.
* First owner: `manage.py create_owner --email ... --name ...` (asks for the password). Sandbox test users:
  `manage.py seed_sandbox_users` (only a `_TEST` database; sign-ins in the gitignored `erp/.sandbox_logins.txt`, never in chat).
* Tests use a fast password hasher (conftest); real passwords use Django's default slow hashing.

## core (step C2 done 2026-09-28 — audit protection, numbering)
* **The audit log is append-only** (owner: "lock + tamper check"): triggers refuse UPDATE / DELETE on `audit_logs` and `audit_seals`
  (only the one-time `user_email` fill of a row that has none). Never write code or tests that edit or delete audit rows — tests
  look at rows after a `log_id` marker instead. Hiding private data: `mask_audit_text.py` only (owner-run, preview then `--apply`,
  backup first, mask = asterisks + at most 4 kept characters, logs `AUDIT_MASKED`).
* **Tamper check:** `engines/audit_seal.py` — an HMAC per row with a key OUTSIDE the database (`audit_seal.key` beside the database
  file, plus `audit_seal.state` = how many rows up to which id). `seal_and_verify()` runs in the daily backup (before the copy);
  the monthly restore test verifies the backup. Problems (changed / deleted / unsealed / cut off / lock removed / key missing) show on
  AP's Data health ("Audit log" group) and the owner's ERP Home; new rows stay unsealed until the owner has looked.
* **Numbers:** `core.numbering` — `register("CR", "Collection receipt")` once per document type (2 capital letters; OS / LN belong to
  AP), then `next_number("CR")` INSIDE the `transaction.atomic()` that saves the document → `2026 - CR 0001` (Manila year, restarts
  yearly). Same `doc_counters` table as AP. Voided documents keep their number (never delete).
* Next in core: C3 parties (one shared list linked to AP's creditors), C4 carve-out tags. Items (tires) come after the Sales
  walk-through (S0/S1), not in core.
