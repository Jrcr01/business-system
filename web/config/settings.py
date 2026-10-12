"""Slick's system on Django (fresh project, stage D0, 2026-10-03; docs/DJANGO_REBUILD_PROPOSAL.md).

Django draws the screens; the business rules stay in engines/ and talk to the database themselves. Django keeps no tables of its own:
  * the people and their roles are the app_users list the Streamlit system already uses (engines/auth_service.py, engines/permissions.py);
  * the sign-in is kept in a signed cookie, so it survives a refresh and a dropped connection, and needs no table;
  * who did each save is set per request (core.middleware), so every audit row names the right person when many people work at once.

Environment (set by run_web_sandbox.bat now, by the server's environment file later; never in git):
  WEB_DB_PATH        the database file (default: data/ap_disbursement_WEB_TEST.db, the Django sandbox)
  WEB_LIVE=1         required to open a file named ap_disbursement.db (the live database); this workspace never sets it
  WEB_DEBUG=1        developer error pages (laptop only)
  WEB_SECRET_KEY     the signing key (default: a random key made once into web/.secret_key, not in git)
  WEB_ALLOWED_HOSTS  comma-separated host names of the server; each is also trusted for forms over HTTPS
  WEB_IDLE_MINUTES   signed out after this many minutes without opening a page (default 120)"""
import os
import secrets
import sys
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent          # C:\Slicks\Slicks_ERP\web
REPO_ROOT = WEB_DIR.parent                                 # C:\Slicks\Slicks_ERP (engines/, schema.sql, data/)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from django.core.exceptions import ImproperlyConfigured  # noqa: E402

# ---- the database: the engines' one door (engines/db_engine.open_db) is the only way in -----------------------------------------
LIVE_DB_FILENAME = "ap_disbursement.db"
DB_PATH = os.environ.get("WEB_DB_PATH") or str(REPO_ROOT / "data" / "ap_disbursement_WEB_TEST.db")
if Path(DB_PATH).name.lower() == LIVE_DB_FILENAME and os.environ.get("WEB_LIVE") != "1":
    raise ImproperlyConfigured(f"Refusing to open the LIVE database ({DB_PATH}) without WEB_LIVE=1.")
os.environ["AP_DB_PATH"] = DB_PATH                         # the engines read AP_DB_PATH when first imported; core.apps re-points any imported earlier
IS_SANDBOX = "TEST" in Path(DB_PATH).name.upper()          # the orange SANDBOX tag, as in Streamlit
# no password and no 6-digit code on the laptop sandbox (owner, 2026-10-08: "turn off the authentication so it's not a hassle"): Sign in shows each person as
# a button. Only with WEB_SANDBOX_NO_SIGNIN=1 (run_web_sandbox.bat) on a TEST database and never on the server (WEB_ALLOWED_HOSTS set) -- checked below.
SANDBOX_NO_SIGNIN = IS_SANDBOX and os.environ.get("WEB_SANDBOX_NO_SIGNIN") == "1"
DATABASES = {}                                             # Django keeps no tables: see above

TIME_ZONE = "Asia/Manila"
USE_TZ = True
LANGUAGE_CODE = "en-us"
USE_I18N = False

# ---- security ------------------------------------------------------------------------------------------------------------------
DEBUG = os.environ.get("WEB_DEBUG") == "1"


def _secret_key() -> str:
    key = os.environ.get("WEB_SECRET_KEY")
    if key:
        return key
    key_file = WEB_DIR / ".secret_key"                     # laptop only; gitignored
    if not key_file.exists():
        key_file.write_text(secrets.token_urlsafe(50), encoding="utf-8")
    return key_file.read_text(encoding="utf-8").strip()


SECRET_KEY = _secret_key()
_SERVER_HOSTS = [h.strip() for h in os.environ.get("WEB_ALLOWED_HOSTS", "").split(",") if h.strip()]
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"] + _SERVER_HOSTS
CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in _SERVER_HOSTS]
if _SERVER_HOSTS:
    SANDBOX_NO_SIGNIN = False                                # the server always asks for the password and the code

