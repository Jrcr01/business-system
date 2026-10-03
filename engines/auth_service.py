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
WRONG = "Wrong email or password."


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


def _audit(conn, action: str, user_id, text: str, who: str | None = None) -> None:
    if who is None:
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), ?, 'app_users', ?, ?)", (action, user_id, text))
    else:
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (?, ?, 'app_users', ?, ?)", (who, action, user_id, text))


def _public(row) -> dict:
    return {"user_id": row["user_id"], "email": row["email"], "name": row["name"], "role": row["role"], "is_active": row["is_active"],
            "must_change_password": row["must_change_password"]}


def get_user(user_id: int) -> dict | None:
    with get_conn() as conn:
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute("SELECT * FROM app_users WHERE user_id = ?", (user_id,)).fetchone()
    return _public(row) if row else None


def needs_setup() -> bool:
    """True while nobody has an account yet: the first screen then asks the owner to set their password."""
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM app_users").fetchone()[0] == 0


def create_owner(email: str, name: str, password: str) -> tuple[bool, str, int | None]:
    """The very first account, made by the owner themselves with a password they choose (no temporary one). Refused once any account exists."""
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
        uid = conn.execute("INSERT INTO app_users (email, name, role, password_hash, must_change_password) VALUES (?, ?, 'admin', ?, 0) RETURNING user_id",
                           (email, name.strip(), hash_password(password))).fetchone()[0]
        _audit(conn, "USER_CREATED", uid, f"{email} as System admin (first account)", who=email)
    return True, "Owner account created.", int(uid)


def create_user(email: str, name: str, role: str) -> tuple[bool, str, int | None, str | None]:
    """The owner adds a person. Returns (ok, message, user_id, temporary_password): the temporary password is shown once and must be changed at first sign-in."""
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
        uid = conn.execute("INSERT INTO app_users (email, name, role, password_hash, must_change_password) VALUES (?, ?, ?, ?, 1) RETURNING user_id",
                           (email, name.strip(), role, hash_password(temp))).fetchone()[0]
        _audit(conn, "USER_CREATED", uid, f"{email} as {role}")
    return True, f"{name.strip()} can sign in with the temporary password and must choose their own.", int(uid), temp


def authenticate(email: str, password: str) -> tuple[bool, dict | None, str]:
    """Checks a sign-in. Returns (ok, user, message). Wrong email and wrong password give the same words. Five wrong tries lock the account for 15 minutes."""
    email, now = _clean_email(email), manila_now()
    with write_tx() as conn:
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute("SELECT * FROM app_users WHERE email = ?", (email,)).fetchone()
        if row is None:
            return False, None, WRONG
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
            return False, None, WRONG
        conn.execute("UPDATE app_users SET failed_attempts = 0, locked_until = NULL, last_login_at = ? WHERE user_id = ?", (now.strftime("%Y-%m-%d %H:%M:%S"), row["user_id"]))
        _audit(conn, "USER_SIGNED_IN", row["user_id"], email, who=email)
        return True, _public(row), ""


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
        if new_password == old_password:
            return False, "Choose a different password from the current one."
        conn.execute("UPDATE app_users SET password_hash = ?, must_change_password = 0 WHERE user_id = ?", (hash_password(new_password), user_id))
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
        return pd.read_sql_query("SELECT user_id, email, name, role, is_active, must_change_password, last_login_at, locked_until FROM app_users ORDER BY CASE role WHEN 'admin' THEN 0 WHEN 'owner' THEN 1 ELSE 2 END, name", conn)
