# Slick's ERP — Slick's Tire Supply

The business system of Slick's Tire Supply: buying (price lists, purchase orders, receiving), paying suppliers (invoices, payment statements, checks,
the daily payment limit), bank statements and reconciliation, lenders, bank loans and credit cards, stock and stock counts, sales and collections,
expenses, other income and the month's income statement, analytics and warnings, printouts, phone pages, and the snip (reading a supplier's
document on the page). Django on a SQLite database, live at **https://erp.slickstiresupply.com** for the family and the shop.

The guide for working on it is `CLAUDE.md` (read its "Current State" section first). `WHATS_NEW.md` is the owner-facing list of what each release added.

## Where things are

| Folder / file | What it is |
|---|---|
| `web/` | The Django site: `core` (sign-in, Home, menu, settings, phone approval), `masters` (people, settings), `buying` (price lists, orders, receiving, the snip), `payables` (statements, checks, calendar, bank, lenders, loans, cards, analytics, reports), `selling` (stock, sales, customers, collect), `phone` (the phone pages). Templates sit beside each app; shared CSS/JS in `web/core/static/core/`. |
| `engines/` | The business rules, shared by every page: one module per area (`statement_rules`, `check_rules`, `po_rules`, `day_cap`, `order_room`, `cash_in`, `clearing`, `open_loans`, `card_analysis`, `warn_list`, `snip`, …). No page logic here. |
| `schema.sql` + `migrations/` | The database shape (schema.sql is the truth) and the log of each change (`NNN_name.sql`). `apply_pending_migrations.py` adds what a database lacks; it never alters or drops. |
| `tests/` | `pytest` (~1,450 tests). `app.py` at the root only re-exports engine names for the older tests; new tests import the engines directly. |
| `deploy/` | The server: `deploy_to_server.sh` (release), `release.sh`, `rollback.sh`, `nightly.py` (18:00 backup), `pull_backup.ps1` (the laptop's 7:30 PM off-site copy), systemd units, Caddy template, `env.template`. |
| `docs/` | `SLICKS_ERP_BLUEPRINT.md` (the plan), `modules/` (module notes), `mockups/` (every screen was drawn here first), `how_it_works.html`, `history/` (the pilot guide and the Django proposal, kept for the record). |
| `tools/` | Admin tools run by hand: `fk_check.py`, `mask_audit_text.py`, `load_test_web.py`, `time_pages.py`. |
| `archive/` | Kept for the record, not used: `streamlit/` (the 2026-09 prototype and its screen tests), `local_era/` (the laptop-only scripts), `one_time_scripts/` (migrations that already ran). |
| `data/` | Local databases (never in git): `ap_disbursement_WEB_TEST.db` is the sandbox copy of live. |
| `db_location.py`, `excel_invoice_entry/`, `invoice_extractor/` | Still used: the backup-safe copy helper, the invoice entry workbook, the invoice reader. |

## Everyday commands (from this folder)

```bash
.venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider
```
runs the tests (about 25 minutes). The sandbox is the preview entry `slicks-web-stage-a-8511` (port 8511, restart it after edits). A new
migration is applied to the sandbox with `.venv/Scripts/python.exe apply_pending_migrations.py --db data/ap_disbursement_WEB_TEST.db`.
A release, only when the owner says "deploy":

```bash
MSYS_NO_PATHCONV=1 bash deploy/deploy_to_server.sh slicks@165.245.188.246 --anyway
```

## Rules that never change

The bank statement is the truth. Controls flag, they never block. Nothing is deleted: a wrong entry is taken back with a reason. Every change is
in the audit log, which is sealed. The family's screens use plain words. Nothing is built on live before it has run on the sandbox.
