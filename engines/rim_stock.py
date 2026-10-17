"""Slicks Inventory and the quick quotation (owner, 2026-10-17; mock-ups docs/mockups/an_slicks_inventory.html and ao_quick_quotation.html, answers all A).

Tires by rim, the way the stock notebook is tabbed (12, 13, 14 ... 24): under each rim every size, under each size every brand with how many are on
hand. The rim and the order come from the tire's size, so nothing has to be tagged. The quote takes one size, lists what is on hand at the cost the next
sale takes (FIFO), and the phone adds the markup; nothing is saved."""
from __future__ import annotations

import math
import re

from engines.common import get_conn

RIMS = ["12", "13", "14", "15", "16", "17", "17.5", "18", "19", "19.5", "20", "22.5", "24"]     # the notebook's tabs, in its order

QUOTE_FOOTER_KEY = "quote_footer"
QUOTE_FOOTER_DEFAULT = "Mounting and balancing included? Ask us."


def _num(text: str) -> str:
    """'16.0' -> '16', '17.5' stays."""
    try:
        f = float(text)
    except ValueError:
        return text
    return f"{f:g}"


def rim_of(size: str) -> str:
    """The rim of a tire size: 205/55R16 -> 16, 7.50-16 -> 16, 9.5R17.5 -> 17.5, 31x10.5R15 -> 15, 12x24 -> 24, 8-18 -> 18. '' when unknown."""
    s = str(size or "").upper().replace(" ", "")
    m = re.search(r"Z?R(\d{1,2}(?:\.\d)?)(?!\d)", s)
    if m:
        return _num(m.group(1))
    m = re.search(r"\d(?:\.\d+)?-(\d{1,2}(?:\.\d)?)(?!\d)", s) or re.search(r"X(\d{1,2}(?:\.\d)?)$", s)
    return _num(m.group(1)) if m else ""


def size_order(size: str) -> tuple:
    """The notebook's order inside a tab: passenger and SUV metric sizes by width then aspect, then the light-truck sizes (185R14), then the
    flotation sizes (31x10.5), then the truck sizes (7.50-16) by section -- bias (nylon) before radial of the same size."""
    s = str(size or "").upper().replace(" ", "")
    m = re.match(r"(\d{3})/(\d{2})", s)
    if m:
        return (0, int(m.group(1)), int(m.group(2)), s)
    m = re.match(r"(\d{3})R", s)
    if m:
        return (1, int(m.group(1)), 0, s)
    m = re.match(r"(\d{2})X(\d+(?:\.\d+)?)", s)
    if m:
        return (2, int(m.group(1)), float(m.group(2)), s)
    m = re.match(r"(\d{1,2}(?:\.\d+)?)", s)
    if m:
        return (3, float(m.group(1)), 0 if "-" in s else 1, s)
    return (4, 0, 0, s)


def _line_name(brand: str, name: str, size: str) -> str:
    """The brand and what follows the size in the item's name ('Westlake 205/55R16 SA37' -> 'Westlake SA37')."""
    rest = str(name or "")
    if brand and rest.lower().startswith(brand.lower()):
        rest = rest[len(brand):]
    rest = rest.replace(str(size or ""), " ", 1)
    return " ".join(f"{brand} {rest}".split())


def _tires():
    with get_conn() as conn:
        return conn.execute("""SELECT id, brand, size_model, name, on_hand, COALESCE(NULLIF(next_cost, 0), avg_cost, 0) FROM items
                               WHERE is_active = 1 AND category = 'tire' AND COALESCE(size_model, '') != ''""").fetchall()


def tabs() -> list[str]:
    """The rim tabs: the notebook's, then any other rim a tire has (e.g. 10), in number order."""
    seen = {rim_of(r[2]) for r in _tires()} - {""}
    extra = sorted(seen - set(RIMS), key=lambda x: float(x))
    return RIMS + extra


def _group(rows, with_cost: bool) -> list[dict]:
    sizes: dict[str, dict] = {}
    for iid, brand, size, name, on_hand, cost in rows:
        g = sizes.setdefault(size, {"size": size, "total": 0.0, "lines": []})
        q = float(on_hand or 0)
        g["total"] += max(q, 0)
        line = {"id": int(iid), "name": _line_name(brand or "", name, size), "qty": q}
        if with_cost:
            line["cost"] = round(float(cost or 0), 2)
        g["lines"].append(line)
    out = sorted(sizes.values(), key=lambda g: size_order(g["size"]))
    for g in out:
        g["lines"].sort(key=lambda ln: (ln["qty"] <= 0, ln["name"].lower()))      # on hand first, then the brands with none (grey)
    return out


def rim_tab(rim: str, with_cost: bool = False) -> list[dict]:
    """One tab: [{'size', 'total', 'lines': [{'id', 'name', 'qty'(, 'cost')}]}] in the notebook's order."""
    return _group([r for r in _tires() if rim_of(r[2]) == str(rim)], with_cost)


def find(query: str, with_cost: bool = False) -> list[dict]:
    """Sizes across every rim matching the words typed ('2055516', 'westlake 16', '7.50'), each with its rim."""
    words = [w for w in str(query or "").lower().split() if w]
    if not words:
        return []
    hits = []
    for r in _tires():
        text = f"{r[3]} {r[2]}".lower()
        flat = re.sub(r"[^a-z0-9]", "", text)
        digits = re.sub(r"\D", "", str(r[2]))
        if all(w in text or re.sub(r"[^a-z0-9]", "", w) in flat or (w.isdigit() and w in digits) for w in words):
            hits.append(r)
    out = _group(hits, with_cost)
    for g in out:
        g["rim"] = rim_of(g["size"])
    return out


def quote_lines(size: str) -> list[dict]:
    """What a quote for one size offers: the brands on hand, cheapest first, at the cost the next sale takes. [{'id', 'name', 'qty', 'cost'}]."""
    rows = [r for r in _tires() if r[2] == size and float(r[4] or 0) > 0]
    lines = _group(rows, True)
    out = lines[0]["lines"] if lines else []
    return sorted(out, key=lambda ln: (ln["cost"] <= 0, ln["cost"], ln["name"].lower()))


def round_up(amount: float, step: int) -> float:
    """Prices round up to the next step (owner, 2026-10-17: answer 2 A, ₱50). Step 0 = exactly."""
    if step <= 0:
        return round(amount, 2)
    return float(math.ceil(round(amount, 2) / step - 1e-9) * step)


def quote_footer() -> str:
    from engines.settings_store import get_setting
    v = get_setting(QUOTE_FOOTER_KEY, None)
    return QUOTE_FOOTER_DEFAULT if v is None else str(v)


def save_quote_footer(text: str) -> tuple[bool, str]:
    """The line at the bottom of the customer's view (owner, 2026-10-17: answer 4 A, editable). Blank = no line. System admin only, audited."""
    from engines import settings_store
    from engines.permissions import require
    from engines.settings_registry import DB_PATH, _audit, _write
    ok, why = require("settings.edit")
    if not ok:
        return False, why
    text = " ".join(str(text or "").split())[:160]
    settings_store._CACHE.clear()
    with get_conn(DB_PATH) as conn:
        _write(conn, QUOTE_FOOTER_KEY, text, "general", "The line under a quote shown to a walk-in customer")
        _audit(conn, "SETTING_CHANGED", QUOTE_FOOTER_KEY, f"Quote footer: {text or '(none)'}")
    return True, "Saved." if text else "Removed: the customer's view has no bottom line."
