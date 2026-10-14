"""One place for the owner's adjustable numbers and keyboard shortcuts (2026-10-01, plan D1 / C / D6).

Every number that used to be fixed in the code is listed here with a plain label, its default (= the behaviour before this page existed), limits and a
help line. `value(key)` reads the saved number or the default; `save()` validates, writes and audits (old -> new); `reset()` goes back to the default.
The engines read these through `value()`, so changing a number here changes the system without touching code. Keys that already existed
(stale_deposit_days, recon_debit_alert_factor, cc_missing_wait_days, cash_buffer, planner_*) keep their names, so nothing saved before is lost.

Keyboard shortcuts are stored as one JSON setting. Actions: search, park, new (a key, optionally with modifiers) and pages (the modifier that goes with
the digits 1..0 to open a menu page). Conflicts and keys the browser keeps for itself are refused; `reset_shortcuts()` restores the defaults."""
from __future__ import annotations

from engines.common import get_conn
import json
import os
import sqlite3

from engines import settings_store
from engines.settings_store import get_setting

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

# key, group, label, default, minimum, maximum, decimals (0 = whole number), unit, help
NUMBERS = [
    ("stale_check_days", "Checks", "A check is stale after", 180, 30, 720, 0, "days",
     "Philippine checks go stale about six months after their date. Checks the bank has not cleared by then are flagged in Data health, the Overview and the Dashboard."),
    ("stale_deposit_days", "Bank", "Old bank statement reminder after", 7, 0, 60, 0, "days",
     "Reminder when a check is still not cleared by the bank this many days after its check date, or the newest imported statement is this old."),
    ("recon_debit_alert_factor", "Bank", "Large bank debit alert at", 5, 1, 50, 1, "× usual",
     "An unexpected bank debit this many times larger than the usual size is shown as an alert."),
    ("show_cash_forecast", "Cash forecast", "Show the cash forecast and the Check-date planner (1 = yes, 0 = no)", 0, 0, 1, 0, "",
     "Off for now (owner, 2026-10-02): until the Sales / AR cycle brings real deposits the forecast has nothing to rest on. While off, the Cash forecast, "
     "the planner, the low-cash warnings and the purchase-order cash check are hidden. The Payables due page shows what is committed instead."),
    ("forecast_fresh_days", "Cash forecast", "Bank balance is too old to forecast from after", 14, 1, 60, 0, "days",
     "When the latest bank balance is older than this, the cash forecast does not use it (import a newer statement)."),
    ("inflow_weeks", "Cash forecast", "Expected deposits are averaged over the last", 8, 2, 52, 0, "weeks",
     "The forecast estimates future deposits from this many recent weeks of bank deposits."),
    ("cash_buffer", "Cash forecast", "Cash buffer to keep in the bank", 0, 0, 1_000_000_000, 2, "₱",
     "The Dashboard, forecast and planner warn when the balance would go below this amount."),
    ("cc_start_month", "Credit cards", "Card statements start from (year and month as YYYYMM, e.g. 202609; 0 = show every statement)", 0, 0, 209912, 0, "",
     "Owner, 2026-10-13: \"we start statements from september\", \"maybe we do not include statements prior september\". Statements of an earlier month "
     "stay saved but are left off the Statements list; a link on that page shows them again. The month is the statement's own month (its statement "
     "date), not its due date. Nothing is deleted, and the month can be moved."),
    ("cc_missing_wait_days", "Credit cards", "Wait before a card statement counts as missing", 7, 0, 60, 0, "days",
     "After a card's statement date, wait this many days before warning that its statement has not been entered."),
    ("min_money_amount", "Entry checks", "Smallest loan / limit amount accepted", 1, 0.01, 1000, 2, "₱",
     "A loan principal or card limit below this is refused as a likely typo (for example a missed zero)."),
    ("supplier_concentration_pct", "Dashboard alerts", "Supplier concentration alert at", 25, 5, 100, 0, "% of supplier debt",
     "The Dashboard warns when one supplier alone is at least this share of everything owed to suppliers."),
    ("po_in_forecast", "Purchasing", "Count open purchase orders in the cash forecast (1 = yes, 0 = no)", 1, 0, 1, 0, "",
     "An ordered purchase order counts as money going out on its expected pay date, in the forecast and the Check-date planner, before the supplier's invoice exists."),
    ("po_daily_payables_target", "Daily payment limit", "Daily target: plan each day at or under", 350_000, 0, 1_000_000_000, 2, "₱",
     "Owner, 2026-10-09: \"no AP above 350,000 to 400,000 per day\". Days above this show amber on the Calendar; Suggest dates and the split of an order "
     "plan each day under it. Everything that goes out counts: checks, SIs with no check yet, open orders, bank loans, cards (and the card reserve). 0 = off."),
    ("po_daily_payables_limit", "Daily payment limit", "Daily ceiling: confirm above (0 = no warning)", 400_000, 0, 1_000_000_000, 2, "₱",
     "A day above this shows red. Ordering, parking a check or moving a check date onto such a day asks \"Confirm anyway\" and is noted on the Review "
     "list. It is a warning only: the user decides."),
    ("po_daily_limit_scope", "Daily payment limit", "What the daily limit counts (1 = everything that goes out, 0 = suppliers only)", 1, 0, 1, 0, "",
     "1: checks, SIs with no check, open orders, lender checks, bank loans, cards and the card reserve (owner, 2026-10-10). 0: only the supplier side -- supplier "
     "checks, SIs with no check yet and open orders. With everything counted, most days pass ₱400,000 on loans and cards alone."),
    ("po_many_drs", "Purchasing", "More than one DR per order (1 = yes, 0 = no)", 0, 0, 1, 0, "",
     "0 (owner, 2026-10-11: \"since we have now a PO we can ask them to give 1 dr or si per PO\"): an order should come on ONE delivery receipt or sales invoice. "
     "A delivery that leaves part of the order asks first: the rest will not come (the order is closed), or keep it open for another DR (noted on the Review list). "
     "1: an order may be filled by several DRs with no question. Either way nothing is blocked, and every DR is its own payable."),
    ("sale_scan_first", "Sell", "Sales start from a scan (1 = yes, 0 = not yet)", 0, 0, 1, 0, "",
     "0 (owner, 2026-10-11: \"the important for now is recording of sales\" ... \"hide first the scan thing\"): the Scan out page, its menu entry "
     "and its Home card are hidden, and invoices typed after close ask no questions. 1 (the day the scanning routine starts): Scan out appears, goods "
     "are scanned out when they leave, and an invoice typed without a scan slip is noted on the Review list. Either way nothing is blocked."),
    ("signin_authenticator", "Sign-in", "Ask for the Google Authenticator code (1 = yes, 0 = no)", 1, 0, 1, 0, "",
     "1: after the password, the 6-digit code from the phone, once a day per device. 0 (owner, 2026-10-12: \"turnoff google authenticator but instead i "
     "will lower session duration\"): the code is skipped for a person Cloudflare has already checked under the same email, so sign-in is Cloudflare plus "
     "the password. Without Cloudflare in front the code is still asked. Turning it back on ends every sign-in that was made without a code."),
    ("signin_idle_minutes", "Sign-in", "Sign out after no activity for", 30, 5, 120, 0, "minutes",
     "Owner, 2026-10-12: \"30 minutes with the warning\". A person who opens no page and touches nothing for this long is signed out; a minute before, "
     "the page says so and offers to stay. Typing or clicking on an open page counts as activity. Two hours is the most the server allows."),
    ("signin_fingerprint", "Sign-in", "Sign in with fingerprint or face on a person's own device (1 = yes, 0 = no)", 1, 0, 1, 0, "",
     "Owner, 2026-10-13: \"For mobile view it is possible to have a biometrics for logging in?\". 1: after one password sign-in, a person can turn on "
     "fingerprint sign-in for that phone or laptop; from then on the device's own fingerprint, face or screen lock stands in for typing the password "
     "there. The fingerprint never leaves the device. The password stays for a new device, Cloudflare in front is unchanged, and the Scanner account "
     "never uses it. A changed or reset password, or Sign out everywhere, removes the person's devices. 0: only the password signs in."),
    ("email_morning", "Notifications", "The morning email (1 = yes, 0 = no)", 1, 0, 1, 0, "",
     "Owner, 2026-10-14: \"is email notification possible every morning?\" ... \"family too, 8 am\". A short email to each person who receives it "
     "(Settings > Notifications): yesterday's sales, today's list, and one line when something needs them. Sent every day, quiet days too; a "
     "section whose page is hidden from a person is left out of their email. Nothing is sent until the mail service is set up on that page."),
    ("email_morning_hour", "Notifications", "The morning email is sent at", 8, 0, 23, 0, "h",
     "Manila time, on the hour."),
    ("supplier_qr", "Payment statements", "QR code for the supplier on the printed statement (1 = yes, 0 = no)", 1, 0, 1, 0, "",
     "Owner, 2026-10-14: \"It is possible to have a qr code then if scanned it shows the a4?\" ... \"supplier\". 1: the thermal strip and the supplier's "
     "copy of a numbered payment statement carry a QR code. Scanning it opens that one statement's supplier copy with no sign-in, showing which checks "
     "have cleared; it opens nothing else, every opening is recorded on the statement's page, and one link can be switched off there. 0: no QR code is "
     "printed and every link already printed stops opening."),
    ("supplier_qr_days", "Payment statements", "The supplier's QR link works until this long after the statement's last check date", 90, 7, 730, 0, "days",
     "After that the link says it has expired. Counted from the day the link was made when the statement has no check yet."),
    ("signin_phone_approval", "Sign-in", "Sign in on a computer with your phone (1 = yes, 0 = no)", 1, 0, 1, 0, "",
     "Owner, 2026-10-14: \"it ensures they are the one logging in\". 1: a computer's sign-in page offers \"Sign in with your phone\" beside the password. "
     "It shows a QR code and a two-digit number; the person scans it with their own phone, signed in by its fingerprint, picks the number and approves, "
     "and the computer signs in. No Bluetooth. Only for a person with fingerprint sign-in turned on, never the Scanner account; a code works once, for "
     "two minutes. 0: the link is not shown."),
    ("phone_pages", "Phone", "The phone pages are on (1 = yes, 0 = no)", 0, 0, 1, 0, "",
     "Owner, 2026-10-12: \"We need pricelist, inventory. Stock in, stock out. Those items need mobility without using a laptop.\" 1: /phone/ opens -- "
     "Price list, Stock, Stock in and Stock out on a phone, added to its home screen -- for everyone allowed those pages, and the Scanner role signs in "
     "straight to it. 0: /phone/ goes to Home."),
    ("notify_card_statement", "Notifications", "Card reminder: the statement is out (1 = yes, 0 = no)", 1, 0, 1, 0, "",
     "Owner, 2026-10-13: credit card reminders on the phone. Sent from two days after the statement was expected, \"when the bank has usually sent it\", "
     "and again every 3 days until that month's statement is recorded or the card is marked \"No due this month\": the same cards as Credit cards > "
     "Missing statement check. Who receives them: Settings > Notifications."),
    ("notify_card_due", "Notifications", "Card reminder: the payment is due today (1 = yes, 0 = no)", 1, 0, 1, 0, "",
     "Sent on the due date only, twice that day (the two hours below), while the statement is not marked paid in full."),
    ("notify_card_fee", "Notifications", "Card reminder: the annual fee is next month (1 = yes, 0 = no)", 1, 0, 1, 0, "",
     "Sent on the 1st of the month before the card's annual-fee month, so there is time to ask the bank for a waiver."),
    ("notify_hour", "Notifications", "Reminders are sent at (hour of the day, Manila)", 8, 5, 12, 0, "o'clock",
     "Owner, 2026-10-13: 8 AM. The morning hour for every card reminder."),
    ("notify_second_hour", "Notifications", "The second due-date reminder is sent at (hour of the day, Manila)", 14, 12, 20, 0, "o'clock",
     "Owner, 2026-10-13: 2 PM. Only the due-today reminder is repeated, and only if the card is still not marked paid."),
    ("stock_old_years", "Stock", "A tire this old (by its DOT) is old stock", 3, 1, 15, 0, "years",
     "Tires at or above this age show as Old stock, to be sold first. The age comes from the DOT week and year typed when the tires are received."),
    ("affiliate_open_days", "Stock", "Flag tires lent or borrowed and still open after", 30, 1, 365, 0, "days",
     "A lend to (or a borrow from) a relative's business that is not returned, paid or set off after this many days goes on the Review list. Nothing is blocked."),
    ("po_window_months", "Daily payment limit", "Judge days against the limit, and reserve for cards, this far ahead (months; 0 = every day)", 6, 0, 24, 0, "months",
     "Owner, 2026-10-15: orders are the variable payable; checks a supplier has collected cannot move. A new order can fall due as far ahead as the longest "
     "supplier terms (150 days) plus delivery, so days within this window are judged (red / amber, the confirmation) and card reserves are placed; days "
     "beyond it are shown in grey and not judged, since nothing decided today can land there."),
    ("po_cash_in_week", "Daily payment limit", "Expected cash in per week, typed (used while sales history is under 8 weeks; 0 = none)", 0, 0, 1_000_000_000, 2, "₱",
     "Owner, 2026-10-15: the room for new orders rests on expected cash in, averaged from the last 8 weeks of recorded sales. Until that much history "
     "exists, the average is too small to mean anything; a typed weekly figure (what the shop usually takes in a week) stands in for it until then."),
    ("card_reserve_on", "Daily payment limit", "Reserve room for card dues before their SOA arrives (1 = yes, 0 = no)", 1, 0, 1, 0, "",
     "A card's SOA comes about a month before its due date. Until then, its usual due day carries an estimate -- the average of its last 3 statements -- "
     "shown as \"Card reserve (estimate)\", so the day is not filled by orders or checks."),
    ("po_max_defer_days", "Purchasing", "Suggest a later order date up to", 30, 0, 180, 0, "days later",
     "When an order does not fit the cash plan, the system looks this many days ahead for the first order date that does."),
    ("planner_max_days_late", "Check-date planner", "Planner may push a check at most", 7, 0, 60, 0, "days later",
     "How far the Check-date planner may move a check past its supplier's due date."),
    ("planner_daily_limit", "Check-date planner", "Planner daily limit (0 = none)", 0, 0, 1_000_000_000, 2, "₱",
     "Most money the planner puts on one check date. 0 means no limit."),
    ("planner_daily_max_checks", "Check-date planner", "Planner most checks per day (0 = none)", 0, 0, 500, 0, "checks",
     "Most checks the planner puts on one date. 0 means no limit."),
]
_BY_KEY = {n[0]: n for n in NUMBERS}
GROUPS = list(dict.fromkeys(n[1] for n in NUMBERS))


