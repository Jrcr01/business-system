"""The company profile (owner, 2026-10-20, mock-up company_profile.html, "build"): who the business is, where the system runs, money and dates,
and the document number formats -- typed once in Settings > Company instead of being written into the code. The defaults are Slick's values,
so a database without a saved profile (Slick's own) looks exactly as before; a new business fills the tab first.

Everything that showed "Slick's Tire Supply", the logo, the domain or the mail sender reads from here: profile()[key], name(), short_name(),
site(), logo_path(), symbol(), fmt_date(), and number()/number_prefix() for the document formats (YYYY = year, YY = 2-digit year, N... = the
running number zero-padded to that many digits; the count restarts each year only when the format has a year)."""
from __future__ import annotations

import os
import re
from datetime import date, datetime
from engines.clock import manila_today
from pathlib import Path

CATEGORY = "company"

# key, label, default (Slick's), kind: text / choice / month / fmt
FIELDS = [
    ("company_name", "Business name (on documents)", "Slick's Tire Supply", "text"),
    ("company_short", "Short name (top bar, phone app, emails)", "Slick's", "text"),
    ("company_legal", "Registered name (BIR, if different)", "", "text"),
    ("company_tin", "TIN", "", "text"),
    ("company_vat", "VAT status", "vat", "choice"),
    ("company_line", "Business line (shown on printouts)", "", "text"),
    ("company_address", "Address", "", "text"),
    ("company_phone", "Phone", "", "text"),
    ("company_email", "Email (public)", "", "text"),
    ("company_website", "Website", "slickstiresupply.com", "text"),
    ("company_colour", "Colour of the top bar", "", "text"),
    ("site_url", "Site address (links in emails and phone set-up)", "https://erp.slickstiresupply.com", "text"),
    ("mail_sender_name", "Email sender name", "Slick's Tire Supply", "text"),
    ("job_actor", "Fallback actor for scheduled jobs (audit \"who\")", "", "text"),
    ("currency_code", "Currency", "PHP", "choice"),
    ("timezone", "Time zone", "Asia/Manila", "choice"),
    ("date_format", "Date format", "mdy", "choice"),
    ("fy_start_month", "Financial year starts", "1", "month"),
    ("num_po", "Purchase order", "YYYY - PO NNNN", "fmt"),
    ("num_ps", "Payment statement", "YYYY - PS NNNN", "fmt"),
    ("num_qt", "Quotation", "QT-NNNN", "fmt"),
    ("num_cr", "Collection receipt", "CR-NNNN", "fmt"),
    ("num_exp", "Expense", "EXP-NNNN", "fmt"),
    ("num_ct", "Stock count", "CT-NNNN", "fmt"),
]
DEFAULTS = {k: d for k, _l, d, _t in FIELDS}
LABELS = {k: lbl for k, lbl, _d, _t in FIELDS}
VAT_CHOICES = [("vat", "VAT-registered (12%)"), ("nonvat", "Non-VAT (percentage tax)")]
CURRENCIES = {"PHP": ("₱", "Philippine peso"), "USD": ("$", "US dollar")}
TIMEZONES = ["Asia/Manila"]
DATE_FORMATS = {"mdy": ("mm/dd/yyyy", "%m/%d/%Y"), "dmy": ("dd/mm/yyyy", "%d/%m/%Y"), "ymd": ("yyyy-mm-dd", "%Y-%m-%d")}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
NUMBER_KINDS = {"num_po": "PO", "num_ps": "PS", "num_qt": "QT", "num_cr": "CR", "num_exp": "EXP", "num_ct": "CT"}
LOGO_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def profile() -> dict:
    """Every field, the saved value or Slick's default. Read from the settings table (cached until the database changes)."""
    from engines.settings_store import get_setting
    out = {}
    for k, _l, d, _t in FIELDS:
        try:
            v = get_setting(k, None)
        except Exception:                               # noqa: BLE001 -- a database without the settings table (old test fixtures): the defaults
            v = None
        out[k] = str(v) if v not in (None, "") else d
    out["currency_symbol"] = CURRENCIES.get(out["currency_code"], CURRENCIES["PHP"])[0]
    out["date_label"] = DATE_FORMATS.get(out["date_format"], DATE_FORMATS["mdy"])[0]
    out["site_host"] = re.sub(r"^https?://", "", out["site_url"]).strip("/")
    out["has_logo"] = logo_path() is not None
    out["vat_label"] = dict(VAT_CHOICES).get(out["company_vat"], "")
    return out


