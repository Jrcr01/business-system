"""Shared fixtures for the regression suite.

The one thing every test needs is a real database that isn't the live
data/ap_disbursement.db — every business-rule function in this app talks to
SQLite directly (get_conn() in app.py, or a same-named DB_PATH constant in
each engines/*.py module — there's no shared config, each file hardcodes its
own copy of "data/ap_disbursement.db"), so an isolated DB means patching
DB_PATH on app.py *and* every engines module that has one, not just one place.
"""
import os
import sqlite3
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(PROJECT_ROOT, "schema.sql")
sys.path.insert(0, PROJECT_ROOT)

# app.py's top-level script body (every `with tab_X:` block, Streamlit tabs are plain sequential
# code, not lazily rendered) runs immediately on import, against whatever AP_DB_PATH resolves to
# at that moment -- which, unpatched, is the real data/ap_disbursement.db file (2026-09-17: this
# broke test collection the moment schema.sql gained a table that file doesn't have yet, since
# live only gets new schema via the Phase 8b promotion script, not automatically). Tests should
# never touch live data even implicitly at import time, so this points bare import at a private
# scratch db instead -- each test's own `db` fixture (below) still overrides this per-test with
# its own fully isolated tmp_path database; this only covers the brief window before that fixture
# runs.
_IMPORT_TIME_DB_PATH = os.path.join(PROJECT_ROOT, "data", "_pytest_import_scratch.db")
# Rebuilt whenever schema.sql is newer than the scratch file, so a newly added table never breaks bare import (2026-09-27).
if os.path.exists(_IMPORT_TIME_DB_PATH) and os.path.getmtime(SCHEMA_PATH) > os.path.getmtime(_IMPORT_TIME_DB_PATH):
    os.remove(_IMPORT_TIME_DB_PATH)
if not os.path.exists(_IMPORT_TIME_DB_PATH):
    _scratch_conn = sqlite3.connect(_IMPORT_TIME_DB_PATH)
    with open(SCHEMA_PATH, encoding="utf-8") as _f:
        _scratch_conn.executescript(_f.read())
    _scratch_conn.commit()
    _scratch_conn.close()
os.environ["AP_DB_PATH"] = _IMPORT_TIME_DB_PATH

import app  # noqa: E402
import engines.analytics_service as analytics_service  # noqa: E402
import engines.db_engine as db_engine  # noqa: E402
import engines.backup_service as backup_service  # noqa: E402
import engines.bank_loan_report_service as bank_loan_report_service  # noqa: E402
import engines.bank_loan_service as bank_loan_service  # noqa: E402
import engines.batch_statement_service as batch_statement_service  # noqa: E402
import engines.cc_service as cc_service  # noqa: E402
import engines.check_engine as check_engine  # noqa: E402
import engines.check_run_service as check_run_service  # noqa: E402
import engines.park_draft_service as park_draft_service  # noqa: E402
import engines.global_search_service as global_search_service  # noqa: E402
import engines.checkbook_service as checkbook_service  # noqa: E402
import engines.dashboard_kpi_service as dashboard_kpi_service  # noqa: E402
import engines.duplicate_service as duplicate_service  # noqa: E402
import engines.duplicate_scan_service as duplicate_scan_service  # noqa: E402
import engines.invoice_template_service as invoice_template_service  # noqa: E402
import engines.overview_report_service as overview_report_service  # noqa: E402
import engines.interest_service as interest_service  # noqa: E402
import engines.loan_note_service as loan_note_service  # noqa: E402
import engines.loan_schedule_service as loan_schedule_service  # noqa: E402
import engines.reports_service as reports_service  # noqa: E402
import engines.search_service as search_service  # noqa: E402
import engines.soa_matcher as soa_matcher  # noqa: E402
import engines.validators as validators  # noqa: E402

# Every module with its own DB_PATH global (app, every engines.* module) is pointed at the test database. Found
# automatically (2026-09-27): every engines module is imported here, so a new module can never be forgotten -- a
# forgotten one would silently keep pointing at the real production database.
import importlib  # noqa: E402
import pkgutil  # noqa: E402

import engines  # noqa: E402

for _mod in pkgutil.iter_modules(engines.__path__):
    importlib.import_module(f"engines.{_mod.name}")


def _db_path_modules() -> list:
    return [m for name, m in list(sys.modules.items()) if m is not None and (name == "app" or name.startswith("engines.")) and hasattr(m, "DB_PATH")]


@pytest.fixture
def db(tmp_path):
    """A fresh SQLite database built from the real schema.sql, isolated to this
    one test. Yields the file path (tests needing a raw connection for setup
    can use it directly); every app/engines function called during the test
    transparently uses it too, via the DB_PATH patch below.

    Also clears st.cache_data (q() is @st.cache_data-decorated with a 15s TTL —
    without clearing, a later test could read a stale result cached by an
    earlier one under an identical query+params) and st.session_state
    (guard_duplicate_submit() and the sidebar's _jump() helper both stash
    state there, keyed by name, not by DB — so it persists across tests in the
    same pytest process unless cleared)."""
    db_path = str(tmp_path / "test.db")
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema_sql = f.read()
    conn = sqlite3.connect(db_path)
    conn.executescript(schema_sql)  # schema.sql itself seeds bank_account_id=1 (app.PRIMARY_BANK_ACCOUNT_ID)
    conn.commit()
    conn.close()

    originals = [(m, m.DB_PATH) for m in _db_path_modules()]
    for m, _ in originals:
        m.DB_PATH = db_path

    yield db_path

    for m, orig in originals:
        m.DB_PATH = orig


