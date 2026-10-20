"""Bank accounts (owner, 2026-10-20; mock-up bank_setup.html, "build"): each account's nature, its check-number rule, which statement file it
imports and what it is used for -- read by the code instead of "Metrobank, branch 399, account 1". The check-writing account is ONE account at a
time (the booklets, the check printer and the daily limit belong to it); any active account may import statements and count in the forecast.
Slick's account keeps the values the code assumed (migration 117), so nothing changes for Slick's."""
from __future__ import annotations

import re

NATURES = [("operating", "Operating (checks, suppliers)"), ("settlement", "Settlement (customer deposits, collections)"), ("payroll", "Payroll"),
           ("savings", "Savings / reserve"), ("loan", "Loan account"), ("ewallet", "E-wallet (GCash, Maya)"), ("other", "Other")]
NATURE_DEFAULTS = {"operating": (1, 1, 1), "settlement": (0, 1, 0), "payroll": (0, 1, 0), "savings": (0, 1, 0), "loan": (0, 0, 0), "ewallet": (0, 1, 0), "other": (0, 0, 0)}
READERS = [("mbos", "Metrobank MBOS (PDF / CSV / Excel)"), ("generic", "Generic columns (CSV / Excel): date, check no., description, debit, credit, balance")]
NUMBER_FORMS = [("full", "the full number (prefix + leaf)"), ("leaf", "the leaf only"), ("either", "either")]
BANKS = ["Metrobank", "BDO", "BPI", "PNB", "RCBC", "Security Bank", "UnionBank", "China Bank", "EastWest", "Landbank", "GCash", "Maya", "Other"]
COLS = ("bank_account_id", "bank_name", "account_name", "account_number", "branch_code", "shown_as", "nature", "check_prefix", "check_digits",
        "statement_number_form", "statement_reader", "writes_checks", "in_forecast", "cards_paid_from", "is_active", "closed_at",
        "check_width_mm", "check_height_mm", "check_offset_x_mm", "check_offset_y_mm")
_LABEL = dict(NATURES)
_FALLBACK = {"bank_account_id": 1, "bank_name": "Metrobank", "account_name": "", "account_number": "", "branch_code": "399", "shown_as": "Metrobank Account 1",
             "nature": "operating", "check_prefix": "399", "check_digits": 7, "statement_number_form": "either", "statement_reader": "mbos",
             "writes_checks": 1, "in_forecast": 1, "cards_paid_from": 1, "is_active": 1, "closed_at": None,
             "check_width_mm": 203.2, "check_height_mm": 76.2, "check_offset_x_mm": 0.0, "check_offset_y_mm": 0.0}


def _row(r) -> dict:
    d = dict(zip(COLS, r))
    d["shown_as"] = d["shown_as"] or f"{d['bank_name']} {d['account_name']}".strip()
    d["check_prefix"] = str(d["check_prefix"] or "")
    d["check_digits"] = int(d["check_digits"] or 7)
    d["nature_label"] = _LABEL.get(d["nature"], d["nature"])
    d["last4"] = str(d["account_number"] or "")[-4:]
    d["example"] = format_number(d, 2554301 % (10 ** d["check_digits"]))                 # e.g. 3992554301 for a 399 + 7 account
    return d


def listing(include_closed: bool = True) -> list[dict]:
    from engines.common import get_conn
    try:
        with get_conn() as conn:
            rows = conn.execute(f"SELECT {', '.join(COLS)} FROM bank_accounts {'' if include_closed else 'WHERE is_active = 1 '}ORDER BY is_active DESC, writes_checks DESC, bank_account_id").fetchall()
    except Exception:                                   # noqa: BLE001 -- a database before migration 117: the one account the code always assumed
        return [dict(_FALLBACK)]
    return [_row(r) for r in rows]


def get(bank_account_id: int) -> dict | None:
    return next((a for a in listing() if int(a["bank_account_id"]) == int(bank_account_id)), None)


def check_account() -> dict:
    """The account checks are written from: the one ticked 'writes checks', else account 1, else the first active one."""
    accts = listing()
    for a in accts:
        if a["writes_checks"] and a["is_active"]:
            return a
    return next((a for a in accts if int(a["bank_account_id"]) == 1), accts[0] if accts else dict(_FALLBACK))


def check_account_id() -> int:
    return int(check_account()["bank_account_id"])


def forecast_ids() -> list[int]:
    return [int(a["bank_account_id"]) for a in listing() if a["in_forecast"] and a["is_active"]]


def cards_account() -> dict:
    accts = listing()
    return next((a for a in accts if a["cards_paid_from"] and a["is_active"]), check_account())


