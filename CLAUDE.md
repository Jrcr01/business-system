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
* **ERP stack (decided 2026-09-28): Python + Django**, Django templates + HTMX + a Tailwind-based component kit; one Django project
  `erp/` with one app per module; SQLite on the shared database for now (PostgreSQL = a settings change later). AP moves to Django
  after Sales is stable, page by page. Not built yet — the skeleton is a Phase F step.
* **Rules for all code (Phase F1):** open the database only through `engines/common.get_conn()` / `write_tx()` (the one door:
  foreign keys, busy timeout, WAL); anything that checks then saves goes in ONE `write_tx()`; business dates/times only from
  `engines/clock.py` (`manila_today()` / `manila_now()`, SQL `manila_today()`), never the computer clock; numbers from
  `next_doc_number()`; `RETURNING` instead of `lastrowid` in new SQL. Tests read views through `get_conn()`.
* Each module gets its own `erp/<module>/CLAUDE.md` (Claude reads it when working in that folder). AP's notes: `docs/modules/ap.md`
  (read before touching AP code — it holds every AP rule and decision).
* Tests: `python -m pytest` (~400; ~1.5 min). Sandbox app: preview `slicks-erp-sandbox-8504`.

## Modules

| Module | Status | Notes |
|---|---|---|
| AP — supplier invoices, batches, checks, lenders, bank loans, cards, bank reconciliation | **Live** (Streamlit, `main`) | `docs/modules/ap.md` |
| Sales + AR — counter sales, payments, credit, collections | **Next** (Django) | plan in the blueprint; `erp/sales/CLAUDE.md` when started |
| Inventory, Affiliates (stock borrow / lend), Purchasing (PO), Rentals, Family / personal + carve-out, BI in every module | Later, owner picks the order | blueprint, module map |

## Foundation (Phase F) — status

Detail: `docs/foundation/phase_f.md`. Done on `erp` (not live): **F1.1** broken-link check (`fk_check.py`), **F1.2** one door + WAL,
**F1.3** one clock (Manila), **F1.4** safe saves (`write_tx`, `doc_counters`). Next: **F1.5** audit "who did it" + all-or-nothing
check-number typing; then Django skeleton + `core` (users / roles, audit, numbering, parties, items, carve-out tags), then server and
logins before the owner's brother uses Sales. Migrations on this branch not yet live: 054 (one-clock views), 055 (`doc_counters`).

## Docs map

* `docs/SLICKS_ERP_BLUEPRINT.md` — the master blueprint (owner-facing).
* `docs/foundation/phase_f.md` — foundation plan and progress.
* `docs/modules/ap.md` — everything about the AP module (was CLAUDE.md until 2026-09-28).
* `MEMORY.md` — the Slicks ERP build log (entries tagged by module).
* `WHATS_NEW.md` — owner-facing change notes (shown in the app).
