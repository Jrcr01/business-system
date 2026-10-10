"""Sign-in for the AP app (B8, phase 1, 2026-10-02). A Users list in the database (app_users): an email, a name, a role, and a password stored SCRAMBLED (scrypt with its
own random salt, never the password itself). Roles: 'owner' (Payables: everything) and 'purchasing' (the owner's brother: purchase orders and price lists only).

  * Nobody can be locked in or out by accident: the first owner sets their own password; a new person gets a temporary password that must be changed at first sign-in;
    five wrong tries lock that account for 15 minutes; the last active owner can never be switched off or demoted.
  * Phase 2 (at hosting, B9) adds Google 2-step sign-in on top of the SAME list (the email is the key in both): only how a person proves who they are changes.
  * Every change is in the audit log, with the person who made it."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from datetime import timedelta

import pandas as pd

from engines.permissions import require as require_permission
from engines.clock import manila_now
from engines.common import get_conn, write_tx

from engines.permissions import ROLES
MAX_TRIES = 5
LOCK_MINUTES = 15
_N, _R, _P = 2 ** 14, 8, 1
_TEMP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"       # no 0/O/1/l/I: it is read out or typed from paper
WRONG = "Wrong username or password."
WRONG_EMAIL = "Wrong email or password."


# --- passwords ----------------------------------------------------------------------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        scheme, n, r, p, salt, digest = (stored or "").split("$")
        if scheme != "scrypt" or not password:
            return False
        got = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def password_problem(password: str, email: str = "") -> str | None:
    """Why this password is not allowed, or None: at least 8 characters with a letter and a digit, and not the email."""
    if len(password or "") < 8:
        return "Use at least 8 characters."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Use both letters and digits."
    if email and password.lower() == email.lower():
        return "The password cannot be the email."
    return None


def _temp_password() -> str:
    while True:
        pw = "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(10))
        if password_problem(pw) is None:
            return pw


# --- users --------------------------------------------------------------------------------------------------------------------------------------

def _clean_email(email: str) -> str:
    return (email or "").strip().lower()


def _valid_email(email: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email))


# --- usernames (owner, 2026-10-08): people sign in with a username, not their email; the email stays on the account for records -------------------
USERNAME_RULE = "3 to 30 characters: lowercase letters, digits, dot, dash or underscore, starting with a letter or digit."


def clean_username(text) -> str:
    return (text or "").strip().lower()


def username_problem(username: str) -> str | None:
    return None if re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,29}", username or "") else f"The username must be {USERNAME_RULE}"


def _username_taken(conn, username: str, user_id: int | None = None) -> bool:
    return bool(conn.execute("SELECT 1 FROM app_users WHERE lower(username) = ? AND user_id IS NOT ?", (username, user_id)).fetchone())


def _username_from_email(conn, email: str) -> str:
    """For an account made without one: the part of the email before @, cleaned, made unique with a number when needed."""
    base = re.sub(r"[^a-z0-9._-]", "", email.split("@")[0].lower())[:26] or "user"
    if not base[0].isalnum():
        base = "u" + base
    base = base.ljust(3, "0")                                   # at least 3 characters
    name, n = base, 1
    while _username_taken(conn, name):
        n += 1
        name = f"{base}{n}"
    return name


def _pick_username(conn, username: str, email: str) -> tuple[str | None, str | None]:
    """(username, None) or (None, problem). Blank = made from the email."""
    username = clean_username(username)
    if not username:
        return _username_from_email(conn, email), None
    problem = username_problem(username)
    if problem:
        return None, problem
    if _username_taken(conn, username):
        return None, f"The username {username} is already used."
    return username, None


def fill_missing_usernames() -> list[tuple[str, str]]:
    """Every account without a username gets one from its email (run once by deploy/release.sh; a no-op after). Returns [(email, username)]."""
    done = []
    with write_tx() as conn:
        for uid, email in conn.execute("SELECT user_id, email FROM app_users WHERE username IS NULL OR username = '' ORDER BY user_id").fetchall():
            name = _username_from_email(conn, email)
            conn.execute("UPDATE app_users SET username = ? WHERE user_id = ?", (name, uid))
            _audit(conn, "USERNAME_SET", uid, f"{email} -> {name} (made from the email)", who="system")
            done.append((email, name))
    return done


def set_username(user_id: int, username: str) -> tuple[bool, str]:
    """The System admin changes a person's username (their next sign-in uses the new one)."""
    _ok, _msg = require_permission("users.manage")
    if not _ok:
        return False, _msg
    username = clean_username(username)
    problem = username_problem(username)
    if problem:
        return False, problem
    with write_tx() as conn:
        row = conn.execute("SELECT email, username FROM app_users WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return False, "Account not found."
        if _username_taken(conn, username, int(user_id)):
            return False, f"The username {username} is already used."
        conn.execute("UPDATE app_users SET username = ? WHERE user_id = ?", (username, user_id))
        _audit(conn, "USERNAME_CHANGED", user_id, f"{row[0]}: {row[1]} -> {username}")
    return True, f"The username is now {username}."


def set_email(user_id: int, email: str) -> tuple[bool, str]:
    """The System admin changes a person's email (owner, 2026-10-09). The email is how they sign in to the main system and must equal their Cloudflare
    Access email; their switches, password and history stay (they hang on the account, not the email). Past audit lines keep the old email."""
    import re
    _ok, _msg = require_permission("users.manage")
    if not _ok:
        return False, _msg
    email = (email or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return False, "Type a valid email address."
    with write_tx() as conn:
        row = conn.execute("SELECT email FROM app_users WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return False, "Account not found."
        if (row[0] or "").strip().lower() == email:
            return True, "The email is unchanged."
        if conn.execute("SELECT 1 FROM app_users WHERE lower(trim(email)) = ? AND user_id != ?", (email, user_id)).fetchone():
            return False, f"Another account already uses {email}."
        conn.execute("UPDATE app_users SET email = ? WHERE user_id = ?", (email, user_id))
        _audit(conn, "USER_EMAIL_CHANGED", user_id, f"{row[0]} -> {email}")
    return True, f"The email is now {email}. Add it to Cloudflare Access (erp application, Include: Emails) so they can get in."


def _audit(conn, action: str, user_id, text: str, who: str | None = None) -> None:
    if who is None:
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), ?, 'app_users', ?, ?)", (action, user_id, text))
    else:
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (?, ?, 'app_users', ?, ?)", (who, action, user_id, text))


def _public(row) -> dict:
    return {"user_id": row["user_id"], "email": row["email"], "username": row["username"], "name": row["name"], "role": row["role"], "is_active": row["is_active"],
            "must_change_password": row["must_change_password"], "device_epoch": int(row["device_epoch"] or 0) if "device_epoch" in row.keys() else 0}


def get_user(user_id: int) -> dict | None:
    with get_conn() as conn:
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute("SELECT * FROM app_users WHERE user_id = ?", (user_id,)).fetchone()
    return _public(row) if row else None


def needs_setup() -> bool:
    """True while nobody has an account yet: the first screen then asks the owner to set their password."""
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM app_users").fetchone()[0] == 0


def create_owner(email: str, name: str, password: str, username: str = "") -> tuple[bool, str, int | None]:
    """The very first account, made by the owner themselves with a password they choose (no temporary one). Refused once any account exists.
    username blank = made from the email."""
    email = _clean_email(email)
    if not _valid_email(email):
        return False, "Enter a valid email.", None
    if not (name or "").strip():
        return False, "Enter your name.", None
    problem = password_problem(password, email)
    if problem:
        return False, problem, None
    with write_tx() as conn:
        if conn.execute("SELECT COUNT(*) FROM app_users").fetchone()[0]:
            return False, "An account already exists. Sign in with it.", None
        username, problem = _pick_username(conn, username, email)
        if problem:
            return False, problem, None
        uid = conn.execute("INSERT INTO app_users (email, username, name, role, password_hash, must_change_password) VALUES (?, ?, ?, 'admin', ?, 0) RETURNING user_id",
                           (email, username, name.strip(), hash_password(password))).fetchone()[0]
        conn.execute("UPDATE app_users SET totp_setup_until = ? WHERE user_id = ?", (_setup_until(), uid))     # the first account sets up its own authenticator
        _audit(conn, "USER_CREATED", uid, f"{email} as System admin (first account)", who=email)
    return True, "Owner account created.", int(uid)


def create_user(email: str, name: str, role: str, username: str = "") -> tuple[bool, str, int | None, str | None]:
    """The owner adds a person. Returns (ok, message, user_id, temporary_password): the temporary password is shown once and must be changed at first sign-in.
    username: what they sign in with (blank = made from the email)."""
    _ok, _msg = require_permission('users.manage')
    if not _ok:
        raise PermissionError(_msg)
    email = _clean_email(email)
    if role not in ROLES:
        return False, "Pick a role.", None, None
    if not _valid_email(email):
        return False, "Enter a valid email.", None, None
    if not (name or "").strip():
        return False, "Enter the person's name.", None, None
    temp = _temp_password()
    with write_tx() as conn:
        if conn.execute("SELECT 1 FROM app_users WHERE email = ?", (email,)).fetchone():
            return False, f"{email} already has an account.", None, None
        username, problem = _pick_username(conn, username, email)
        if problem:
            return False, problem, None, None
        uid = conn.execute("INSERT INTO app_users (email, username, name, role, password_hash, must_change_password) VALUES (?, ?, ?, ?, ?, 1) RETURNING user_id",
                           (email, username, name.strip(), role, hash_password(temp))).fetchone()[0]
        from engines.whats_new import latest_id
        conn.execute("UPDATE app_users SET whats_new_seen = ?, totp_setup_until = ? WHERE user_id = ?", (latest_id(), _setup_until(), uid))
        # a new person starts with nothing "new", and with a day to set up their authenticator: adding them is the System admin's go-ahead
        _audit(conn, "USER_CREATED", uid, f"{email} (username {username}) as {role}")
    return True, f"{name.strip()} signs in as {username} with the temporary password and must choose their own.", int(uid), temp


MORE_BLOCKED = "Your account uses the main system only."


def authenticate(email: str, password: str, where: str = "more", by_email: bool = False) -> tuple[bool, dict | None, str]:
    """Checks a sign-in. Returns (ok, user, message). `email` is what was typed: the username (Django: only the username; the old screens: the username
    or the email). A wrong name and a wrong password give the same words. Five wrong tries lock the account for 15 minutes.
    where = "more" (the old Streamlit screens, the default they call with) refuses a person whose "Old screens (/more/)" switch is off (owner, 2026-10-08).
    by_email = True (Django, owner 2026-10-09): the account is found by its email only -- the email Cloudflare Access verified, or the one typed when
    Access is not on; the username no longer signs in to the main system.
    where = "web" (Django) is only the first step: the wrong-tries count is cleared by the 6-digit code (verify_totp), not by the password alone."""
    login, now = (email or "").strip().lower(), manila_now()
    with write_tx() as conn:
        conn.row_factory = __import__("sqlite3").Row
        if by_email:
            row = conn.execute("SELECT * FROM app_users WHERE lower(trim(email)) = ?", (login,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM app_users WHERE lower(username) = ?", (login,)).fetchone()     # the username (owner, 2026-10-08)
        if row is None and where != "web" and not by_email:                                                              # the old screens' sign-in still says Email
            row = conn.execute("SELECT * FROM app_users WHERE email = ?", (login,)).fetchone()
        email = row["email"] if row is not None else login
        wrong = WRONG_EMAIL if by_email else WRONG
        if row is None:
            return False, None, wrong
        if not row["is_active"]:
            return False, None, "This account is switched off. Ask the owner."
        locked = row["locked_until"]
        if locked and now < pd.to_datetime(locked).to_pydatetime():
            mins = int((pd.to_datetime(locked).to_pydatetime() - now).total_seconds() // 60) + 1
            return False, None, f"This account is locked for now. Try again in {mins} minute(s)."
        if not verify_password(password, row["password_hash"]):
            tries = int(row["failed_attempts"]) + 1
            if tries >= MAX_TRIES:
                until = (now + timedelta(minutes=LOCK_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
                conn.execute("UPDATE app_users SET failed_attempts = 0, locked_until = ? WHERE user_id = ?", (until, row["user_id"]))
                _audit(conn, "USER_LOCKED", row["user_id"], f"{email}: {MAX_TRIES} wrong tries, locked until {until}", who=email)
                return False, None, f"Too many wrong tries. This account is locked for {LOCK_MINUTES} minutes."
            conn.execute("UPDATE app_users SET failed_attempts = ? WHERE user_id = ?", (tries, row["user_id"]))
            return False, None, wrong
        if where == "web":
            return True, _public(row), ""
        from engines.permissions import denied_for
        if "more.use" in denied_for(email):
            return False, None, MORE_BLOCKED
        conn.execute("UPDATE app_users SET failed_attempts = 0, locked_until = NULL, last_login_at = ? WHERE user_id = ?", (now.strftime("%Y-%m-%d %H:%M:%S"), row["user_id"]))
        _audit(conn, "USER_SIGNED_IN", row["user_id"], email, who=email)
        return True, _public(row), ""


# --- the 6-digit sign-in code (owner, 2026-10-08): the standard authenticator code (RFC 6238: 30 seconds, 6 digits), as Google and Microsoft Authenticator
# show it. Asked at every sign-in to the main system, after the password. A code is accepted once only, and wrong codes count toward the same 5-tries lock.
TOTP_STEP = 30
TOTP_ISSUER = "Slick's"
SETUP_HOURS = 24            # the System admin's "Allow authenticator set-up" lasts a day (owner, 2026-10-11)
SETUP_NOT_ALLOWED = "Ask the System admin to allow your authenticator set-up."
STAMP = "%Y-%m-%d %H:%M:%S"


def _setup_until() -> str:
    return (manila_now() + timedelta(hours=SETUP_HOURS)).strftime(STAMP)


def shown_time(stamp: str | None) -> str:
    """A stored Manila time as people read it: 10/12/2026 5:30 AM."""
    if not stamp:
        return ""
    from datetime import datetime
    try:
        return datetime.strptime(str(stamp)[:19], STAMP).strftime("%m/%d/%Y %I:%M %p")
    except ValueError:
        return str(stamp)


def totp_setup_until(user_id: int) -> str | None:
    """Until when (Manila time) this person may set up an authenticator; None = not allowed now (never allowed, or the day has passed)."""
    with get_conn() as conn:
        row = conn.execute("SELECT totp_setup_until FROM app_users WHERE user_id = ?", (int(user_id),)).fetchone()
    until = row[0] if row else None
    return until if until and str(until) > manila_now().strftime(STAMP) else None


def allow_totp_setup(user_id: int) -> tuple[bool, str]:
    """The System admin lets this person set up an authenticator for the next 24 hours (owner, 2026-10-11: it is set up or replaced only when the
    admin allows it -- a stolen password and a hacked inbox are not enough to add one). A person who has one is reset first (a lost or new phone)."""
    _ok, _msg = require_permission("users.manage")
    if not _ok:
        raise PermissionError(_msg)
    with write_tx() as conn:
        row = conn.execute("SELECT email, totp_enabled_at FROM app_users WHERE user_id = ?", (int(user_id),)).fetchone()
        if row is None:
            return False, "No such person."
        if row[1]:
            return False, f"{row[0]} already has an authenticator. For a lost or new phone, press Reset code first, then allow the set-up."
        until = _setup_until()
        conn.execute("UPDATE app_users SET totp_setup_until = ? WHERE user_id = ?", (until, int(user_id)))
        _audit(conn, "USER_2FA_SETUP_ALLOWED", int(user_id), f"{row[0]} until {until}")
    return True, f"{row[0]} may set up an authenticator at their next sign-in, within {SETUP_HOURS} hours."


def _totp_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    mac = hmac.new(key, step.to_bytes(8, "big"), hashlib.sha1).digest()
    o = mac[-1] & 0x0F
    return f"{(int.from_bytes(mac[o:o + 4], 'big') & 0x7FFFFFFF) % 1_000_000:06d}"


def _now_step() -> int:
    return int(time.time()) // TOTP_STEP


def totp_setup_secret(user_id: int) -> str | None:
    """The key for the set-up screen: made once and kept until the person confirms it with a code. None once the code is set up."""
    with write_tx() as conn:
        row = conn.execute("SELECT totp_secret, totp_enabled_at FROM app_users WHERE user_id = ?", (int(user_id),)).fetchone()
        if row is None or row[1]:
            return None
        if row[0]:
            return row[0]
        secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
        conn.execute("UPDATE app_users SET totp_secret = ?, totp_last_step = NULL WHERE user_id = ?", (secret, int(user_id)))
        return secret


def totp_uri(email: str, secret: str) -> str:
    """What the QR code holds: the authenticator app reads the name, the account and the key from it."""
    from urllib.parse import quote
    return f"otpauth://totp/{quote(TOTP_ISSUER)}:{quote(email)}?secret={secret}&issuer={quote(TOTP_ISSUER)}&digits=6&period={TOTP_STEP}"


def verify_totp(user_id: int, code: str) -> tuple[bool, str]:
    """Checks the 6-digit code (this step, or one step either side for a slow phone clock). The first correct code also confirms the set-up.
    A code already used is refused. Wrong codes count toward the 5-tries / 15-minute lock."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    now = manila_now()
    with write_tx() as conn:
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute("SELECT * FROM app_users WHERE user_id = ?", (int(user_id),)).fetchone()
        if row is None or not row["is_active"]:
            return False, "This account is switched off. Ask the owner."
        locked = row["locked_until"]
        if locked and now < pd.to_datetime(locked).to_pydatetime():
            mins = int((pd.to_datetime(locked).to_pydatetime() - now).total_seconds() // 60) + 1
            return False, f"This account is locked for now. Try again in {mins} minute(s)."
        if not row["totp_secret"]:
            return False, "Set up your sign-in code first."
        setting_up = not row["totp_enabled_at"]
        if setting_up and not str(row["totp_setup_until"] or "") > now.strftime(STAMP):       # also refused here, not only on the screen
            return False, SETUP_NOT_ALLOWED
        last, here = row["totp_last_step"] or 0, _now_step()
        hit = next((s for s in (here - 1, here, here + 1) if s > last and len(code) == 6 and hmac.compare_digest(_totp_at(row["totp_secret"], s), code)), None)
        if hit is None:
            tries = int(row["failed_attempts"]) + 1
            if tries >= MAX_TRIES:
                until = (now + timedelta(minutes=LOCK_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
                conn.execute("UPDATE app_users SET failed_attempts = 0, locked_until = ? WHERE user_id = ?", (until, row["user_id"]))
                _audit(conn, "USER_LOCKED", row["user_id"], f"{row['email']}: {MAX_TRIES} wrong tries, locked until {until}", who=row["email"])
                return False, f"Too many wrong tries. This account is locked for {LOCK_MINUTES} minutes."
            conn.execute("UPDATE app_users SET failed_attempts = ? WHERE user_id = ?", (tries, row["user_id"]))
            return False, "That code didn't match. Try the newest one."
        stamp = now.strftime("%Y-%m-%d %H:%M:%S")
        if setting_up:
            conn.execute("UPDATE app_users SET totp_enabled_at = ?, totp_setup_until = NULL WHERE user_id = ?", (stamp, row["user_id"]))
            _audit(conn, "USER_2FA_SET_UP", row["user_id"], row["email"], who=row["email"])
            conn.execute("INSERT INTO review_flags (kind, ref_type, ref_id, detail, raised_by) VALUES ('AUTHENTICATOR_SET_UP', 'app_users', ?, ?, ?)",
                         (row["user_id"], f"{row['email']} set up an authenticator on {now.strftime('%m/%d/%Y %I:%M %p')}. Check with them that it was their own phone.",
                          row["email"]))
        conn.execute("UPDATE app_users SET totp_last_step = ?, failed_attempts = 0, locked_until = NULL, last_login_at = ? WHERE user_id = ?", (hit, stamp, row["user_id"]))
        _audit(conn, "USER_SIGNED_IN", row["user_id"], f"{row['email']} (password and code)", who=row["email"])
        return True, ""


def reset_totp(user_id: int, by_command: bool = False) -> tuple[bool, str]:
    """For a lost phone: the person sets up a new code at their next sign-in. The System admin only (or the server command, for the admin's own phone)."""
    if not by_command:
        _ok, _msg = require_permission("users.manage")
        if not _ok:
            raise PermissionError(_msg)
    with write_tx() as conn:
        row = conn.execute("SELECT email FROM app_users WHERE user_id = ?", (int(user_id),)).fetchone()
        if row is None:
            return False, "No such person."
        # the new set-up needs the System admin's go-ahead (allow_totp_setup). Given at once for the server command and for the admin's own account:
        # both are the admin acting, and without it the only System admin would be locked out
        from engines.acting_user import acting_email
        own = by_command or (acting_email() or "") == str(row[0]).strip().lower()
        # device_epoch + 1: the devices that gave the old code today are forgotten and the person's open sign-ins end
        conn.execute("UPDATE app_users SET totp_secret = NULL, totp_enabled_at = NULL, totp_last_step = NULL, totp_setup_until = ?, device_epoch = device_epoch + 1 "
                     "WHERE user_id = ?", (_setup_until() if own else None, int(user_id)))
        _audit(conn, "USER_2FA_RESET", int(user_id), row[0] + (" (server command)" if by_command else ""))
    if own:
        return True, f"The sign-in code of {row[0]} is reset. Set up the new one at the next sign-in, within {SETUP_HOURS} hours."
    return True, f"The sign-in code of {row[0]} is reset. Press \"Allow authenticator set-up\" when they are ready to set up the new one."


def note_password_sign_in(user_id: int) -> None:
    """A sign-in with the password alone, on a device that already gave this person's code today (once a day per device; owner, 2026-10-11)."""
    with write_tx() as conn:
        row = conn.execute("SELECT email FROM app_users WHERE user_id = ?", (int(user_id),)).fetchone()
        if row is None:
            return
        conn.execute("UPDATE app_users SET failed_attempts = 0, locked_until = NULL, last_login_at = ? WHERE user_id = ?", (manila_now().strftime(STAMP), int(user_id)))
        _audit(conn, "USER_SIGNED_IN", int(user_id), f"{row[0]} (password; this device gave the code earlier today)", who=row[0])


def sign_out_everywhere(user_id: int) -> tuple[bool, str]:
    """Ends every sign-in of this person and forgets every device that gave their code today: each device signs in again with the password and the code."""
    _ok, _msg = require_permission("users.manage")
    if not _ok:
        raise PermissionError(_msg)
    with write_tx() as conn:
        row = conn.execute("SELECT email FROM app_users WHERE user_id = ?", (int(user_id),)).fetchone()
        if row is None:
            return False, "No such person."
        conn.execute("UPDATE app_users SET device_epoch = device_epoch + 1 WHERE user_id = ?", (int(user_id),))
        _audit(conn, "USER_SIGNED_OUT_EVERYWHERE", int(user_id), row[0])
    return True, f"{row[0]} is signed out everywhere. Every device asks for the password and the code again."


SIGNED_IN = ("CODE", "REMEMBERED", "SET_UP")
SIGN_IN_RESULTS = {"CODE": "Signed in: password and code", "REMEMBERED": "Signed in: password (code given earlier that day on this device)",
                   "SET_UP": "Authenticator set up, signed in", "PASSWORD_REFUSED": "Not signed in: wrong password, locked or switched off",
                   "WRONG_CODE": "Not signed in: wrong code", "SETUP_REFUSED": "Not signed in: authenticator set-up not allowed"}


def log_sign_in(user_id: int, result: str, device_id: str = "", browser: str = "", ip: str = "", country: str = "", city: str = "") -> bool:
    """One line of the sign-in record (owner, 2026-10-11: "are they really the one accessing the system"). Returns True when the person signed in on a
    device they had not used before; that also goes on the Review list."""
    now = manila_now()
    with write_tx() as conn:
        row = conn.execute("SELECT email FROM app_users WHERE user_id = ?", (int(user_id),)).fetchone()
        if row is None:
            return False
        marks = ",".join("?" * len(SIGNED_IN))
        new = bool(result in SIGNED_IN and device_id and not conn.execute(
            f"SELECT 1 FROM sign_in_log WHERE user_id = ? AND device_id = ? AND result IN ({marks})", (int(user_id), device_id, *SIGNED_IN)).fetchone())
        conn.execute("INSERT INTO sign_in_log (user_id, email, result, device_id, is_new_device, browser, ip, country, city, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (int(user_id), row[0], result, device_id or None, int(new), browser or None, ip or None, country or None, city or None, now.strftime(STAMP)))
        if new:
            place = ", ".join(x for x in (city, country) if x)
            what = " ".join(x for x in (browser, f"in {place}" if place else "") if x) or "an unknown browser"
            conn.execute("INSERT INTO review_flags (kind, ref_type, ref_id, detail, raised_by) VALUES ('NEW_DEVICE_SIGN_IN', 'app_users', ?, ?, ?)",
                         (int(user_id), f"{row[0]} signed in on a device not used before: {what}, {now.strftime('%m/%d/%Y %I:%M %p')}.", row[0]))
    return new


def recent_sign_ins(user_id: int, limit: int = 20) -> list[dict]:
    """The person's last sign-ins, newest first, for their page in Settings > Users."""
    with get_conn() as conn:
        rows = conn.execute("SELECT at, result, device_id, is_new_device, browser, ip, country, city FROM sign_in_log WHERE user_id = ? ORDER BY log_id DESC LIMIT ?",
                            (int(user_id), int(limit))).fetchall()
    return [{"at": r[0], "when": shown_time(r[0]), "result": r[1], "label": SIGN_IN_RESULTS.get(r[1], r[1]), "ok": r[1] in SIGNED_IN, "device": (r[2] or "")[:6], "new": bool(r[3]),
             "browser": r[4] or "", "ip": r[5] or "", "place": ", ".join(x for x in (r[7], r[6]) if x)} for r in rows]


def totp_enabled_at(user_id: int) -> str | None:
    """When the person set up their sign-in code; None = not set up (they are asked to at their next sign-in)."""
    with get_conn() as conn:
        row = conn.execute("SELECT totp_enabled_at FROM app_users WHERE user_id = ?", (int(user_id),)).fetchone()
    return row[0] if row else None


KEEP_PASSWORDS = 5          # a new password cannot be one of the person's last 5 (owner, 2026-10-08)


def _remember(conn, user_id: int, password_hash: str) -> None:
    """Keeps the stored (scrambled) password in the person's history, newest 5 only."""
    conn.execute("INSERT INTO password_history (user_id, password_hash) VALUES (?, ?)", (user_id, password_hash))
    conn.execute("DELETE FROM password_history WHERE user_id = ? AND history_id NOT IN (SELECT history_id FROM password_history WHERE user_id = ? ORDER BY history_id DESC LIMIT ?)",
                 (user_id, user_id, KEEP_PASSWORDS))


def _was_used(conn, user_id: int, password: str) -> bool:
    return any(verify_password(password, r[0]) for r in conn.execute("SELECT password_hash FROM password_history WHERE user_id = ? ORDER BY history_id DESC LIMIT ?", (user_id, KEEP_PASSWORDS)))


def get_restrictions(user_id: int) -> set[str]:
    """The actions the System admin switched off for this person."""
    with get_conn() as conn:
        return {r[0] for r in conn.execute("SELECT action FROM user_restrictions WHERE user_id = ?", (user_id,))}


def split_new_switches() -> int:
    """Once each (2026-10-09): a new switch starts as the switch it was split from -- "Mark payables paid" as Bank, "Card statements" as Loans, and
    later "Bank loans" as Lenders -- so nobody gains or loses anything until the System admin changes it. Marked done in the settings; returns how many
    switches were set."""
    from engines.settings_store import get_setting, set_setting
    n = 0
    for flag, pairs in (("switches_split_2026_10_09", (("bank.use", "payables.mark_paid"), ("loans.use", "cards.use"))),
                        ("switches_split_bankloans_2026_10_09", (("loans.use", "bankloans.use"),)),
                        ("switches_split_booklets_2026_10_11", (("bank.use", "booklets.use"),))):         # Check booklets starts as Bank and cash
        if str(get_setting(flag, "0")) == "1":
            continue
        n += _split(pairs)
        set_setting(flag, "1")
    if str(get_setting("switches_receive_one_2026_10_11", "0")) != "1":      # "receive goods" and "record the SI or DR" became one switch (owner, 2026-10-11)
        with get_conn() as conn:
            got = {}
            for uid, action in conn.execute("SELECT user_id, action FROM user_restrictions WHERE action IN ('po.receive_goods', 'po.receive')"):
                got.setdefault(uid, set()).add(action)
            for uid, denied in got.items():
                if len(denied) == 1:                             # one was on: both are on now (the person could receive, or could record the document)
                    conn.execute("DELETE FROM user_restrictions WHERE user_id = ? AND action IN ('po.receive_goods', 'po.receive')", (uid,))
                    conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'USER_ACCESS_CHANGED', 'app_users', ?, ?)",
                                 (uid, "Receiving is one switch now (it always records the payable): this person had one of the two, so it is on."))
            conn.commit()
        set_setting("switches_receive_one_2026_10_11", "1")
    return n


def _split(pairs) -> int:
    n = 0
    with get_conn() as conn:
        for old, new in pairs:
            for (uid,) in conn.execute("SELECT user_id FROM user_restrictions WHERE action = ?", (old,)).fetchall():
                if not conn.execute("SELECT 1 FROM user_restrictions WHERE user_id = ? AND action = ?", (uid, new)).fetchone():
                    conn.execute("INSERT INTO user_restrictions (user_id, action, created_by) VALUES (?, ?, 'split 2026-10-09')", (uid, new))
                    n += 1
    return n


def get_hidden_pages(user_id: int) -> set[str]:
    """The pages the System admin hid from this person (what they see, 2026-10-09)."""
    try:
        with get_conn() as conn:
            return {r[0] for r in conn.execute("SELECT page_id FROM user_hidden_pages WHERE user_id = ?", (user_id,))}
    except Exception:                                   # an older database without the table
        return set()


def set_access(user_id: int, hidden: set[str], denied: set[str]) -> tuple[bool, str]:
    """The System admin saves what this person SEES (hidden pages) and DOES (switched-off actions) in one locked step (owner, 2026-10-09). Never for a
    System admin. Audited in plain words: what is hidden, view only and switched off, old and new."""
    from engines.permissions import ACCESS_PAGES, SWITCHES
    _ok, _msg = require_permission('users.manage')
    if not _ok:
        return False, _msg
    hidden = {p for p in hidden if p in ACCESS_PAGES}
    denied = {a for a in denied if a in SWITCHES}
    with write_tx() as conn:
        row = conn.execute("SELECT email, role FROM app_users WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return False, "Account not found."
        if row[1] == "admin":
            return False, "The System admin can't be limited."
        h_before = {r[0] for r in conn.execute("SELECT page_id FROM user_hidden_pages WHERE user_id = ?", (user_id,))}
        d_before = {r[0] for r in conn.execute("SELECT action FROM user_restrictions WHERE user_id = ?", (user_id,))}
        if h_before == hidden and d_before == denied:
            return True, "No change."
        conn.execute("DELETE FROM user_hidden_pages WHERE user_id = ?", (user_id,))
        for p in sorted(hidden):
            conn.execute("INSERT INTO user_hidden_pages (user_id, page_id, created_by) VALUES (?, ?, acting_user())", (user_id, p))
        conn.execute("DELETE FROM user_restrictions WHERE user_id = ?", (user_id,))
        for a in sorted(denied):
            conn.execute("INSERT INTO user_restrictions (user_id, action, created_by) VALUES (?, ?, acting_user())", (user_id, a))
        _audit(conn, "USER_ACCESS_CHANGED", user_id,
               f"{row[0]}: hidden now {sorted(hidden) or 'nothing'} (was {sorted(h_before) or 'nothing'}); switched off now {sorted(denied) or 'nothing'}; "
               f"newly off {sorted(denied - d_before) or 'none'}; back on {sorted(d_before - denied) or 'none'}")
    return True, f"{row[0]}: saved. It applies at their next click."


def set_restrictions(user_id: int, denied: set[str]) -> tuple[bool, str]:
    """The System admin saves what this person may NOT do (everything else they may). Never for a System admin. Audited with what changed."""
    from engines.permissions import SWITCHES
    _ok, _msg = require_permission('users.manage')
    if not _ok:
        return False, _msg
    denied = {a for a in denied if a in SWITCHES}
    with write_tx() as conn:
        row = conn.execute("SELECT email, role FROM app_users WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return False, "Account not found."
        if row[1] == "admin":
            return False, "The System admin can't be limited."
        before = {r[0] for r in conn.execute("SELECT action FROM user_restrictions WHERE user_id = ?", (user_id,))}
        if before == denied:
            return True, "No change."
        conn.execute("DELETE FROM user_restrictions WHERE user_id = ?", (user_id,))
        for a in sorted(denied):
            conn.execute("INSERT INTO user_restrictions (user_id, action, created_by) VALUES (?, ?, acting_user())", (user_id, a))
        _audit(conn, "USER_LIMITS_CHANGED", user_id, f"{row[0]}: switched off now {sorted(denied) or 'nothing'}; newly off {sorted(denied - before) or 'none'}; back on {sorted(before - denied) or 'none'}")
    return True, f"{row[0]}: saved. It applies at their next click."


def change_password(user_id: int, old_password: str, new_password: str) -> tuple[bool, str]:
    with write_tx() as conn:
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute("SELECT * FROM app_users WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            return False, "Account not found."
        if not verify_password(old_password, row["password_hash"]):
            return False, "The current password is not right."
        problem = password_problem(new_password, row["email"])
        if problem:
            return False, problem
        if new_password == old_password or _was_used(conn, user_id, new_password):
            return False, f"Choose a password you have not used before (not any of your last {KEEP_PASSWORDS})."
        new_hash = hash_password(new_password)
        _remember(conn, user_id, row["password_hash"])                         # the one being replaced goes into the history
        conn.execute("UPDATE app_users SET password_hash = ?, must_change_password = 0 WHERE user_id = ?", (new_hash, user_id))
        _audit(conn, "PASSWORD_CHANGED", user_id, row["email"], who=row["email"])
    return True, "Password changed."


def _other_active_owners(conn, user_id: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM app_users WHERE role = 'admin' AND is_active = 1 AND user_id != ?", (user_id,)).fetchone()[0]


def reset_password(user_id: int) -> tuple[bool, str, str | None]:
    """The owner issues a new temporary password (the person must change it) and clears any lock."""
    _ok, _msg = require_permission('users.manage')
    if not _ok:
        raise PermissionError(_msg)
    temp = _temp_password()
    with write_tx() as conn:
        row = conn.execute("SELECT email FROM app_users WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return False, "Account not found.", None
        conn.execute("UPDATE app_users SET password_hash = ?, must_change_password = 1, failed_attempts = 0, locked_until = NULL WHERE user_id = ?", (hash_password(temp), user_id))
        _audit(conn, "PASSWORD_RESET", user_id, row[0])
    return True, f"New temporary password for {row[0]}.", temp


def set_active(user_id: int, active: bool) -> tuple[bool, str]:
    _ok, _msg = require_permission('users.manage')
    if not _ok:
        return False, _msg
    with write_tx() as conn:
        row = conn.execute("SELECT email, role, is_active FROM app_users WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return False, "Account not found."
        if not active and row[1] == "admin" and row[2] and _other_active_owners(conn, user_id) == 0:
            return False, "This is the last active System admin: switching it off would lock everyone out."
        conn.execute("UPDATE app_users SET is_active = ? WHERE user_id = ?", (1 if active else 0, user_id))
        _audit(conn, "USER_SWITCHED_ON" if active else "USER_SWITCHED_OFF", user_id, row[0])
    return True, f"{row[0]} switched {'on' if active else 'off'}."


def set_role(user_id: int, role: str) -> tuple[bool, str]:
    _ok, _msg = require_permission('users.manage')
    if not _ok:
        return False, _msg
    if role not in ROLES:
        return False, "Pick a role."
    with write_tx() as conn:
        row = conn.execute("SELECT email, role, is_active FROM app_users WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return False, "Account not found."
        if row[1] == "admin" and role != "admin" and row[2] and _other_active_owners(conn, user_id) == 0:
            return False, "This is the last active System admin: changing its role would lock everyone out."
        conn.execute("UPDATE app_users SET role = ? WHERE user_id = ?", (role, user_id))
        _audit(conn, "USER_ROLE_CHANGED", user_id, f"{row[0]}: {row[1]} -> {role}")
    return True, f"{row[0]} is now {role}."


def list_users() -> pd.DataFrame:
    """Everyone, without the password hashes: user_id, email, name, role, is_active, must_change_password, last_login_at, locked_until."""
    with get_conn() as conn:
        return pd.read_sql_query("SELECT user_id, username, email, name, role, is_active, must_change_password, last_login_at, locked_until FROM app_users ORDER BY CASE role WHEN 'admin' THEN 0 WHEN 'owner' THEN 1 ELSE 2 END, name", conn)
