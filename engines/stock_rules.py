"""Items and stock (the revenue cycle, stage A; owner, 2026-10-11). The stock ledger (engines/stock_ledger.py) is the record: every movement is a row,
never edited. This module reads it for the screens and holds the few rules around it:

  * stock_list / summary / item / ledger   -- what is on hand, at what cost, and everything that happened to one item
  * set_reorder_level                      -- the low-stock level, per item (owner: "per item")
  * adjust                                 -- the System admin sets the counted quantity outside a count, with a reason (history is never edited)
  * clean_dot / dot_text / dot_age / note_dot -- tire age: the DOT week and year typed at receiving, a note on the item (no lot accounting)
  * find_by_code / learn_barcode / picker_items -- scanning: our label carries the item's code; a maker's barcode is learned the first time it is scanned

Controls flag, they do not block: stock may go below zero (a sale is never refused); it shows red and goes on the Review list."""
from __future__ import annotations

import re
from datetime import date

from engines.clock import manila_today
from engines.common import get_conn, write_tx
from engines.permissions import require as require_permission

CATEGORY_LABEL = {"tire": "Tires", "battery": "Battery", "mags": "Mags", "rims": "Rims"}
OLD_YEARS_KEY = "stock_old_years"
_WHAT = {"IN": "Received", "OUT": "Out", "ADJ": "Adjusted", "RETURN": "Returned", "COST": "Cost corrected"}


# --- tire age (DOT) ---------------------------------------------------------------------------------------------------------------------------------

def clean_dot(text) -> str | None:
    """The DOT week and year as stored, 'WWYY': '2425', '24/25', '24-2025' and '24 2025' all give '2425'. None when it is not a week (1-53) and a year."""
    digits = re.sub(r"\D", "", str(text or ""))
    if len(digits) == 6:                                         # WWYYYY
        digits = digits[:2] + digits[4:]
    if len(digits) != 4 or not 1 <= int(digits[:2]) <= 53:
        return None
    return digits


def dot_text(dot) -> str:
    """'2425' -> '24/2025' (week 24 of 2025); '' when there is none."""
    d = clean_dot(dot)
    return f"{d[:2]}/20{d[2:]}" if d else ""


def dot_age(dot, today: date | None = None) -> float | None:
    """Years since the tire was made, from its DOT week and year."""
    d = clean_dot(dot)
    if not d:
        return None
    made = date(2000 + int(d[2:]), 1, 1).toordinal() + (int(d[:2]) - 1) * 7
    return max(((today or manila_today()).toordinal() - made) / 365.25, 0.0)


def _older(a, b) -> bool:
    """Is DOT a older than DOT b?"""
    return (a[2:], a[:2]) < (b[2:], b[:2])


def note_dot(conn, item_id, dot) -> None:
    """Tires arrived with this DOT: the item keeps its OLDEST one, the one to sell first."""
    d = clean_dot(dot)
    if not d or not item_id:
        return
    have = clean_dot((conn.execute("SELECT oldest_dot FROM items WHERE id = ?", (int(item_id),)).fetchone() or [None])[0])
    if not have or _older(d, have):
        conn.execute("UPDATE items SET oldest_dot = ? WHERE id = ?", (d, int(item_id)))


def old_years() -> float:
    from engines.settings_registry import value
    return float(value(OLD_YEARS_KEY))


# --- what is on hand ----------------------------------------------------------------------------------------------------------------------------------