def prefixes() -> list[tuple[str, int]]:
    """(prefix, digits) of every account with a prefix: what a full check number may start with."""
    out = []
    for a in listing():
        if a["check_prefix"]:
            out.append((a["check_prefix"], a["check_digits"]))
    return out or [("399", 7)]


def format_number(acct: dict, leaf: int) -> str:
    """The full check number for a leaf on this account: prefix + the leaf zero-filled to the digits."""
    return f"{acct['check_prefix']}{str(int(leaf)).zfill(int(acct['check_digits']))}"


def leaf_of(text, acct: dict | None = None) -> int | None:
    """The leaf a typed or imported check number stands for: the full number less the prefix, or the bare leaf; None when it is not a number."""
    s = re.sub(r"\D", "", str(text or ""))
    if not s:
        return None
    for prefix, digits in ([(acct["check_prefix"], acct["check_digits"])] if acct and acct.get("check_prefix") else prefixes()):
        if prefix and len(s) == len(prefix) + digits and s.startswith(prefix):
            return int(s[len(prefix):])
    return int(s)


def _clean(values: dict) -> tuple[dict | None, str]:
    v = {k: " ".join(str(values.get(k) or "").split()) for k in ("bank_name", "account_name", "account_number", "branch_code", "shown_as", "nature",
                                                                   "check_prefix", "statement_number_form", "statement_reader")}
    if not v["bank_name"] or not v["account_name"] or not v["account_number"]:
        return None, "Type the bank, the account name and the account number."
    if v["nature"] not in _LABEL:
        return None, "Pick the nature of the account."
    if v["statement_reader"] not in dict(READERS):
        return None, "Pick the statement file the bank gives."
    if v["statement_number_form"] not in dict(NUMBER_FORMS):
        v["statement_number_form"] = "either"
    if v["check_prefix"] and not v["check_prefix"].isdigit():
        return None, "The check prefix is digits only (e.g. 399), or blank."
    try:
        digits = int(str(values.get("check_digits") or 7))
    except ValueError:
        return None, "Digits after the prefix: a whole number."
    if not 4 <= digits <= 12:
        return None, "Digits after the prefix: between 4 and 12."
    v["check_digits"] = digits
    v["shown_as"] = v["shown_as"] or f"{v['bank_name']} {v['account_name']}"
    for k in ("writes_checks", "in_forecast", "cards_paid_from"):
        v[k] = 1 if str(values.get(k, "")) in ("1", "on", "true", "True") else 0
    for k in ("check_width_mm", "check_height_mm", "check_offset_x_mm", "check_offset_y_mm"):
        try:
            v[k] = float(values.get(k) if values.get(k) not in (None, "") else _FALLBACK[k])
        except ValueError:
            return None, "The check printer measurements are numbers (mm)."
    return v, ""


def _audit(conn, action: str, aid: int, text: str) -> None:
    conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), ?, 'bank_accounts', ?, ?)", (action, int(aid), text[:1500]))


def add(values: dict) -> tuple[bool, str, int | None]:
    """A new account. Its nature fills in 'used for'; it never takes check writing from the current check account by itself."""
    from engines.common import write_tx
    from engines.permissions import require
    ok, why = require("settings.edit")
    if not ok:
        return False, why, None
    v, problem = _clean({"statement_reader": "generic", "statement_number_form": "either", "check_prefix": "", "check_digits": 10, **values,
                         **dict(zip(("writes_checks", "in_forecast", "cards_paid_from"), NATURE_DEFAULTS.get(values.get("nature", ""), (0, 1, 0))))})
    if v is None:
        return False, problem, None
    v["writes_checks"] = 0
    v["cards_paid_from"] = 0
    with write_tx() as conn:
        if conn.execute("SELECT 1 FROM bank_accounts WHERE account_number = ?", (v["account_number"],)).fetchone():
            return False, "An account with that number is already on file.", None
        aid = conn.execute("""INSERT INTO bank_accounts (bank_name, account_name, account_number, branch_code, shown_as, nature, check_prefix, check_digits,
                              statement_number_form, statement_reader, writes_checks, in_forecast, cards_paid_from, check_width_mm, check_height_mm,
                              check_offset_x_mm, check_offset_y_mm) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING bank_account_id""",
                           (v["bank_name"], v["account_name"], v["account_number"], v["branch_code"] or v["check_prefix"] or "", v["shown_as"], v["nature"],
                            v["check_prefix"], v["check_digits"], v["statement_number_form"], v["statement_reader"], 0, v["in_forecast"], 0,
                            v["check_width_mm"], v["check_height_mm"], v["check_offset_x_mm"], v["check_offset_y_mm"])).fetchone()[0]
        _audit(conn, "BANK_ACCOUNT_ADDED", aid, f"{v['shown_as']} ({v['bank_name']}, …{v['account_number'][-4:]}), {_LABEL[v['nature']]}")
    return True, f"{v['shown_as']} added. Edit it to set its check numbers, statement file and what it is used for.", int(aid)