def _meta(key: str) -> dict:
    n = _BY_KEY[key]
    return {"key": n[0], "group": n[1], "label": n[2], "default": n[3], "min": n[4], "max": n[5], "decimals": n[6], "unit": n[7], "help": n[8]}


def registry() -> list[dict]:
    return [_meta(n[0]) for n in NUMBERS]


def default(key: str) -> float:
    return float(_BY_KEY[key][3])


def value(key: str) -> float:
    """The saved number, or the default when none is saved (or it is unreadable / outside its limits)."""
    m = _meta(key)
    try:
        v = float(get_setting(key, m["default"]))
    except (TypeError, ValueError):
        return float(m["default"])
    if v < m["min"] or v > m["max"]:
        return float(m["default"])
    return v


def int_value(key: str) -> int:
    return int(round(value(key)))


def _audit(conn, action: str, key: str, text: str) -> None:
    conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), ?, 'settings', NULL, ?)", (action, text))


def _write(conn, key: str, new_value: str, category: str, description: str) -> None:
    if conn.execute("SELECT 1 FROM settings WHERE key = ?", (key,)).fetchone():
        conn.execute("UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE key = ?", (new_value, key))
    else:
        conn.execute("INSERT INTO settings (key, value, category, description) VALUES (?, ?, ?, ?)", (key, new_value, category, description))