X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
if not DEBUG and _SERVER_HOSTS:                            # on the server: HTTPS only
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")                # Caddy in front ends HTTPS

# ---- sign-in: a signed cookie, so a refresh never signs anyone out ------------------------------------------------------------
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
# browsers share cookies across ports on one computer: each copy of the site (sandbox 8505, load test 8507 ...) gets cookie names of its own, from its database,
# so signing in or out of one never signs anyone out of another (found 2026-10-03)
_COOKIE_TAG = "".join(ch for ch in Path(DB_PATH).stem.lower() if ch.isalnum())
SESSION_COOKIE_NAME = f"slicks_{_COOKIE_TAG}_session"
CSRF_COOKIE_NAME = f"slicks_{_COOKIE_TAG}_csrf"
DEVICE_COOKIE_NAME = f"slicks_{_COOKIE_TAG}_dev"             # this browser's own number, and whose 6-digit code it gave today (core/views.py)
IDLE_MINUTES = int(os.environ.get("WEB_IDLE_MINUTES") or 120)
SESSION_COOKIE_AGE = IDLE_MINUTES * 60
SESSION_SAVE_EVERY_REQUEST = True                          # each page opened restarts the idle time
MESSAGE_STORAGE = "django.contrib.messages.storage.cookie.CookieStorage"

INSTALLED_APPS = [
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "core",
    "masters",                                             # D1: creditors, settings, users
    "buying",                                              # D2: price lists, purchase orders, receiving
    "payables",                                            # D3: order statements, invoices, credit memos, Add data, Payables due
    "phone",                                               # the phone pages (owner, 2026-10-12): price list, stock, stock in, stock out
    "selling",                                             # the revenue cycle, stage A (2026-10-11): stock, counts, affiliates, counter sale
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "core.cfaccess.AccessMiddleware",                      # zero trust: a valid Cloudflare Access pass on every request, when WEB_CF_TEAM / WEB_CF_AUD are set
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "core.middleware.SignInMiddleware",                    # who is signed in, their page rights, and who every save is recorded as
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
STATIC_URL = "static/"
STATIC_ROOT = Path(os.environ.get("WEB_STATIC_ROOT") or WEB_DIR / "staticfiles")
# the Streamlit screens (/more/) are retired (owner, 2026-10-09: the planner, their last page, dropped): nothing links there any more,
# whatever WEB_STREAMLIT_URL says on the server
STREAMLIT_URL = ""
# Cloudflare Access (owner, 2026-10-08): the team name (yourteam.cloudflareaccess.com) and this application's audience tag; empty = not checked (laptop, tests)
CF_ACCESS_TEAM = os.environ.get("WEB_CF_TEAM", "").strip()
CF_ACCESS_AUD = os.environ.get("WEB_CF_AUD", "").strip()
_CF_FILE = Path(DB_PATH).parent / "cf_access.txt"             # or two lines, team then audience, in this file beside the database (writable by the deploy user; the env file is root's)
if not (CF_ACCESS_TEAM and CF_ACCESS_AUD) and _CF_FILE.exists():
    _lines = [ln.strip() for ln in _CF_FILE.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
    if len(_lines) >= 2:
        CF_ACCESS_TEAM, CF_ACCESS_AUD = _lines[0], _lines[1]
STATICFILES_DIRS = [("assets", REPO_ROOT / "assets")]      # the Slick's logo, shared with Streamlit
WHITENOISE_USE_FINDERS = DEBUG or not _SERVER_HOSTS          # on the laptop the CSS and logo are served straight from the project (the server runs collectstatic)
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage" if DEBUG or not _SERVER_HOSTS
                    else "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.messages.context_processors.messages",
                "core.views.page_frame",
            ],
            "builtins": ["core.templatetags.slicks"],          # |dmy: dates shown mm/dd/yyyy everywhere (2026-10-09)
        },
    },
]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
}
