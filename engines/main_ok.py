"""Is the main system OK? (2026-10-03). The live AP keeps running until JRCR says the new main system (this one) is OK; only then is the live data migrated in, with a backup and the
owner's go-ahead. This is the checklist for that decision: plain items, ticked by the System admin with who and when, kept in the settings table. Nothing here changes any data.
It only says how far the list is; the decision stays the owner's."""
from __future__ import annotations

import json
from datetime import datetime

from engines.acting_user import acting_user
from engines.clock import manila_now
from engines.permissions import require as require_permission
from engines.settings_store import get_setting, set_setting

KEY = "main_ok_checklist"
ITEMS = [
    ("order_to_bank", "One real order walked from the price list to the bank: price, order, goods, invoice, batch, check, release, cleared."),
    ("release_slip", "A release slip printed with a statement, signed by a supplier, and recorded by the Releaser."),
    ("figures_match", "For one week the figures match the live AP: Payables due, the bank balance, the open batches."),
    ("brother_week", "The brother used the Purchasing screens for a week and found nothing missing."),
    ("owners_signed_in", "Each family owner signed in once and sees what they should (and not Settings, Users or the audit log)."),
    ("backup_restore", "A backup was taken, and restored on a copy to prove it works."),
]


def _state() -> dict:
    try:
        return json.loads(get_setting(KEY, "{}") or "{}")
    except ValueError:
        return {}


def status() -> list[dict]:
    """[{'key', 'text', 'done', 'by', 'on'}] in order."""
    st = _state()
    return [{"key": k, "text": t, "done": k in st, "by": st.get(k, {}).get("by", ""), "on": st.get(k, {}).get("on", "")} for k, t in ITEMS]


def progress() -> tuple[int, int]:
    s = status()
    return sum(1 for i in s if i["done"]), len(s)


def tick(key: str, done: bool) -> tuple[bool, str]:
    ok, why = require_permission("settings.edit")                       # the System admin's call
    if not ok:
        return False, why
    if key not in dict(ITEMS):
        return False, "That item is not on the list."
    st = _state()
    if done:
        st[key] = {"by": acting_user(), "on": manila_now().strftime("%Y-%m-%d")}
    else:
        st.pop(key, None)
    set_setting(KEY, json.dumps(st), "system", "Checklist for deciding the main system is OK (engines/main_ok.py)")
    return True, "Ticked." if done else "Un-ticked."