def make_creditor(db_path: str, name: str = "Test Supplier", creditor_type: str = "SUPPLIER", terms: str = "30") -> int:
    """Inserts a minimal creditor directly (bypassing add_creditor()'s own
    logic, which several tests exercise separately) and returns its id --
    almost every test needs at least one creditor to hang invoices/checks off."""
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO creditors (creditor_name, creditor_type, terms) VALUES (?, ?, ?)",
        (name, creditor_type, terms),
    )
    conn.commit()
    creditor_id = cur.lastrowid
    conn.close()
    return creditor_id


def make_statement(db_path: str, creditor_id: int) -> int:
    """Inserts a minimal Draft order_statement directly and returns its id."""
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO order_statements (creditor_id, statement_ref, total_amount, status) VALUES (?, 'TEST-REF', 0, 'Draft')",
        (creditor_id,),
    )
    conn.commit()
    statement_id = cur.lastrowid
    conn.close()
    return statement_id


def bank_clear(db_path: str, *check_ids: int, on=None) -> None:
    """The bank cleared these checks (on their own date unless `on` is given), and their batches and loans follow -- the clearing rule
    (engines/clearing.py, owner 2026-10-08): a check is paid only when cleared, never by its date alone."""
    conn = sqlite3.connect(db_path)
    for cid in check_ids:
        conn.execute("UPDATE checks_issued SET bank_cleared_date = COALESCE(?, check_date) WHERE check_id = ?", (str(on) if on else None, cid))
    conn.commit()
    conn.close()
    from engines.clearing import resync
    resync(list(check_ids))


def fetchone(db_path: str, sql: str, params: tuple = ()):
    conn = sqlite3.connect(db_path)
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return row


# ---- Slick's on Django (web/, stage D0) ----------------------------------------------------------------------------------------
@pytest.fixture(scope="session")
def web_setup():
    """Django set up once per test run. Django keeps no tables, so each test's own `db` fixture is the database the site uses (the engines read it)."""
    pytest.importorskip("django", reason="Django is installed in the project environment (.venv); run the suite with .venv/Scripts/python")
    os.environ["WEB_DB_PATH"] = _IMPORT_TIME_DB_PATH.replace("_pytest_import_scratch", "_pytest_web_TEST")
    os.environ["WEB_DEBUG"] = "1"
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    web_dir = os.path.join(PROJECT_ROOT, "web")
    if web_dir not in sys.path:
        sys.path.insert(0, web_dir)
    import django
    from django.test.utils import setup_test_environment, teardown_test_environment

    django.setup()
    setup_test_environment()
    yield
    teardown_test_environment()


@pytest.fixture(autouse=True)
def _po_request_rule(request, monkeypatch):
    """Owner, 2026-10-11: a draft becomes a PO only after it was sent to the supplier and they confirmed (po_rules.REQUEST_FIRST). The tests of the later
    steps (receiving, matching, cash, the trail) make their PO with order_po straight from a draft, so the rule is off for them; the tests of the rule
    itself (test_web_po_request.py) run with it on, as the system does."""
    if not request.module.__name__.endswith("test_web_po_request"):
        import engines.po_rules as po_rules
        monkeypatch.setattr(po_rules, "REQUEST_FIRST", False)


@pytest.fixture(autouse=True)
def _home_fresh(monkeypatch):
    """Home's figures are kept up to a minute on the server (engines/home_tiles.KEEP_SECONDS); a test reads them fresh every time. An environment
    switch, so nothing is imported before the test's own database is set."""
    monkeypatch.setenv("SLICKS_HOME_KEEP", "0")


@pytest.fixture(autouse=True)
def _stock_counted(request, monkeypatch):
    """Owner, 2026-10-18: "Stock not counted yet" is on until the opening count (settings_registry.stock_not_counted). The stock tests were written for
    counted stock, so the switch is off for them; test_stock_not_counted.py runs with it on, as the system does now."""
    if not request.module.__name__.endswith(("test_stock_not_counted", "test_counter_items")):
        import engines.settings_registry as sreg
        monkeypatch.setattr(sreg, "stock_not_counted", lambda: False)
        monkeypatch.setattr(sreg, "stock_out_on_phone", lambda: True)          # the phone tests were written with Stock out shown
    if not request.module.__name__.endswith("test_review_batch"):
        monkeypatch.setenv("SLICKS_ODD_CHECKS", "0")                            # invoices with fixed and future dates in the older tests


@pytest.fixture
def web(web_setup, db):
    """A browser for the Django site on this test's fresh database."""
    from django.test import Client
    return Client()
