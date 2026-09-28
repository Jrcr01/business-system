"""Slicks ERP Django skeleton (2026-09-28): the new program runs on Manila time, on the SAME database file as AP, with the one door's
rules (foreign keys, wait-when-busy, WAL, one save at a time) and the same SQL functions, never opens the live file by accident, and
has the security defaults on. Runs in the ERP environment: .venv\\Scripts\\python -m pytest (main Python skips this file)."""
import os
import sqlite3
import subprocess
import sys
from datetime import datetime

import pytest

pytest.importorskip("django", reason="Django is installed only in the ERP environment (.venv) -- run the suite with .venv\\Scripts\\python")

from conftest import PROJECT_ROOT, SCHEMA_PATH  # noqa: E402
from engines.acting_user import OWNER_EMAIL  # noqa: E402
from engines.clock import set_fake_now  # noqa: E402

ERP_DIR = os.path.join(PROJECT_ROOT, "erp")
MANAGE = os.path.join(ERP_DIR, "manage.py")


@pytest.fixture(scope="module")
def erp(tmp_path_factory):
    """Django set up once for this file, on a fresh database built from schema.sql (+ Django's own tables via migrate)."""
    db_path = str(tmp_path_factory.mktemp("erp") / "erp_test.db")
    conn = sqlite3.connect(db_path)
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.execute("INSERT INTO creditors (creditor_name, creditor_type) VALUES ('Skeleton Supplier', 'SUPPLIER')")
    conn.commit()
    conn.close()

    os.environ["ERP_DB_PATH"] = db_path
    os.environ["ERP_DEBUG"] = "1"
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    if ERP_DIR not in sys.path:
        sys.path.insert(0, ERP_DIR)
    import django
    from django.core.management import call_command

    from django.test.utils import setup_test_environment, teardown_test_environment

    django.setup()
    setup_test_environment()              # the test client's host is allowed; page context is recorded
    call_command("migrate", verbosity=0)
    yield db_path
    from django.db import connections

    connections.close_all()
    teardown_test_environment()


def _run_manage(args, **env):
    full_env = {k: v for k, v in os.environ.items() if not k.startswith("ERP_")}
    full_env.update(env)
    return subprocess.run([sys.executable, MANAGE, *args], capture_output=True, text=True, env=full_env, timeout=120)


def test_settings_manila_time_and_one_save_at_a_time(erp):
    from django.conf import settings

    assert settings.TIME_ZONE == "Asia/Manila" and settings.USE_TZ
    db = settings.DATABASES["default"]
    assert db["NAME"] == erp
    assert db["OPTIONS"]["transaction_mode"] == "IMMEDIATE"
    assert "django.contrib.admin" not in settings.INSTALLED_APPS          # no login pages until logins are designed


def test_connection_has_the_one_door_settings_and_sql_functions(erp):
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute("PRAGMA foreign_keys")
        assert cur.fetchone()[0] == 1
        cur.execute("PRAGMA busy_timeout")
        assert cur.fetchone()[0] == 10000
        cur.execute("PRAGMA journal_mode")
        assert cur.fetchone()[0].lower() == "wal"
        set_fake_now(datetime(2030, 1, 15, 7, 30))          # 07:30 Manila = 23:30 UTC the day before
        try:
            cur.execute("SELECT manila_today(), acting_user()")
            assert cur.fetchone() == ("2030-01-15", OWNER_EMAIL)
        finally:
            set_fake_now(None)


def test_django_tables_sit_beside_ap_tables_and_ap_schema_is_still_complete(erp):
    import apply_pending_migrations

    conn = sqlite3.connect(erp)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()
    assert {"auth_user", "django_session", "django_migrations"} <= tables
    assert {"creditors", "checks_issued", "audit_logs"} <= tables
    report = apply_pending_migrations.diff_and_sync(erp, dry_run=True)
    assert report["tables_added"] == [] and report["columns_added"] == []


def test_home_page_shows_manila_date_and_reads_the_shared_database(erp):
    from django.test import Client

    set_fake_now(datetime(2030, 1, 15, 7, 30))
    try:
        resp = Client().get("/")
    finally:
        set_fake_now(None)
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Jan 15, 2030" in html and "7:30 AM" in html
    assert "erp_test.db" in html and erp not in html                  # the file name, never the full path
    assert resp.context["creditors"] == 1
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_home_page_is_read_only(erp):
    from django.test import Client

    assert Client().post("/").status_code in (403, 405)                 # CSRF or method refused; nothing is saved


def test_refuses_the_live_database_without_erp_live(tmp_path):
    live_named = str(tmp_path / "ap_disbursement.db")
    result = _run_manage(["check"], ERP_DB_PATH=live_named)
    assert result.returncode != 0 and "Refusing to open the LIVE database" in result.stderr


def test_server_security_check_is_clean(tmp_path):
    result = _run_manage(["check", "--deploy", "--fail-level", "WARNING"], ERP_DB_PATH=str(tmp_path / "x_TEST.db"),
                         ERP_SECRET_KEY="k" * 20 + "-deploy-check-only-" + "z" * 30, ERP_ALLOWED_HOSTS="app.example.com")
    assert result.returncode == 0, result.stdout + result.stderr