NOTICE_KEY = "home_notice"
NOTICE_UNTIL_KEY = "home_notice_until"          # 2026-10-09: with a time, the notice shows on EVERY page until then (e.g. "Update tonight at 12 AM")


def save_notice(text: str, until: str = "") -> tuple[bool, str]:
    """The owner's notice for everyone (blank = no notice): on Home, or -- with `until` (YYYY-MM-DDTHH:MM, Manila) -- on every page until that time,
    then gone by itself. Audited."""
    from datetime import datetime
    from engines.permissions import require
    ok, why = require("settings.edit")
    if not ok:
        return False, why
    text = " ".join(str(text or "").split())[:300]
    until = (until or "").strip()
    if until:
        try:
            until = datetime.fromisoformat(until).strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            return False, "The 'show on every page until' time is not a date and time."
    settings_store._CACHE.clear()
    with get_conn(DB_PATH) as conn:
        _write(conn, NOTICE_KEY, text, "general", "Notice shown to everyone on Home")
        _write(conn, NOTICE_UNTIL_KEY, until if text else "", "general", "The notice shows on every page until this time (Manila)")
        _audit(conn, "SETTING_CHANGED", NOTICE_KEY, f"Notice: {text or '(removed)'}" + (f"; on every page until {until}" if text and until else ""))
    if not text:
        return True, "Notice removed."
    return True, f"Notice saved: everyone sees it on every page until {until.replace('T', ' ')}." if until else "Notice saved: everyone sees it on Home."


