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


def acting_user() -> str:
    """The email recorded on audit rows written now: the person set by acting_as(), else the owner (no logins yet)."""
    return _acting.get() or OWNER_EMAIL


@contextmanager
def acting_as(email: str | None):
    """Record everything saved inside this block as done by `email` (None = the owner)."""
    token = _acting.set((email or "").strip().lower() or None)
    try:
        yield
    finally:
        _acting.reset(token)
