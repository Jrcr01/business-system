"""The one door to the AI (owner, 2026-10-20: "I want to ready my system for this"). Every part of the system that wants AI help calls ask() here;
nothing else talks to the AI. It keeps the owner's rules:
  * off until the System admin adds the key (Settings > AI) and switches it on (settings: ai_on);
  * a hard monthly cap in pesos (ai_monthly_cap): at the cap the AI stops until the 1st of next month, and the System admin is told at 80%;
  * every call is logged with its cost (table ai_usage), and every answer is only a suggestion -- the feature that asked shows it to a person, who
    confirms before anything is saved.
The key is kept in a file beside the database (ai_key.txt), never in the code or the database, and only its last four characters are ever shown.
Model and prices: Claude Opus 5.5, $4 / $20 per million input / output tokens (Anthropic first-party prices, 2026-10)."""
from __future__ import annotations

import base64
import os
from datetime import date
from pathlib import Path

MODEL = "claude-opus-5-5"
PRICES = {"claude-opus-5-5": (4.00, 20.00), "claude-sonnet-5-5": (2.00, 10.00), "claude-haiku-5-5": (0.10, 0.50)}   # US$ per million tokens
WARN_AT = 0.80


def _key_path() -> Path:
    import engines.db_engine as d
    return Path(d.DB_PATH).parent / "ai_key.txt"


def _key() -> str:
    try:
        return _key_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def status() -> dict:
    """What Settings > AI may show: on/off, the key's last four characters, this month's spend and the cap."""
    from engines.settings_registry import int_value, value
    key = _key()
    spent, cap = month_spent(), float(value("ai_monthly_cap"))
    return {"on": bool(int_value("ai_on")), "has_key": bool(key), "key_end": key[-4:] if len(key) >= 8 else "", "model": MODEL,
            "spent": spent, "cap": cap, "pct": round(spent / cap * 100, 1) if cap > 0 else 0.0, "ready": ready()}


def ready() -> bool:
    from engines.settings_registry import int_value
    return bool(int_value("ai_on")) and bool(_key())


def save_key(key: str) -> tuple[bool, str]:
    """The System admin pastes the key from the Anthropic console. Kept beside the database, readable by the server only."""
    from engines.permissions import require
    ok, why = require("settings.edit")
    if not ok:
        return False, why
    key = (key or "").strip()
    if not key.startswith("sk-ant-") or len(key) < 20 or any(c.isspace() for c in key):
        return False, "That does not look like an Anthropic API key (it starts with sk-ant-). Copy it again from the Anthropic console."
    path = _key_path()
    replaced = bool(_key())
    path.write_text(key, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    _audit("AI_KEY_SAVED", f"AI key {'replaced' if replaced else 'added'} (ends {key[-4:]})")
    return True, f"Key saved (ends {key[-4:]}). Switch the AI on below when you are ready."


def remove_key() -> tuple[bool, str]:
    from engines.permissions import require
    ok, why = require("settings.edit")
    if not ok:
        return False, why
    try:
        _key_path().unlink()
    except FileNotFoundError:
        return True, "There was no key."
    _audit("AI_KEY_REMOVED", "AI key removed: the AI cannot be used until a key is added again")
    return True, "Key removed. The AI cannot be used until a key is added again."


def _audit(action: str, text: str) -> None:
    from engines.common import write_tx
    with write_tx() as conn:
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), ?, 'settings', 0, ?)", (action, text))


def _month_start(today: date | None = None) -> str:
    from engines.clock import manila_today
    t = today or manila_today()
    return t.replace(day=1).isoformat()


def month_spent(today: date | None = None) -> float:
    """This month's AI cost in pesos (Manila months)."""
    from engines.common import get_conn
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT COALESCE(SUM(cost_php), 0) FROM ai_usage WHERE date(used_at, '+8 hours') >= ?", (_month_start(today),)).fetchone()
        return round(float(row[0] or 0), 2)
    except Exception:                                   # an older database without the table: nothing spent
        return 0.0


def recent(limit: int = 30) -> list[dict]:
    from engines.common import get_conn
    try:
        with get_conn() as conn:
            rows = conn.execute("""SELECT datetime(used_at, '+8 hours'), user_email, feature, model, input_tokens, output_tokens, cost_php, ok, note
                                   FROM ai_usage ORDER BY usage_id DESC LIMIT ?""", (int(limit),)).fetchall()
    except Exception:
        return []
    return [{"at": r[0], "who": r[1] or "", "feature": r[2], "model": r[3], "tokens_in": r[4], "tokens_out": r[5], "cost": float(r[6] or 0),
             "ok": bool(r[7]), "note": r[8] or ""} for r in rows]


