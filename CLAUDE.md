# Slicks ERP — guide for every session

> **THIS WORKSPACE = the Slicks ERP BUILD** (folder `C:\Slicks\Slicks_ERP`, git branch `erp`, sandbox app on port 8504 with its own
> database `C:\Slicks\Slicks_ERP\data\ap_disbursement_TEST.db`).
> * **Never** edit `C:\Slicks\AP_System` (the LIVE AP system, branch `main`) or the live database `C:\Slicks\Data\ap_disbursement.db` from here.
>   Never run `update_database.bat` / `restart_live.bat` for ERP work. This folder has no `live_db_path.txt` / `local_settings.bat` on
>   purpose, so its scripts cannot reach live data or the Google Drive backups.
> * After an AP fix lands on `main`: `git merge main` here (AP notes written on main belong in `docs/modules/ap.md`).
> * Everything built here goes live **once**, when the owner approves: merge `erp` into `main`, backup, `update_database.bat`,
>   a before / after checklist of known figures.

**Folders (moved 2026-09-28, owner: one parent folder):** everything lives in `C:\Slicks`: `AP_System` (live AP program,
branch `main`), `Slicks_ERP` (ERP build, branch `erp`), `Data` (live database `ap_disbursement.db`, bank statement copies `bank`,
Drive sign-in `drive`, code bundles `code_backups`). The backup task "AP System Daily Backup" runs
`C:\Slicks\AP_System\run_backup.bat quiet`; `live_db_path.txt` points at `C:\Slicks\Data\ap_disbursement.db`.

## What Slicks ERP is

The one system for Slick's Tire Supply **and the family's affairs**, built module by module ("like a puzzle"). AP (module 1) is live;
**Sales + AR (module 2) is next**. Full picture — principles, architecture, security, UI, controls, module map, the Sales plan and the
decisions log: **`docs/SLICKS_ERP_BLUEPRINT.md`** (read it before planning anything new).

## How the owner works (JRCR) — always

* **Ask before coding.** For every new request, say how you read it and ask the few questions that change what you would build
  (AskUserQuestion, recommended option first). Tiny obvious fixes can go ahead — say so. Never delete or narrow behaviour on an
  ambiguous reading.
