"""Who is acting (Phase F1.5, 2026-09-28): every audit_logs row records the email of the person who did it (audit_logs.user_email).

  * Python:  acting_user() -> the email of whoever is acting right now; `with acting_as(email):` sets it for a block of work.
  * SQL:     acting_user() is a SQL function registered on every connection by the one door (db_engine.open_db), so every
             INSERT INTO audit_logs writes `user_email` = acting_user() -- the save functions never pass it around by hand.
  * Until logins exist (Phase F2) nobody sets it, so it is the owner (owner, 2026-09-28: "my Google email", the same email that
    becomes the owner's login). F2 sets the logged-in person's email per page run with acting_as(); nothing else changes.
The value is per thread / per task (a ContextVar), so two people using the app at once never get each other's name.
Needs no other module (imported by db_engine, the lowest layer)."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

OWNER_EMAIL = "v.jrcroxas@gmail.com"      # the owner; also written on the audit rows made before F1.5 (apply_pending_migrations)

_acting: ContextVar[str | None] = ContextVar("acting_user", default=None)
_role: ContextVar[str | None] = ContextVar("acting_role", default=None)


_FALLBACK = None          # a function () -> {'email', 'role'} | None, registered by a screen layer that keeps the signed-in person elsewhere


def register_fallback(fn) -> None:
    """The Streamlit app registers how to find the person signed in to the browser tab: its button callbacks and pop-ups run before the page's
    sign-in step, so the per-run value below is not set yet. Django never needs it (its middleware sets the value for every request).
    Kept here as a hook so engines/ itself never imports a screen framework."""
    global _FALLBACK
    _FALLBACK = fn


def _signed_in() -> dict | None:
    if _FALLBACK is None:
        return None
    try:
        return _FALLBACK()
    except Exception:
        return None


def acting_user() -> str:
    """The email recorded on audit rows written now: the person set by acting_as(), else the signed-in person of this tab, else the owner (scripts, tests)."""
    return _acting.get() or ((_signed_in() or {}).get("email") or "").strip().lower() or _fallback()


def _fallback() -> str:
    """Nobody signed in and nobody acting (a script, a scheduled job): the fallback actor from Settings > Company, else the owner's email."""
    try:
        from engines.company import profile
        return (profile()["job_actor"] or "").strip().lower() or OWNER_EMAIL
    except Exception:                                   # noqa: BLE001 -- before the settings table exists
        return OWNER_EMAIL


def acting_email() -> str | None:
    """The signed-in person's email, or None when nobody is signed in (a script or a test)."""
    return _acting.get() or ((_signed_in() or {}).get("email") or "").strip().lower() or None


def acting_role() -> str | None:
    """The signed-in person's role (engines/permissions.py), or None when nobody is signed in: that acts as the owner."""
    return _role.get() or (_signed_in() or {}).get("role") or None


def set_acting(email: str | None, role: str | None = None) -> None:
    """Record everything saved for the rest of this page run as done by `email`, with `role` for the permission guards (the sign-in sets both at the top of every run)."""
    _acting.set((email or "").strip().lower() or None)
    _role.set(role or None)


@contextmanager
def acting_as(email: str | None, role: str | None = None):
    """Record everything saved inside this block as done by `email` (None = the owner), with `role` for the permission guards (None = the owner's)."""
    token, rtoken = _acting.set((email or "").strip().lower() or None), _role.set(role or None)
    try:
        yield
    finally:
        _acting.reset(token)
        _role.reset(rtoken)