def cost_php(model: str, tokens_in: int, tokens_out: int) -> float:
    from engines.settings_registry import value
    p_in, p_out = PRICES.get(model, PRICES[MODEL])
    return round((tokens_in * p_in + tokens_out * p_out) / 1_000_000 * value("ai_peso_per_dollar"), 4)


def _log(feature: str, model: str, tokens_in: int, tokens_out: int, ok: bool, note: str = "") -> float:
    from engines.common import write_tx
    from engines.acting_user import acting_email
    cost = cost_php(model, tokens_in, tokens_out)
    before = month_spent()
    with write_tx() as conn:
        conn.execute("INSERT INTO ai_usage (user_email, feature, model, input_tokens, output_tokens, cost_php, ok, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (acting_email() or "", feature, model, int(tokens_in), int(tokens_out), cost, 1 if ok else 0, note or None))
    _warn_if_near(before, before + cost)
    return cost


def _warn_if_near(before: float, after: float) -> None:
    """Once a month, when spending crosses 80% of the cap: a note on the Review list for the System admin."""
    from engines.settings_registry import value
    cap = float(value("ai_monthly_cap"))
    if cap <= 0 or not (before < cap * WARN_AT <= after):
        return
    from engines.common import write_tx
    from engines.review_flags import flag
    with write_tx() as conn:
        flag(conn, "AI_SPEND_80", "settings", None, f"AI cost this month is ₱{after:,.2f}, past 80% of the ₱{cap:,.0f} cap. At the cap the AI stops until "
                                                     "next month; raise the cap on Settings > AI if needed.")


def _client():
    """The Anthropic client (official SDK). Separate, so the tests can stand in for the internet."""
    import anthropic
    return anthropic.Anthropic(api_key=_key(), timeout=120.0, max_retries=2)


def image_block(data: bytes, media_type: str) -> dict:
    """A photo for ask(): JPEG, PNG, GIF or WEBP."""
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64.standard_b64encode(data).decode("ascii")}}


def pdf_block(data: bytes) -> dict:
    return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": base64.standard_b64encode(data).decode("ascii")}}


def ask(feature: str, content: list | str, system: str = "", max_tokens: int = 16000, effort: str = "medium") -> tuple[bool, str, str]:
    """Ask the AI. content: text, or a list of blocks (image_block / pdf_block / {"type": "text", ...}). Returns (ok, answer text, message for the
    person). Refuses -- without calling anything -- when the AI is off, there is no key, or this month's cap is reached."""
    from engines.settings_registry import value
    if not ready():
        return False, "", "AI help is switched off. The System admin can switch it on in Settings > AI."
    cap = float(value("ai_monthly_cap"))
    if cap <= 0 or month_spent() >= cap:
        return False, "", f"This month's AI budget (₱{cap:,.0f}) is used up. The AI is back on the 1st; please type it in for now."
    import anthropic
    messages = [{"role": "user", "content": content}]
    try:
        kwargs = {"model": MODEL, "max_tokens": int(max_tokens), "messages": messages, "output_config": {"effort": effort}}
        if system:
            kwargs["system"] = system
        resp = _client().messages.create(**kwargs)
    except anthropic.AuthenticationError:
        _log(feature, MODEL, 0, 0, False, "the key was refused")
        return False, "", "The AI key was refused. The System admin should check it in Settings > AI."
    except anthropic.RateLimitError:
        _log(feature, MODEL, 0, 0, False, "busy (rate limit)")
        return False, "", "The AI is busy. Try again in a minute."
    except anthropic.APIStatusError as e:
        _log(feature, MODEL, 0, 0, False, f"error {e.status_code}")
        return False, "", "The AI could not answer just now. Try again later, or type it in."
    except anthropic.APIConnectionError:
        _log(feature, MODEL, 0, 0, False, "no connection")
        return False, "", "The AI could not be reached. Try again later, or type it in."
    usage = resp.usage
    tokens_in = int(getattr(usage, "input_tokens", 0) or 0) + int(getattr(usage, "cache_creation_input_tokens", 0) or 0) + int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    tokens_out = int(getattr(usage, "output_tokens", 0) or 0)
    if resp.stop_reason == "refusal":
        _log(feature, MODEL, tokens_in, tokens_out, False, "declined")
        return False, "", "The AI declined this one. Please type it in."
    text = "".join(b.text for b in resp.content if b.type == "text")
    cut = resp.stop_reason == "max_tokens"
    _log(feature, MODEL, tokens_in, tokens_out, not cut, "answer cut short" if cut else "")
    if cut:
        return False, text, "The answer was too long and was cut short. Try fewer pages at a time."
    return True, text, ""