def name() -> str:
    return profile()["company_name"]


def short_name() -> str:
    return profile()["company_short"]


def site() -> str:
    return profile()["site_url"].rstrip("/")


def symbol() -> str:
    return profile()["currency_symbol"]


def money(amount) -> str:
    """₱1,234.50 in the company's currency; the one formatter for text and PDFs."""
    try:
        return f"{symbol()}{float(amount):,.2f}"
    except (TypeError, ValueError):
        return f"{symbol()}0.00"


def fmt_date(d, with_time: bool = False) -> str:
    """A date in the company's date format."""
    f = DATE_FORMATS.get(profile()["date_format"], DATE_FORMATS["mdy"])[1]
    if isinstance(d, datetime) and with_time:
        return d.strftime(f + " %H:%M")
    return d.strftime(f)


def header_lines() -> list[str]:
    """The printout header under the name: business line, TIN and VAT status, address, phone / email. Only what is filled in."""
    p = profile()
    out = []
    if p["company_line"]:
        out.append(p["company_line"])
    tin = " · ".join(x for x in (f"TIN {p['company_tin']}" if p["company_tin"] else "", p["vat_label"] if p["company_tin"] else "") if x)
    if tin:
        out.append(tin)
    if p["company_address"]:
        out.append(p["company_address"])
    contact = " · ".join(x for x in (p["company_phone"], p["company_email"]) if x)
    if contact:
        out.append(contact)
    return out


# ---- document numbers ---------------------------------------------------------------------------------------------------------------------------

def _fmt(kind_key: str) -> str:
    f = profile().get(kind_key) or DEFAULTS[kind_key]
    return f if valid_format(f) else DEFAULTS[kind_key]


def valid_format(f: str) -> bool:
    return bool(re.search(r"N{2,8}", f or "")) and len(f) <= 30 and "\n" not in f


def has_year(kind_key: str) -> bool:
    return "YY" in _fmt(kind_key)


def number_prefix(kind_key: str, year: int | None = None) -> str:
    """What every number of this kind starts with this year (to find the numbers already given): the format up to its N run, years filled in."""
    f = _fmt(kind_key)
    y = year or manila_today().year
    head = f[:re.search(r"N{2,8}", f).start()]
    return head.replace("YYYY", str(y)).replace("YY", f"{y % 100:02d}")


def number(kind_key: str, n: int, year: int | None = None) -> str:
    """The number for the n-th document of this kind: 'PS-0041', '2026 - PO 0012'."""
    f = _fmt(kind_key)
    y = year or manila_today().year
    m = re.search(r"N{2,8}", f)
    return (f[:m.start()] + f"{int(n):0{len(m.group(0))}d}" + f[m.end():]).replace("YYYY", str(y)).replace("YY", f"{y % 100:02d}")


def seq_of(kind_key: str, text: str, year: int | None = None) -> int | None:
    """The running number inside an existing document number of this kind (None when it is not in this format)."""
    f = _fmt(kind_key)
    m = re.search(r"N{2,8}", f)
    head, tail = number_prefix(kind_key, year), f[m.end():]
    s = str(text or "")
    if not s.startswith(head) or not s.endswith(tail):
        return None
    mid = s[len(head):len(s) - len(tail)] if tail else s[len(head):]
    return int(mid) if mid.strip().isdigit() else None


# ---- logo ---------------------------------------------------------------------------------------------------------------------------------------