def stock_list(search: str = "", show: str = "") -> list[dict]:
    """Every active item with what is on hand: id, code, name, category, on_hand, avg_cost, value, reorder_level, last_in, last_out, dot (text), age,
    last_counted_at, low, negative, old. `search`: any words in any order (code, name); `show`: a category code, or 'low', 'negative', 'old', 'uncounted'."""
    words = [w for w in " ".join(str(search or "").lower().split()).split(" ") if w]
    today, limit = manila_today(), old_years()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT i.id, i.code, i.name, i.category, i.on_hand, i.avg_cost, i.reorder_level, i.oldest_dot, i.last_counted_at,
                      (SELECT MAX(moved_on) FROM stock_ledger s WHERE s.item_id = i.id AND s.qty > 0),
                      (SELECT MAX(moved_on) FROM stock_ledger s WHERE s.item_id = i.id AND s.qty < 0)
               FROM items i WHERE i.is_active = 1 ORDER BY i.category, i.name COLLATE NOCASE""").fetchall()
    out = []
    for iid, code, name, cat, on_hand, cost, low_at, dot, counted, last_in, last_out in rows:
        text = f"{code} {name}".lower()
        if any(w not in text and w not in re.sub(r"[^a-z0-9]", "", text) and w not in re.sub(r"\D", "", name) for w in words):     # '2055516' finds 205/55R16
            continue
        on_hand, cost = float(on_hand or 0), float(cost or 0)
        age = dot_age(dot, today) if on_hand > 0 else None
        days = (today - date.fromisoformat(str(counted)[:10])).days if counted else None
        r = {"id": int(iid), "code": code, "name": name, "category": cat, "on_hand": on_hand, "avg_cost": cost, "value": round(on_hand * cost, 2),
             "reorder_level": int(low_at or 0), "last_in": last_in, "last_out": last_out, "dot": dot_text(dot) if on_hand > 0 else "", "age": age,
             "last_counted_at": counted, "low": 0 <= on_hand <= int(low_at or 0) and bool(last_in or last_out), "negative": on_hand < 0, "old": age is not None and age >= limit,
             "uncounted": days is None or days > 90}
        if show in CATEGORY_LABEL and cat != show:
            continue
        if show in ("low", "negative", "old", "uncounted") and not r[show]:
            continue
        out.append(r)
    return out


def summary(rows: list[dict] | None = None) -> dict:
    rows = stock_list() if rows is None else rows
    return {"items": len(rows), "pieces": sum(r["on_hand"] for r in rows if r["on_hand"] > 0), "value": round(sum(r["value"] for r in rows), 2),
            "low": sum(1 for r in rows if r["low"]), "negative": sum(1 for r in rows if r["negative"]),
            "old_pcs": sum(r["on_hand"] for r in rows if r["old"]), "uncounted": sum(1 for r in rows if r["uncounted"])}


def item(item_id: int) -> dict | None:
    for r in stock_list():
        if r["id"] == int(item_id):
            return r
    with get_conn() as conn:                                     # switched off, but its history is still there
        row = conn.execute("SELECT id, code, name, category, on_hand, avg_cost, reorder_level, oldest_dot, last_counted_at FROM items WHERE id = ?", (int(item_id),)).fetchone()
    if not row:
        return None
    return {"id": row[0], "code": row[1], "name": row[2], "category": row[3], "on_hand": float(row[4] or 0), "avg_cost": float(row[5] or 0),
            "value": round(float(row[4] or 0) * float(row[5] or 0), 2), "reorder_level": int(row[6] or 0), "dot": dot_text(row[7]), "age": dot_age(row[7]),
            "last_counted_at": row[8], "low": False, "negative": float(row[4] or 0) < 0, "old": False, "uncounted": True, "last_in": None, "last_out": None}


def _document(conn, ref_type, ref_id) -> tuple[str, str]:
    """(label, url) of the document behind a stock movement."""
    if not ref_type or ref_id is None:
        return "", ""
    if ref_type == "purchase_order_lines":
        row = conn.execute("SELECT p.po_id, p.po_no, c.creditor_name FROM purchase_order_lines l JOIN purchase_orders p ON p.po_id = l.po_id JOIN creditors c ON c.creditor_id = p.creditor_id WHERE l.line_id = ?",
                           (ref_id,)).fetchone()
        return (f"{row[1]} · {row[2]}", f"/purchase-orders/{row[0]}/") if row else ("a purchase order", "")
    if ref_type == "sales":
        row = conn.execute("SELECT s.si_no, s.release_no, COALESCE(c.customer_name, NULLIF(s.customer_name, ''), 'Walk-in') FROM sales s LEFT JOIN customers c ON c.customer_id = s.customer_id WHERE s.sale_id = ?",
                           (ref_id,)).fetchone()
        return (f"{'SI ' + f'{row[0]:06d}' if row[0] else row[1]} · {row[2]}", f"/sales/{ref_id}/") if row else ("a sale", "")
    if ref_type == "affiliate_moves":
        row = conn.execute("SELECT m.move_no, c.customer_name FROM affiliate_moves m JOIN customers c ON c.customer_id = m.customer_id WHERE m.move_id = ?", (ref_id,)).fetchone()
        return (f"{row[0]} · {row[1]}", "/affiliates/") if row else ("an affiliate", "")
    if ref_type == "stock_counts":
        row = conn.execute("SELECT count_no FROM stock_counts WHERE count_id = ?", (ref_id,)).fetchone()
        return (row[0], f"/stock-counts/{ref_id}/") if row else ("a count", "")
    if ref_type == "take_in":                                   # a trade-in or surplus lot taken in on the phone (2026-10-12)
        return ("taken in", f"/stock/{ref_id}/")
    return "", ""


def ledger(item_id: int, limit: int = 200) -> list[dict]:
    """Everything that happened to the item, newest first: date, what, document, url, who, qty_in, qty_out, cost, after, note."""
    with get_conn() as conn:
        rows = conn.execute("""SELECT move_id, kind, qty, unit_cost, on_hand_after, ref_type, ref_id, moved_on, note, created_by FROM stock_ledger
                               WHERE item_id = ? ORDER BY move_id DESC LIMIT ?""", (int(item_id), int(limit))).fetchall()
        out = []
        for mid, kind, qty, cost, after, ref_type, ref_id, day, note, who in rows:
            doc, url = _document(conn, ref_type, ref_id)
            what = _WHAT.get(kind, kind)
            if ref_type == "sales":
                what = "Sold" if float(qty) < 0 else "Returned by a customer"
            elif ref_type == "affiliate_moves":
                what = ("Lent to an affiliate" if float(qty) < 0 else "Came from an affiliate")
            elif ref_type == "stock_counts":
                what = "Counted"
            out.append({"date": day, "what": what, "document": doc, "url": url, "who": who or "", "qty_in": float(qty) if float(qty) > 0 else None,
                        "qty_out": -float(qty) if float(qty) < 0 else None, "cost": float(cost or 0), "after": float(after or 0), "note": note or ""})
    return out


# --- the two things typed on an item ----------------------------------------------------------------------------------------------------------------

def set_reorder_level(item_id: int, level) -> tuple[bool, str]:
    """The low-stock level of ONE item (owner, 2026-10-11: "per item"): at or below it the item shows Low."""
    allowed, why = require_permission("inventory.use")
    if not allowed:
        return False, why
    try:
        n = int(float(str(level).replace(",", "")))
    except (TypeError, ValueError):
        return False, "Type a whole number for the low-stock level."
    if n < 0 or n > 9999:
        return False, "The low-stock level must be between 0 and 9,999."
    with write_tx() as conn:
        row = conn.execute("SELECT name, reorder_level FROM items WHERE id = ?", (int(item_id),)).fetchone()
        if not row:
            return False, "Item not found."
        conn.execute("UPDATE items SET reorder_level = ? WHERE id = ?", (n, int(item_id)))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, old_values, new_values) VALUES (acting_user(), 'ITEM_LOW_LEVEL', 'items', ?, ?, ?)",
                     (int(item_id), str(row[1]), f"{row[0]}: low at {n}"))
    return True, f"{row[0]} now shows Low at {n} or fewer."


def adjust(item_id: int, counted, reason: str) -> tuple[bool, str]:
    """The System admin sets what is really on hand, outside a count, with a reason. The difference is a new ledger row; nothing before it changes."""
    allowed, why = require_permission("inventory.approve")
    if not allowed:
        return False, why
    reason = " ".join(str(reason or "").split())
    if not reason:
        return False, "Say why the quantity is corrected (e.g. 'found damaged', 'typed twice')."
    try:
        qty = float(str(counted).replace(",", ""))
    except (TypeError, ValueError):
        return False, "Type the quantity that is really on hand."
    from engines.review_flags import flag
    from engines.stock_ledger import stock_set
    with write_tx() as conn:
        row = conn.execute("SELECT name, on_hand FROM items WHERE id = ?", (int(item_id),)).fetchone()
        if not row:
            return False, "Item not found."
        diff = qty - float(row[1] or 0)
        if abs(diff) < 1e-9:
            return False, f"{row[0]} already shows {qty:g}."
        stock_set(conn, int(item_id), qty, "adjustment", int(item_id), note=f"adjusted from {float(row[1] or 0):g} to {qty:g}: {reason}")
        note = f"{row[0]}: {float(row[1] or 0):g} → {qty:g} ({diff:+g}). Why: {reason}"
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'STOCK_ADJUSTED', 'items', ?, ?)", (int(item_id), note))
        flag(conn, "STOCK_ADJUSTED", "items", int(item_id), note)
    return True, f"{row[0]}: on hand is now {qty:g} ({diff:+g})."


# --- scanning -----------------------------------------------------------------------------------------------------------------------------------------

def find_by_code(text) -> dict | None:
    """What a scan (or a typed code) means: one of our item codes (the label), or a maker's barcode learned earlier. {'id', 'code', 'name'} or None."""
    code = " ".join(str(text or "").split())
    if not code:
        return None
    with get_conn() as conn:
        row = (conn.execute("SELECT id, code, name FROM items WHERE UPPER(code) = UPPER(?)", (code,)).fetchone()
               or conn.execute("SELECT i.id, i.code, i.name FROM item_barcodes b JOIN items i ON i.id = b.item_id WHERE b.barcode = ?", (code,)).fetchone())
    return {"id": int(row[0]), "code": row[1], "name": row[2]} if row else None