def save(bank_account_id: int, values: dict) -> tuple[bool, str]:
    """Settings > Bank accounts > Edit. Ticking 'writes checks' moves check writing to this account (the only one that may have it); the same
    for 'cards paid from'. Old -> new in the audit log."""
    from engines.common import write_tx
    from engines.permissions import require
    ok, why = require("settings.edit")
    if not ok:
        return False, why
    before = get(bank_account_id)
    if before is None:
        return False, "Account not found."
    v, problem = _clean(values)
    if v is None:
        return False, problem
    if before["writes_checks"] and not v["writes_checks"] and not any(a["writes_checks"] for a in listing() if int(a["bank_account_id"]) != int(bank_account_id)):
        return False, "One account must write checks. Tick it on the other account first."
    changed = [k for k in v if str(v[k]) != str(before.get(k))]
    if not changed:
        return True, "No change."
    with write_tx() as conn:
        if v["account_number"] != before["account_number"] and conn.execute("SELECT 1 FROM bank_accounts WHERE account_number = ? AND bank_account_id != ?", (v["account_number"], int(bank_account_id))).fetchone():
            return False, "An account with that number is already on file."
        if v["writes_checks"]:
            conn.execute("UPDATE bank_accounts SET writes_checks = 0 WHERE bank_account_id != ?", (int(bank_account_id),))
        if v["cards_paid_from"]:
            conn.execute("UPDATE bank_accounts SET cards_paid_from = 0 WHERE bank_account_id != ?", (int(bank_account_id),))
        conn.execute("""UPDATE bank_accounts SET bank_name = ?, account_name = ?, account_number = ?, branch_code = ?, shown_as = ?, nature = ?, check_prefix = ?,
                        check_digits = ?, statement_number_form = ?, statement_reader = ?, writes_checks = ?, in_forecast = ?, cards_paid_from = ?,
                        check_width_mm = ?, check_height_mm = ?, check_offset_x_mm = ?, check_offset_y_mm = ?, updated_at = CURRENT_TIMESTAMP WHERE bank_account_id = ?""",
                     (v["bank_name"], v["account_name"], v["account_number"], v["branch_code"] or v["check_prefix"] or "", v["shown_as"], v["nature"], v["check_prefix"],
                      v["check_digits"], v["statement_number_form"], v["statement_reader"], v["writes_checks"], v["in_forecast"], v["cards_paid_from"],
                      v["check_width_mm"], v["check_height_mm"], v["check_offset_x_mm"], v["check_offset_y_mm"], int(bank_account_id)))
        _audit(conn, "BANK_ACCOUNT_CHANGED", bank_account_id, f"{before['shown_as']}: " + "; ".join(f"{k}: '{before.get(k)}' -> '{v[k]}'" for k in changed))
    return True, f"Saved: {v['shown_as']}."


def close(bank_account_id: int) -> tuple[bool, str]:
    from engines.common import write_tx
    from engines.permissions import require
    ok, why = require("settings.edit")
    if not ok:
        return False, why
    a = get(bank_account_id)
    if a is None:
        return False, "Account not found."
    if a["writes_checks"]:
        return False, "This account writes the checks. Move check writing to another account first."
    with write_tx() as conn:
        conn.execute("UPDATE bank_accounts SET is_active = 0, closed_at = CURRENT_TIMESTAMP, in_forecast = 0, cards_paid_from = 0 WHERE bank_account_id = ?", (int(bank_account_id),))
        _audit(conn, "BANK_ACCOUNT_CLOSED", bank_account_id, f"{a['shown_as']} closed: its history stays, nothing new goes to it")
    return True, f"{a['shown_as']} closed."


def reopen(bank_account_id: int) -> tuple[bool, str]:
    from engines.common import write_tx
    from engines.permissions import require
    ok, why = require("settings.edit")
    if not ok:
        return False, why
    a = get(bank_account_id)
    if a is None:
        return False, "Account not found."
    with write_tx() as conn:
        conn.execute("UPDATE bank_accounts SET is_active = 1, closed_at = NULL WHERE bank_account_id = ?", (int(bank_account_id),))
        _audit(conn, "BANK_ACCOUNT_REOPENED", bank_account_id, f"{a['shown_as']} reopened")
    return True, f"{a['shown_as']} reopened."