def _logo_dir() -> Path:
    import engines.db_engine as d
    return Path(d.DB_PATH).parent


def logo_path() -> Path | None:
    for ext in LOGO_TYPES:
        p = _logo_dir() / f"company_logo{ext}"
        if p.is_file():
            return p
    return None


def save_logo(filename: str, data: bytes) -> tuple[bool, str]:
    from engines.permissions import require
    ok, why = require("settings.edit")
    if not ok:
        return False, why
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in LOGO_TYPES:
        return False, "The logo must be a PNG, JPG or WEBP picture."
    if not data or len(data) > 2_000_000:
        return False, "The logo must be under 2 MB."
    try:
        from PIL import Image
        import io
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
    except Exception:                                   # noqa: BLE001
        return False, "That file is not a picture."
    old = logo_path()
    if old:
        old.unlink(missing_ok=True)
    (_logo_dir() / f"company_logo{ext}").write_bytes(data)
    _audit("COMPANY_LOGO_SAVED", f"logo replaced ({filename})")
    return True, "Logo saved."


def remove_logo() -> tuple[bool, str]:
    from engines.permissions import require
    ok, why = require("settings.edit")
    if not ok:
        return False, why
    p = logo_path()
    if p:
        p.unlink(missing_ok=True)
        _audit("COMPANY_LOGO_REMOVED", "logo removed: the built-in one shows")
    return True, "The built-in logo shows again."


# ---- saving ---------------------------------------------------------------------------------------------------------------------------------------

def _audit(action: str, text: str) -> None:
    from engines.common import write_tx
    with write_tx() as conn:
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), ?, 'settings', 0, ?)", (action, text))


def save(values: dict) -> tuple[bool, str]:
    """Settings > Company saved: every field checked, only the changed ones written, old -> new in the audit log."""
    from engines.permissions import require
    from engines.settings_store import set_setting
    ok, why = require("settings.edit")
    if not ok:
        return False, why
    before = profile()
    new = {}
    for k, label, default, kind in FIELDS:
        v = " ".join(str(values.get(k, before[k]) if values.get(k) is not None else before[k]).split()) if kind != "text" else str(values.get(k, before[k]) if values.get(k) is not None else before[k]).strip()
        if kind == "text" and len(v) > 300:
            return False, f"{label}: too long."
        if kind == "choice":
            allowed = {"company_vat": dict(VAT_CHOICES), "currency_code": CURRENCIES, "timezone": {t: t for t in TIMEZONES}, "date_format": DATE_FORMATS}[k]
            if v not in allowed:
                return False, f"{label}: pick one of the choices."
        if kind == "month" and (not v.isdigit() or not 1 <= int(v) <= 12):
            return False, f"{label}: pick a month."
        if kind == "fmt" and not valid_format(v):
            return False, f"{label}: the format needs a run of N for the number (e.g. PS-NNNN), and may have YYYY for the year."
        new[k] = v
    if not new["company_name"]:
        return False, "Type the business name."
    if not new["company_short"]:
        new["company_short"] = new["company_name"]
    if new["site_url"] and not re.match(r"^https?://[^\s/]+", new["site_url"]):
        return False, "The site address starts with https://, e.g. https://erp.example.com."
    if new["company_email"] and ("@" not in new["company_email"] or " " in new["company_email"]):
        return False, "The public email does not look like an address."
    if new["job_actor"] and ("@" not in new["job_actor"]):
        return False, "The fallback actor is an email address of a person in Users (or blank)."
    changed = [k for k in new if new[k] != before[k]]
    if not changed:
        return True, "No change."
    for k in changed:
        set_setting(k, new[k], CATEGORY, LABELS[k])
    _audit("COMPANY_PROFILE_SAVED", "; ".join(f"{LABELS[k]}: '{before[k]}' -> '{new[k]}'" for k in changed)[:1500])
    return True, f"Saved: {', '.join(LABELS[k].split(' (')[0] for k in changed)}."
