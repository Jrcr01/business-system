"""Server step F3.1 (2026-09-28): the ERP is ready for the server -- production settings, the home-screen app, and the deploy files.
  * the app files (manifest, service worker, offline page) open without signing in; the service worker keeps only the look and the
    offline page, never business pages (a lost phone holds no records);
  * every page carries the app head; the test site shows its banner;
  * production static files (whitenoise, hashed names) and the trusted HTTPS origins come from the server's settings file;
  * the nightly job checks + seals the audit log and backs up (newest N kept);
  * the server scripts are valid bash, stop on the first error, use Linux line endings and hold no secrets."""
import glob
import json
import os
import shutil
import sqlite3
import subprocess
import sys

import pytest

pytest.importorskip("django", reason="Django is installed only in the ERP environment (.venv)")

from conftest import PROJECT_ROOT, SCHEMA_PATH  # noqa: E402

DEPLOY = os.path.join(PROJECT_ROOT, "deploy")
MANAGE = os.path.join(PROJECT_ROOT, "erp", "manage.py")


def _env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("ERP_")}
    env.update(extra)
    return env


# ---- home-screen app ------------------------------------------------------------------------------------------------------------

def test_app_files_open_without_signing_in(erp):
    from django.test import Client

    c = Client()
    m = c.get("/manifest.webmanifest")
    assert m.status_code == 200 and m["Content-Type"].startswith("application/manifest+json")
    data = json.loads(m.content)
    assert data["display"] == "standalone" and data["start_url"] == "/" and len(data["icons"]) == 3
    assert any(i.get("purpose") == "maskable" for i in data["icons"])
    sw = c.get("/sw.js")
    assert sw.status_code == 200 and sw["Content-Type"].startswith("application/javascript") and sw["Service-Worker-Allowed"] == "/"
    assert c.get("/offline/").status_code == 200


def test_the_service_worker_never_keeps_business_pages(erp):
    import re

    from django.test import Client

    js = Client().get("/sw.js").content.decode()
    keep = json.loads(re.search(r"const KEEP = (\[.*?\]);", js).group(1).replace("'", '"'))
    assert keep and all(p.startswith("/static/") or p == "/offline/" for p in keep)
    assert 'req.mode === "navigate"' in js and "fetch(req).catch" in js          # pages always from the server


def test_every_page_carries_the_app_head(erp):
    from django.test import Client

    html = Client().get("/sign-in/").content.decode()
    for piece in ('rel="manifest"', 'name="theme-color"', "apple-touch-icon", "serviceWorker"):
        assert piece in html


def test_the_test_site_shows_its_banner(erp):
    from django.test import Client, override_settings

    with override_settings(SITE_LABEL="TEST SITE"):
        assert "TEST SITE — practice data" in Client().get("/sign-in/").content.decode()
        assert json.loads(Client().get("/manifest.webmanifest").content)["short_name"] == "Slicks TEST"
    with override_settings(SITE_LABEL=""):
        assert "practice data" not in Client().get("/sign-in/").content.decode()


# ---- production settings --------------------------------------------------------------------------------------------------------

def test_trusted_origins_and_hosts_come_from_the_servers_settings(tmp_path):
    code = "from django.conf import settings; print(settings.CSRF_TRUSTED_ORIGINS, settings.ALLOWED_HOSTS, settings.DEBUG)"
    out = subprocess.run([sys.executable, MANAGE, "shell", "-c", code], capture_output=True, text=True, timeout=120,
                         env=_env(ERP_DB_PATH=str(tmp_path / "x_TEST.db"), ERP_SECRET_KEY="k" * 60,
                                  ERP_ALLOWED_HOSTS="test.example.com, app.example.com"))
    assert out.returncode == 0, out.stderr
    assert "['https://test.example.com', 'https://app.example.com']" in out.stdout and "False" in out.stdout


def test_production_static_files_are_collected_with_hashed_names(tmp_path):
    root = tmp_path / "static"
    out = subprocess.run([sys.executable, MANAGE, "collectstatic", "--noinput", "-v", "0"], capture_output=True, text=True, timeout=180,
                         env=_env(ERP_DB_PATH=str(tmp_path / "x_TEST.db"), ERP_SECRET_KEY="k" * 60, ERP_STATIC_ROOT=str(root)))
    assert out.returncode == 0, out.stderr
    assert (root / "staticfiles.json").exists()
    assert glob.glob(str(root / "core" / "erp.*.css")) and glob.glob(str(root / "core" / "icons" / "icon-512.*.png"))


# ---- nightly job ----------------------------------------------------------------------------------------------------------------

def test_the_nightly_job_checks_the_audit_log_and_keeps_the_newest_backups(tmp_path):
    db = tmp_path / "data" / "site_TEST.db"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name) VALUES ('x@example.com', 'TEST', 'x')")
    conn.commit()
    conn.close()
    backups = tmp_path / "backups"
    backups.mkdir()
    for i in range(3):
        shutil.copy(db, backups / f"slicks_erp_backup_2020010{i}_000000.db")
    out = subprocess.run([sys.executable, os.path.join(DEPLOY, "nightly.py")], capture_output=True, text=True, timeout=120,
                         env=_env(ERP_DB_PATH=str(db), ERP_BACKUP_DIR=str(backups), ERP_BACKUP_KEEP="2"))
    assert out.returncode == 0, out.stderr
    assert "Audit log check: OK (1 new sealed" in out.stdout
    kept = sorted(os.listdir(backups))
    assert len(kept) == 2 and not kept[-1].startswith("slicks_erp_backup_2020")                  # tonight's is kept
    assert (db.parent / "audit_seal.key").exists()


# ---- deploy files ---------------------------------------------------------------------------------------------------------------

SCRIPTS = ["bootstrap_server.sh", "release.sh", "deploy_to_server.sh", "upload_test_data.sh"]


@pytest.mark.parametrize("name", SCRIPTS)
def test_server_scripts_are_valid_bash_and_stop_on_errors(name):
    path = os.path.join(DEPLOY, name)
    with open(path, "rb") as f:
        raw = f.read()
    assert b"\r\n" not in raw, "must use Linux line endings"
    assert b"set -euo pipefail" in raw
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash on this computer")
    out = subprocess.run([bash, "-n", path], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_deploy_files_hold_no_secrets():
    for path in glob.glob(os.path.join(DEPLOY, "*")):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for line in text.splitlines():
            if "SECRET_KEY=" in line:
                assert line.strip().endswith("=__SECRET__"), f"{path}: {line}"
        assert "BEGIN OPENSSH PRIVATE KEY" not in text and "v.jrcroxas" not in text


def test_the_service_only_listens_on_the_server_itself():
    with open(os.path.join(DEPLOY, "slicks-erp.service"), encoding="utf-8") as f:
        unit = f.read()
    assert "--bind 127.0.0.1:8000" in unit and "User=slicks" in unit and "ProtectSystem=strict" in unit