def page_notice() -> str:
    """The notice to show on every page right now ('' when there is none, it has no time, or its time has passed)."""
    from engines.clock import manila_now
    from engines.settings_store import get_setting
    text, until = get_setting(NOTICE_KEY, "") or "", get_setting(NOTICE_UNTIL_KEY, "") or ""
    return text if text and until and manila_now().strftime("%Y-%m-%dT%H:%M") < until else ""


def _fmt(v: float, decimals: int) -> str:
    return f"{v:,.{decimals}f}" if decimals else f"{int(round(v)):,}"


def save(key: str, new_value) -> tuple[bool, str]:
    from engines.permissions import require
    ok, why = require("settings.edit")
    if not ok:
        return False, why
    if key not in _BY_KEY:
        return False, "Unknown setting."
    m = _meta(key)
    try:
        v = float(new_value)
    except (TypeError, ValueError):
        return False, f"{m['label']}: enter a number."
    if v < m["min"] or v > m["max"]:
        return False, f"{m['label']}: must be between {_fmt(m['min'], m['decimals'])} and {_fmt(m['max'], m['decimals'])} {m['unit']}."
    old = value(key)
    if abs(old - v) < 1e-9:
        return True, f"{m['label']}: unchanged."
    stored = str(int(round(v))) if m["decimals"] == 0 else str(round(v, m["decimals"]))
    settings_store._CACHE.clear()                     # the next read sees the change at once
    with get_conn(DB_PATH) as conn:
        _write(conn, key, stored, "settings", m["label"])
        _audit(conn, "SETTING_CHANGED", key, f"{m['label']}: {_fmt(old, m['decimals'])} -> {_fmt(v, m['decimals'])} {m['unit']}")
    return True, f"{m['label']}: {_fmt(old, m['decimals'])} -> {_fmt(v, m['decimals'])} {m['unit']}."