def learn_barcode(barcode, item_id: int) -> tuple[bool, str]:
    """This maker's barcode is that item, from now on (asked once, the first time it is scanned)."""
    allowed = require_permission("inventory.use")[0] or require_permission("sales.use")[0] or require_permission("po.receive_goods")[0]
    if not allowed:
        return False, "Scanning is switched off for you."
    code = " ".join(str(barcode or "").split())
    if len(code) < 4 or len(code) > 64:
        return False, "That does not look like a barcode."
    with write_tx() as conn:
        it = conn.execute("SELECT name FROM items WHERE id = ?", (int(item_id),)).fetchone()
        if not it:
            return False, "Item not found."
        if conn.execute("SELECT 1 FROM items WHERE UPPER(code) = UPPER(?)", (code,)).fetchone():
            return False, "That is one of our own item codes."
        had = conn.execute("SELECT i.name FROM item_barcodes b JOIN items i ON i.id = b.item_id WHERE b.barcode = ?", (code,)).fetchone()
        if had:
            return (True, f"{code} is already {had[0]}.") if had[0] == it[0] else (False, f"{code} already means {had[0]}. An item is switched off, never re-used.")
        conn.execute("INSERT INTO item_barcodes (barcode, item_id, created_by) VALUES (?, ?, acting_user())", (code, int(item_id)))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'BARCODE_LEARNED', 'items', ?, ?)",
                     (int(item_id), f"{code} = {it[0]}"))
    return True, f"From now on, {code} is {it[0]}."


