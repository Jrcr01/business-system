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
* **Going live (later, owner's go-ahead):** `update_database.bat` will also need `manage.py migrate` — not added yet.
* UI: every new screen starts as a mock-up the owner approves. The skeleton's home page is a plain "it works" page only.
