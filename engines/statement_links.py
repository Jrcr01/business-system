"""The supplier's link to one payment statement (owner, 2026-10-14: "It is possible to have a qr code then if scanned it shows the a4?" ... "supplier").
A QR code on the thermal strip and on the supplier's copy opens that one statement's supplier copy with no sign-in: the address carries a long random
code nobody can guess. It shows that statement only -- never the filing copy, never anything else. Every opening is recorded; a link lives until
some days after the statement's last check date; the System admin can switch one link, or all of them, off."""
from __future__ import annotations

import secrets
from datetime import date, timedelta

from engines.clock import manila_now, manila_today
from engines.common import get_conn, write_tx

STAMP = "%Y-%m-%d %H:%M:%S"
PATH = "/s/"
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"


def on() -> bool:
    from engines.settings_registry import int_value
    return int_value("supplier_qr") == 1


def days() -> int:
    from engines.settings_registry import int_value
    return int_value("supplier_qr_days")


def _new_token() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(32))


def token_for(statement_id: int) -> str | None:
    """The statement's code, made the first time it is printed. None while the links are off, for a draft (no number yet), or a link switched off."""
    if not on():
        return None
    with write_tx() as conn:
        st = conn.execute("SELECT statement_ref FROM order_statements WHERE statement_id = ?", (int(statement_id),)).fetchone()
        if st is None or str(st[0]).startswith("DRAFT-"):
            return None
        row = conn.execute("SELECT token, revoked_at FROM statement_links WHERE statement_id = ?", (int(statement_id),)).fetchone()
        if row:
            return None if row[1] else row[0]
        token = _new_token()
        conn.execute("INSERT INTO statement_links (statement_id, token, created_at, created_by) VALUES (?, ?, ?, acting_user())",
                     (int(statement_id), token, manila_now().strftime(STAMP)))
    return token


def url_for(statement_id: int, base: str) -> str | None:
    token = token_for(statement_id) if base else None
    return f"{base.rstrip('/')}{PATH}{token}/" if token else None


def _expires(conn, statement_id: int, created_at: str) -> date:
    last = conn.execute("SELECT MAX(check_date) FROM checks_issued WHERE statement_id = ? AND status = 'Active'", (int(statement_id),)).fetchone()[0]
    start = date.fromisoformat(str(last)[:10]) if last else date.fromisoformat(str(created_at)[:10])
    return start + timedelta(days=days())


def resolve(token: str) -> tuple[int | None, str]:
    """(statement id, '') for a link that opens; (None, why) otherwise: 'off', 'none', 'revoked', 'expired'."""
    if not on():
        return None, "off"
    token = str(token or "")
    if len(token) != 32 or any(ch not in _ALPHABET for ch in token):
        return None, "none"
    with get_conn() as conn:
        row = conn.execute("SELECT statement_id, revoked_at, created_at FROM statement_links WHERE token = ?", (token,)).fetchone()
        if row is None:
            return None, "none"
        if row[1]:
            return None, "revoked"
        if manila_today() > _expires(conn, int(row[0]), row[2]):
            return None, "expired"
    return int(row[0]), ""


def log_open(statement_id: int, ip: str = "", country: str = "", browser: str = "") -> None:
    with write_tx() as conn:
        conn.execute("INSERT INTO statement_link_opens (statement_id, opened_at, ip, country, browser) VALUES (?, ?, ?, ?, ?)",
                     (int(statement_id), manila_now().strftime(STAMP), ip[:45] or None, country[:2] or None, browser[:80] or None))


def state(statement_id: int) -> dict | None:
    """For the statement's page: is there a link, until when, how often it was opened. None when none was made yet."""
    from engines.auth_service import shown_time
    with get_conn() as conn:
        row = conn.execute("SELECT token, revoked_at, created_at FROM statement_links WHERE statement_id = ?", (int(statement_id),)).fetchone()
        if row is None:
            return None
        n, last = conn.execute("SELECT COUNT(*), MAX(opened_at) FROM statement_link_opens WHERE statement_id = ?", (int(statement_id),)).fetchone()
        opens = conn.execute("SELECT opened_at, ip, country, browser FROM statement_link_opens WHERE statement_id = ? ORDER BY open_id DESC LIMIT 10", (int(statement_id),)).fetchall()
        until = _expires(conn, int(statement_id), row[2])
    return {"off": bool(row[1]), "until": until, "expired": manila_today() > until, "opens": int(n), "last": shown_time(last) if last else "",
            "recent": [{"when": shown_time(a), "ip": b or "", "country": c or "", "browser": d or ""} for a, b, c, d in opens]}


def set_off(statement_id: int, off: bool) -> tuple[bool, str]:
    """Switch one statement's link off (a strip that went to the wrong hands) or back on."""
    from engines.permissions import require
    ok, why = require("settings.edit")
    if not ok:
        return False, why
    with write_tx() as conn:
        row = conn.execute("SELECT 1 FROM statement_links WHERE statement_id = ?", (int(statement_id),)).fetchone()
        if row is None:
            return False, "This payment statement has no link yet: it is made the first time the statement is printed."
        conn.execute("UPDATE statement_links SET revoked_at = ?, revoked_by = ? WHERE statement_id = ?",
                     (manila_now().strftime(STAMP) if off else None, None, int(statement_id)))
        if off:
            conn.execute("UPDATE statement_links SET revoked_by = acting_user() WHERE statement_id = ?", (int(statement_id),))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), ?, 'order_statements', ?, ?)",
                     ("SUPPLIER_LINK_OFF" if off else "SUPPLIER_LINK_ON", int(statement_id), "the supplier's QR link"))
    return True, "The supplier's link is switched off: the QR code on the printed copies opens nothing now." if off else "The supplier's link works again."


# --- guessing: a code is 32 random characters, so guessing is hopeless; still, an address that keeps trying wrong codes is refused for a while.
_MISSES: dict[str, list[float]] = {}
MISS_LIMIT, MISS_WINDOW = 20, 600


def too_many_misses(ip: str, now: float) -> bool:
    recent = [t for t in _MISSES.get(ip, []) if now - t < MISS_WINDOW]
    _MISSES[ip] = recent
    return len(recent) >= MISS_LIMIT


def note_miss(ip: str, now: float) -> None:
    _MISSES.setdefault(ip, []).append(now)
    if len(_MISSES) > 5000:
        _MISSES.clear()