def picker_items() -> list[dict]:
    """Every active item for the scan-and-search box: id, code, name, on_hand, cost, dot, find (lower-case words, plus the digits run together so
    '2055516' finds 205/55R16) and codes (our code and the makers' barcodes: an exact match adds the item at once)."""
    with get_conn() as conn:
        rows = conn.execute("SELECT id, code, name, on_hand, avg_cost, oldest_dot, unit FROM items WHERE is_active = 1 ORDER BY name COLLATE NOCASE").fetchall()
        codes: dict[int, list[str]] = {}
        for b, iid in conn.execute("SELECT barcode, item_id FROM item_barcodes"):
            codes.setdefault(int(iid), []).append(b)
    out = []
    for iid, code, name, on_hand, cost, dot, unit in rows:
        text = f"{code} {name}".lower()
        out.append({"id": int(iid), "code": code, "name": name, "on_hand": float(on_hand or 0), "cost": float(cost or 0), "dot": dot_text(dot) if float(on_hand or 0) > 0 else "",
                    "unit": unit or "", "find": text + " " + re.sub(r"[^a-z0-9]", "", text) + " " + re.sub(r"\D", "", name),
                    "codes": [code.upper()] + codes.get(int(iid), [])})
    return out


# ---- goods that did not come from a supplier (owner, 2026-10-12: surplus and trade-in tires, "catchall name surplus tire, rim, estimate cost") ----

