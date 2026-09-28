"""Slicks ERP settings (Django skeleton, 2026-09-28). One Django project, one app per module; the SAME SQLite file as AP.

Environment (set by run_erp_sandbox.bat now, by the server's environment file later -- never in git):
  ERP_DB_PATH        the database file (default: the sandbox data/ap_disbursement_TEST.db)
  ERP_LIVE=1         required to open a file named ap_disbursement.db (the live database) -- this workspace never sets it
  ERP_DEBUG=1        developer error pages (laptop sandbox only)
  ERP_SECRET_KEY     the signing key (default: a random key made once into erp/.secret_key, not in git)
  ERP_ALLOWED_HOSTS  comma-separated extra host names (e.g. app.slickstiresupply.com on the server)"""
import os
import secrets
import sys
from pathlib import Path

ERP_DIR = Path(__file__).resolve().parent.parent          # C:\Slicks\Slicks_ERP\erp
REPO_ROOT = ERP_DIR.parent                                 # C:\Slicks\Slicks_ERP (engines/, schema.sql, data/)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from django.core.exceptions import ImproperlyConfigured  # noqa: E402

# ---- the database: the one door's rules (engines/db_engine.open_db) in Django's own words ------------------------------------
LIVE_DB_FILENAME = "ap_disbursement.db"
DB_PATH = os.environ.get("ERP_DB_PATH") or str(REPO_ROOT / "data" / "ap_disbursement_TEST.db")
if Path(DB_PATH).name.lower() == LIVE_DB_FILENAME and os.environ.get("ERP_LIVE") != "1":
    raise ImproperlyConfigured(f"Refusing to open the LIVE database ({DB_PATH}) without ERP_LIVE=1.")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DB_PATH,
        "OPTIONS": {
            "timeout": 10,                        # wait up to 10 s when someone else is saving (busy_timeout)
            "transaction_mode": "IMMEDIATE",      # every save takes the write lock first (F1.4 safe saves)
            "init_command": "PRAGMA foreign_keys = ON; PRAGMA busy_timeout = 10000; PRAGMA journal_mode = WAL;",
        },
    }
}
# The SQL functions manila_today() / manila_now() / acting_user() are added on every connection by core (core/db.py).

# ---- time: Manila. Stored times are UTC (like AP's CURRENT_TIMESTAMP); business dates come from engines/clock.py ------------
TIME_ZONE = "Asia/Manila"
USE_TZ = True
LANGUAGE_CODE = "en-us"
USE_I18N = False

# ---- security defaults -----------------------------------------------------------------------------------------------------
DEBUG = os.environ.get("ERP_DEBUG") == "1"


def _secret_key() -> str:
    key = os.environ.get("ERP_SECRET_KEY")
    if key:
        return key
    key_file = ERP_DIR / ".secret_key"                     # laptop only; gitignored
    if not key_file.exists():
        key_file.write_text(secrets.token_urlsafe(50), encoding="utf-8")
    return key_file.read_text(encoding="utf-8").strip()


SECRET_KEY = _secret_key()
ALLOWED_HOSTS = ["localhost", "127.0.0.1"] + [h.strip() for h in os.environ.get("ERP_ALLOWED_HOSTS", "").split(",") if h.strip()]

X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
if not DEBUG:                                              # on the server: HTTPS only
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")   # the reverse proxy (Caddy) terminates HTTPS

# ---- apps (no django.contrib.admin yet: no login pages until logins are designed in the core step) --------------------------
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",              # 1,234,567.89 in money columns
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",      # every page needs a sign-in unless marked otherwise
    "core.middleware.ActingUserMiddleware",                        # audit rows record the signed-in person
    "core.middleware.MustChangePasswordMiddleware",                # a temporary password must be replaced first
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# ---- users and sign-in (core step C1, owner's choices 2026-09-28) ------------------------------------------------------------
AUTH_USER_MODEL = "core.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGIN_MAX_FAILS = 5                      # 5 wrong tries in a row ...
LOGIN_LOCK_MINUTES = 15                  # ... = locked for 15 minutes
SESSION_COOKIE_AGE = 2 * 60 * 60         # signed out after 2 hours without using the ERP
SESSION_SAVE_EVERY_REQUEST = True        # (each page opened restarts the 2 hours)
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "static/"
STATIC_ROOT = ERP_DIR / "staticfiles"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.views.manila_header",
            ],
        },
    },
]