def reset(key: str) -> tuple[bool, str]:
    from engines.permissions import require
    ok, why = require("settings.edit")
    if not ok:
        return False, why
    if key not in _BY_KEY:
        return False, "Unknown setting."
    m = _meta(key)
    old = value(key)
    settings_store._CACHE.clear()                     # the next read sees the change at once
    with get_conn(DB_PATH) as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        if abs(old - m["default"]) > 1e-9:
            _audit(conn, "SETTING_RESET", key, f"{m['label']}: {_fmt(old, m['decimals'])} -> {_fmt(float(m['default']), m['decimals'])} {m['unit']} (back to the default)")
    return True, f"{m['label']}: back to the default ({_fmt(float(m['default']), m['decimals'])} {m['unit']})."


def changed_from_default() -> list[dict]:
    return [m for m in registry() if abs(value(m["key"]) - m["default"]) > 1e-9]


# ---------------------------------------------------------------------------------------------------------------------------------------
# Keyboard shortcuts
# ---------------------------------------------------------------------------------------------------------------------------------------
SHORTCUT_KEY = "keyboard_shortcuts"
MODIFIERS = ["Alt", "Ctrl+Alt", "Alt+Shift", "None"]
SHORTCUT_ACTIONS = {
    "search": "Open Search everything",
    "park": "Press the screen's main Park / Save / Add button",
    "new": "Open the New ... form (batch, loan note, bank loan)",
}
DEFAULT_SHORTCUTS = {"search": {"mod": "None", "key": "/"}, "park": {"mod": "Alt", "key": "P"}, "new": {"mod": "Alt", "key": "N"}, "pages": {"mod": "Alt"}}
SYMBOL_KEYS = ["/", "?", ".", ",", ";", "[", "]", "-", "="]
BROWSER_ALT_LETTERS = "FED"          # Alt+F / E / D open browser menus