SURPLUS = (("tire", "Surplus tire"), ("rims", "Surplus rim"))


def surplus_items() -> list[dict]:
    """The two catch-all items, made the first time they are asked for: [{'id', 'code', 'name'}]."""
    from engines.product_catalog import get_or_create_product
    out = []
    for category, name in SURPLUS:
        with get_conn() as conn:
            row = conn.execute("SELECT id, code, name FROM items WHERE lower(name) = ? AND category = ?", (name.lower(), category)).fetchone()
        if not row:
            iid, full, _ = get_or_create_product("Tires" if category == "tire" else "Rims", "Surplus", size_model=name.split(" ", 1)[1])
            with write_tx() as conn:
                conn.execute("UPDATE items SET name = ? WHERE id = ?", (name, int(iid)))
                row = conn.execute("SELECT id, code, name FROM items WHERE id = ?", (int(iid),)).fetchone()
        out.append({"id": int(row[0]), "code": row[1], "name": row[2]})
    return out


def take_in(item_id: int, qty, cost, note: str) -> tuple[bool, str]:
    """Pieces that came without a supplier's delivery -- a trade-in, a surplus lot, a tire found -- taken into stock at an estimated cost (0 when
    free), with a note of where they came from. The phone's Stock page does this; the ledger and the audit show it."""
    allowed, why = require_permission("inventory.use")
    if not allowed:
        return False, why
    try:
        n = float(str(qty).replace(",", ""))
    except (TypeError, ValueError):
        return False, "Type how many came in."
    if n <= 0:
        return False, "Type how many came in."
    try:
        c = float(str(cost or 0).replace(",", "") or 0)
    except (TypeError, ValueError):
        return False, "Type the estimated cost each (0 if free)."
    if c < 0:
        return False, "The cost cannot be below zero."
    note = " ".join(str(note or "").split())
    if not note:
        return False, "Say where they came from (e.g. 'trade-in from RVC', 'surplus from Jun')."
    from engines.stock_ledger import stock_in
    with write_tx() as conn:
        row = conn.execute("SELECT name FROM items WHERE id = ? AND is_active = 1", (int(item_id),)).fetchone()
        if not row:
            return False, "Item not found."
        stock_in(conn, int(item_id), n, c, "take_in", int(item_id), note=f"taken in: {note}")
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'STOCK_TAKEN_IN', 'items', ?, ?)",
                     (int(item_id), f"{n:g} x {row[0]} taken in at {c:,.2f} each: {note}"))
    return True, f"{n:g} × {row[0]} taken in at ₱{c:,.2f} each."