* **Gated steps.** Finish one step, test it, stop, summarise in plain language, wait for the owner before the next.
* The owner is not an IT person: plain words, short answers, no jargon without a one-line explanation.
* **The bank statement is the ultimate truth.** Never edit a recorded figure to match — void, reverse or add a tagged adjustment.
* **Never commit real data** (databases, statements, card numbers, passwords, sign-in files). `.gitignore` covers them.
* **Security matters** (see the blueprint's threat -> protection list). **UI matters**: professional yet functional; every new screen
  starts as a mock-up the owner approves.
* Not wanted: a copy of a commercial ERP, general ledger / financial statements, early-pay discounts (not practiced in the PH).

## Stack and layout

* **Now in this folder:** the AP app (Python 3.12 + Streamlit 1.62: `app.py`, `views/`, `engines/`, scripts) + the Phase F work.
* **ERP stack (decided 2026-09-28): Python + Django 5.2 LTS**, Django templates + HTMX + a Tailwind-based component kit; one Django
  project `erp/` with one app per module; SQLite, the SAME file as AP (PostgreSQL = a settings change later). AP moves to Django after
  Sales is stable, page by page. **Skeleton built** (`erp/`, rules in `erp/CLAUDE.md`): Django is installed ONLY in `.venv`
  (`.venv\Scripts\python erp\manage.py ...`; sandbox site `run_erp_sandbox.bat`, port 8505) — never in the main Python (live AP).
* **Rules for all code (Phase F1):** open the database only through `engines/common.get_conn()` / `write_tx()` (the one door:
  foreign keys, busy timeout, WAL); anything that checks then saves goes in ONE `write_tx()`; business dates/times only from
  `engines/clock.py` (`manila_today()` / `manila_now()`, SQL `manila_today()`), never the computer clock; numbers from
  `next_doc_number()`; every `INSERT INTO audit_logs` writes `user_email` = SQL `acting_user()` (`engines/acting_user.py`); the audit log is
  append-only (the database refuses UPDATE / DELETE — never edit or delete audit rows, not even in tests); `RETURNING` instead of `lastrowid` in new SQL. Tests read views through `get_conn()`.
* Each module gets its own `erp/<module>/CLAUDE.md` (Claude reads it when working in that folder). AP's notes: `docs/modules/ap.md`
  (read before touching AP code — it holds every AP rule and decision).
* Tests: `.venv\Scripts\python -m pytest` runs everything (~420; ~2 min); main-Python `python -m pytest` skips the Django tests. Sandbox app: preview `slicks-erp-sandbox-8504`.

## Modules

| Module | Status | Notes |
|---|---|---|
| AP — supplier invoices, batches, checks, lenders, bank loans, cards, bank reconciliation | **Live** (Streamlit, `main`) | `docs/modules/ap.md` |
| Sales + AR — counter sales, payments, credit, collections | **Next** (Django) | plan in the blueprint; `erp/sales/CLAUDE.md` when started |
| Inventory, Affiliates (stock borrow / lend), Purchasing (PO), Rentals, Family / personal + carve-out, BI in every module | Later, owner picks the order | blueprint, module map |

## Foundation (Phase F) — status

Detail: `docs/foundation/phase_f.md`. Done on `erp` (not live): **F1.1** broken-link check (`fk_check.py`), **F1.2** one door + WAL,
**F1.3** one clock (Manila), **F1.4** safe saves (`write_tx`, `doc_counters`), **F1.5** audit "who did it" (`audit_logs.user_email`
via SQL `acting_user()` -- every audit INSERT must include it, a test checks) + all-or-nothing manual check-number typing, **Django skeleton** (`erp/`: Manila time, shared database with the one door's rules, live
guard, security defaults, "it works" page; Django's tables added to the sandbox DB only), **core C1** users / roles / sign-in
(Owner / Sales / Viewer, password now + Google at the server step, 2 h idle sign-out, 5 wrong tries = 15 min lock, Users and Audit
log pages for the owner; detail `erp/CLAUDE.md`), **core C2** audit log locked + daily tamper check (seals with a key outside the
database, run by the daily backup; problems on Data health) + owner-run masking + ERP numbers `2026 - XX 0001`, **core C3**
parties (one list, AP creditors linked read-only, Walk-in customer, Sales sees customers / affiliates only), **core C4** carve-out
tags (Business / Rental / Family-personal + sub-tags, main tag + peso splits on AP card statements / loans / batches, monthly summary;
owner only) -- **core is complete**. **Server step F3** (owner: ERP test site first, DigitalOcean, Google sign-in, new domain):
**F3.1 done** -- production settings, home-screen app, `deploy/` scripts (see `deploy/README.md`). **F3.2-F3.4 paused** (owner:
"we deploy if we are substantially done") until Sales is substantially done (around S5); domain registered then too. Next: **Sales S0** (users / roles, audit, numbering, parties, items, carve-out tags), then server and
logins before the owner's brother uses Sales. Migrations on this branch not yet live: 054 (one-clock views), 055 (`doc_counters`), 056 (`audit_logs.user_email`), 057 (audit lock +
`audit_seals`).

## Docs map

* `docs/SLICKS_ERP_BLUEPRINT.md` — the master blueprint (owner-facing).
* `docs/foundation/phase_f.md` — foundation plan and progress.
* `docs/modules/ap.md` — everything about the AP module (was CLAUDE.md until 2026-09-28).
* `MEMORY.md` — the Slicks ERP build log (entries tagged by module).
* `WHATS_NEW.md` — owner-facing change notes (shown in the app).
