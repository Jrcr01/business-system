"""Sending email (owner, 2026-10-14: "is email notification possible every morning?"). The server cannot send mail itself (its host blocks the mail
ports), so a mail service is called over the web: Resend or Brevo, each with a free tier far above a few emails a morning. The service's key is typed
by the System admin on Settings > Notifications and kept in a file beside the database (mail_out.json), never in the code and never shown again."""
from __future__ import annotations

import json
import os
from pathlib import Path

PROVIDERS = {"resend": "Resend", "brevo": "Brevo"}
AGENT = "slicks-erp/1.0"


def _path() -> Path:
    import engines.db_engine as d
    return Path(d.DB_PATH).parent / "mail_out.json"


def _read() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def config() -> dict:
    """What the Settings page may show: the service, the From address, and only the last four characters of the key."""
    c = _read()
    key = str(c.get("key") or "")
    return {"provider": c.get("provider") or "resend", "sender": c.get("sender") or "", "name": c.get("name") or "Slick's ERP",
            "has_key": bool(key), "key_end": key[-4:] if len(key) >= 8 else "", "ready": ready()}


def ready() -> bool:
    c = _read()
    return bool(c.get("key") and c.get("sender") and c.get("provider") in PROVIDERS)


def save_config(provider: str, sender: str, key: str = "") -> tuple[bool, str]:
    """The System admin's set-up. A blank key keeps the one already saved."""
    from engines.permissions import require
    ok, why = require("settings.edit")
    if not ok:
        return False, why
    provider, sender, key = (provider or "").strip().lower(), (sender or "").strip(), (key or "").strip()
    if provider not in PROVIDERS:
        return False, "Pick the mail service."
    if "@" not in sender or " " in sender:
        return False, "Type the From address, e.g. reports@slickstiresupply.com."
    old = _read()
    key = key or str(old.get("key") or "")
    if not key:
        return False, "Paste the service's key."
    path = _path()
    path.write_text(json.dumps({"provider": provider, "sender": sender, "name": "Slick's ERP", "key": key}), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    from engines.common import write_tx
    with write_tx() as conn:
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'MAIL_SETUP_SAVED', 'settings', 0, ?)",
                     (f"{PROVIDERS[provider]}, from {sender}" + ("" if key == old.get("key") else ", key replaced"),))
    return True, f"Saved: {PROVIDERS[provider]}, from {sender}."


def _post(url: str, headers: dict, body: dict) -> tuple[int, str]:
    """(status, the service's answer). One function, so the tests can stand in for the internet."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": AGENT, **headers})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")[:300]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:                              # noqa: BLE001 -- no internet, a time-out: reported, never raised
        return 0, str(e)[:300]


def send(to: str, subject: str, html: str, text: str) -> tuple[bool, str]:
    c = _read()
    if not ready():
        return False, "Email sending is not set up yet (Settings > Notifications)."
    who = c.get("name") or "Slick's ERP"
    if c["provider"] == "resend":
        status, answer = _post("https://api.resend.com/emails", {"Authorization": f"Bearer {c['key']}"},
                               {"from": f"{who} <{c['sender']}>", "to": [to], "subject": subject, "html": html, "text": text})
    else:
        status, answer = _post("https://api.brevo.com/v3/smtp/email", {"api-key": c["key"]},
                               {"sender": {"email": c["sender"], "name": who}, "to": [{"email": to}], "subject": subject, "htmlContent": html, "textContent": text})
    if 200 <= status < 300:
        return True, "sent"
    return False, f"The mail service answered {status or 'nothing'}: {answer}"
