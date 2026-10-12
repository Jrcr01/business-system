"""Template filters every page has without a {% load %} (registered as builtins in config/settings.py)."""
from __future__ import annotations

from datetime import date, datetime

from django import template

register = template.Library()


import re as _re

_ISO = _re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_WORDS = _re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? (\d{1,2}), (\d{4})\b")


@register.filter
def usdates(text) -> str:
    """Dates written inside free text -- stored notes, the audit log, review details, messages written before 2026-10-09 -- shown mm/dd/yyyy:
    '2026-10-09' and 'Oct 09, 2026' both become '10/09/2026'. The stored text itself is never changed."""
    if text in (None, ""):
        return ""
    s = _ISO.sub(lambda m: f"{m.group(2)}/{m.group(3)}/{m.group(1)}", str(text))
    return _WORDS.sub(lambda m: f"{_MONTHS[m.group(1).lower()]:02d}/{int(m.group(2)):02d}/{m.group(3)}", s)


@register.filter
def dmy(value) -> str:
    """A date shown mm/dd/yyyy (owner, 2026-10-09: one date format across the system, mm/dd/yyyy); with its time (mm/dd/yyyy HH:MM) when it has one.
    Takes a date, a datetime or an ISO text ('2026-10-09', '2026-10-09 14:30:00'); anything else is shown as it is; empty shows nothing."""
    if value in (None, "") or (isinstance(value, float) and value != value) or str(value).strip().lower() in ("nan", "nat", "none"):
        return ""                                           # a missing date from a pandas frame reads as NaN: nothing, not "nan" (2026-10-12)
    if isinstance(value, datetime):
        return value.strftime("%m/%d/%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%m/%d/%Y")
    text = str(value).strip()
    try:
        d = date.fromisoformat(text[:10])
    except ValueError:
        return text
    out = d.strftime("%m/%d/%Y")
    rest = text[10:].strip().lstrip("T")
    return out + (" " + rest[:5] if len(rest) >= 5 and rest[2] == ":" else "")


def peso_short(amount) -> str:
    """₱1.2M, ₱600K, ₱850: the short form of a figure for a phone's calendar cell (owner, 2026-10-12: "1M, 1.2M, 600K")."""
    try:
        a = float(amount)
    except (TypeError, ValueError):
        return ""
    sign, a = ("-" if a < 0 else ""), abs(a)
    if a < 1000:
        return f"{sign}₱{a:,.0f}"
    if a < 1_000_000:
        return f"{sign}₱{a / 1000:.0f}K"
    m = f"{a / 1_000_000:.1f}".rstrip("0").rstrip(".")
    return f"{sign}₱{m}M"


@register.filter(name="pw")
def peso_wide_and_short(amount) -> str:
    """The full figure for a laptop and the short one for a phone, side by side; core/slicks.css shows one of them by the screen's width."""
    from django.utils.html import format_html
    from django.contrib.humanize.templatetags.humanize import intcomma
    try:
        full = "₱" + intcomma(f"{float(amount):,.0f}".replace(",", ""))
    except (TypeError, ValueError):
        return ""
    return format_html('<span class="pw"><span class="pw-full">{}</span><span class="pw-short">{}</span></span>', full, peso_short(amount))