def _norm_key(k) -> str:
    k = (k or "").strip() if isinstance(k, str) else ""
    return k.upper() if len(k) == 1 and k.isalpha() else k


def shortcuts() -> dict:
    """The saved shortcuts merged over the defaults (a damaged value falls back to the defaults)."""
    out = json.loads(json.dumps(DEFAULT_SHORTCUTS))
    try:
        saved = json.loads(get_setting(SHORTCUT_KEY, "") or "{}")
    except (TypeError, ValueError):
        return out
    if not isinstance(saved, dict):
        return out
    for a in SHORTCUT_ACTIONS:
        s = saved.get(a)
        if isinstance(s, dict) and s.get("mod") in MODIFIERS and isinstance(s.get("key"), str) and s["key"]:
            out[a] = {"mod": s["mod"], "key": _norm_key(s["key"])}
    if isinstance(saved.get("pages"), dict) and saved["pages"].get("mod") in MODIFIERS and saved["pages"]["mod"] != "None":
        out["pages"] = {"mod": saved["pages"]["mod"]}
    return out


def label(sc: dict) -> str:
    return sc["key"] if sc["mod"] == "None" else f"{sc['mod']}+{sc['key']}"


def problems(config: dict) -> list[str]:
    """Why this whole shortcut set cannot be used (empty = fine)."""
    out: list[str] = []
    seen: dict[tuple[str, str], str] = {}
    pages_mod = config["pages"]["mod"]
    if pages_mod not in MODIFIERS or pages_mod == "None":
        out.append("Menu pages: choose Alt, Ctrl+Alt or Alt+Shift for the number keys.")
    for a, text in SHORTCUT_ACTIONS.items():
        mod, key = config[a]["mod"], _norm_key(config[a]["key"])
        if mod not in MODIFIERS:
            out.append(f"{text}: unknown modifier.")
        elif len(key) != 1 or not ((key.isascii() and key.isalnum()) or key in SYMBOL_KEYS):
            out.append(f"{text}: use one letter, one digit or one of {' '.join(SYMBOL_KEYS)}.")
        elif mod == "None" and key.isalnum():
            out.append(f"{text}: a plain letter or digit would fire while you type -- add Alt, or use a symbol such as /.")
        elif mod == "Alt" and key in BROWSER_ALT_LETTERS:
            out.append(f"{text}: Alt+{key} opens a browser menu -- pick another letter.")
        elif mod == pages_mod and key.isdigit():
            out.append(f"{text}: {label({'mod': mod, 'key': key})} is already used to open a menu page.")
        elif (mod, key) in seen:
            out.append(f"{text}: {label({'mod': mod, 'key': key})} is already used for \"{seen[(mod, key)]}\".")
        else:
            seen[(mod, key)] = text
    return out


