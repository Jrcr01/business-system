"""One-time codes for the phone forms (owner, 2026-10-17: "what problem occurs if there is loss of connectivity"; mock-up ah_phone_signal_loss).

A phone form carries a code made on the phone (phone.js). The first send with that code claims it; if the save works the code is marked done with
what was saved, if it fails the claim is dropped so the person can press again. A second send with a code already done is answered "already saved"
and nothing is recorded twice -- the case where the server saved it but the answer never reached the phone. Codes are kept 30 days."""
from __future__ import annotations

import re

from engines.clock import manila_now
from engines.common import write_tx

STAMP = "%Y-%m-%d %H:%M:%S"
_OK = re.compile(r"^[A-Za-z0-9_-]{12,64}$")


def claim(token: str, email: str) -> tuple[str, dict | None]:
    """'new' (go ahead), 'done' (already saved: the row), 'busy' (the first send is still being saved), or 'none' (no code: as before)."""
    if not token or not _OK.match(token):
        return "none", None
    now = manila_now()
    with write_tx() as conn:
        conn.execute("DELETE FROM form_once WHERE claimed_at < datetime(?, '-30 days')", (now.strftime(STAMP),))
        row = conn.execute("SELECT done_at, note, url FROM form_once WHERE token = ?", (token,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO form_once (token, user_email, claimed_at) VALUES (?, ?, ?)", (token, (email or "").lower(), now.strftime(STAMP)))
            return "new", None
    if row[0]:
        return "done", {"at": str(row[0]), "note": row[1] or "", "url": row[2] or ""}
    return "busy", None


def finish(token: str, ok: bool, note: str = "", url: str = "") -> None:
    """After the save: remembered when it worked; forgotten when it did not (so pressing again tries again)."""
    if not token or not _OK.match(token):
        return
    with write_tx() as conn:
        if ok:
            conn.execute("UPDATE form_once SET done_at = ?, note = ?, url = ? WHERE token = ?", (manila_now().strftime(STAMP), (note or "")[:300], url[:300], token))
        else:
            conn.execute("DELETE FROM form_once WHERE token = ? AND done_at IS NULL", (token,))