def save_shortcuts(config: dict) -> tuple[bool, str]:
    cfg = {a: {"mod": config[a]["mod"], "key": _norm_key(config[a]["key"])} for a in SHORTCUT_ACTIONS}
    cfg["pages"] = {"mod": config["pages"]["mod"]}
    bad = problems(cfg)
    if bad:
        return False, " ".join(bad)
    old = shortcuts()
    if cfg == old:
        return True, "Shortcuts unchanged."
    changes = [f"{text}: {label(old[a])} -> {label(cfg[a])}" for a, text in SHORTCUT_ACTIONS.items() if old[a] != cfg[a]]
    if old["pages"] != cfg["pages"]:
        changes.append(f"Menu pages: {old['pages']['mod']}+1..0 -> {cfg['pages']['mod']}+1..0")
    settings_store._CACHE.clear()                     # the next read sees the change at once
    with get_conn(DB_PATH) as conn:
        _write(conn, SHORTCUT_KEY, json.dumps(cfg), "settings", "Keyboard shortcuts")
        _audit(conn, "SHORTCUTS_CHANGED", SHORTCUT_KEY, "; ".join(changes))
    return True, "Shortcuts saved: " + "; ".join(changes) + "."


def reset_shortcuts() -> tuple[bool, str]:
    settings_store._CACHE.clear()                     # the next read sees the change at once
    with get_conn(DB_PATH) as conn:
        had = conn.execute("SELECT 1 FROM settings WHERE key = ?", (SHORTCUT_KEY,)).fetchone()
        conn.execute("DELETE FROM settings WHERE key = ?", (SHORTCUT_KEY,))
        if had:
            _audit(conn, "SHORTCUTS_RESET", SHORTCUT_KEY, "Keyboard shortcuts back to the defaults")
    return True, "Keyboard shortcuts are back to the defaults."


def phone_on() -> bool:
    """Are the phone pages open (owner, 2026-10-12)? Off until the System admin switches them on."""
    return int_value("phone_pages") == 1


def idle_minutes() -> int:
    """How long a sign-in lasts with no activity (owner, 2026-10-12)."""
    return int_value("signin_idle_minutes")


def authenticator_on() -> bool:
    """Is the Google Authenticator code asked at sign-in (owner, 2026-10-12)? On unless the System admin switched it off in Settings."""
    return int_value("signin_authenticator") == 1


def sales_scan_on() -> bool:
    """Has the scanning routine started (owner, 2026-10-11)? Off: Scan out is hidden everywhere and recording sales asks no scan questions."""
    return int_value("sale_scan_first") == 1


def forecast_shown() -> bool:
    """Whether the cash forecast and its dependants (planner, low-cash warnings, PO cash check) are visible. Off by default (owner, 2026-10-02)."""
    return int_value("show_cash_forecast") == 1
