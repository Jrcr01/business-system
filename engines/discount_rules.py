"""Supplier discounts on list prices: "less 5%, less 5%" (2026-10-02). A supplier's price list shows LIST prices; the supplier then gives a chain of discounts, each
taken off what is left by the one before: 7,600 less 5% = 7,220, less another 5% = 6,859.00 (not 10% off). The list price is stored as printed; the net price is
worked out from it.

A chain is stored as text "5,5". It is set per supplier and brand (brand left blank = every brand of that supplier), with a start date so a change keeps its
history. A purchase order line starts with the standing chain for its supplier and brand and can be overridden on that line.

  * parse_chain / chain_text / chain_label -- reading what a person types, and showing it
  * net_price                              -- list price less each discount in turn, rounded to the centavo once, at the end
  * set_discount / standing_chain / list_discounts -- the standing chains"""
from __future__ import annotations

import re
from datetime import date

import pandas as pd

from engines.permissions import require as require_permission
from engines.clock import manila_today
from engines.common import get_conn, write_tx
from engines.common import us_date


def parse_chain(text) -> list[float] | None:
    """'5,5' / '5% 5%' / 'less 5, less 5' / '5+5' / [5, 5] -> [5.0, 5.0]. Blank -> [] (no discount). Anything else, or a value below 0 or 100 and above, -> None."""
    if text is None:
        return []
    if isinstance(text, (list, tuple)):
        parts = [str(x) for x in text]
    else:
        raw = re.sub(r"(?i)\bless\b|%|\band\b", " ", str(text))
        if re.search(r"[^\d.,;+/\s-]", raw):
            return None
        parts = [p for p in re.split(r"[,;+/\s]+", raw) if p != ""]
    out = []
    for p in parts:
        try:
            v = float(p)
        except ValueError:
            return None
        if not 0 <= v < 100:
            return None
        out.append(v)
    return out


def chain_text(chain) -> str:
    """The stored form: '5,5' / '7.5,2'. An empty chain is ''."""
    parsed = parse_chain(chain)
    return ",".join(f"{v:g}" for v in (parsed or []))


def chain_label(chain) -> str:
    """For screens and the PDF: 'less 5% + 5%'. No discount: ''."""
    parsed = parse_chain(chain)
    return "less " + " + ".join(f"{v:g}%" for v in parsed) if parsed else ""


def net_price(list_price: float, chain) -> float:
    """List price less each discount in turn, rounded to the centavo once at the end (7,600 less 5% less 5% = 6,859.00). No chain: the list price."""
    net = float(list_price)
    for d in parse_chain(chain) or []:
        net *= 1 - d / 100
    return round(net + 1e-9, 2)


def _audit(conn, action: str, creditor_id: int, text: str) -> None:
    conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), ?, 'supplier_discounts', ?, ?)",
                 (action, creditor_id, text))


def set_discount(creditor_id: int, brand: str, chain, effective_date: date) -> tuple[bool, str]:
    """Saves the standing chain for a supplier's brand ('' = all brands) from a date. A blank chain means 'no discount from this date'.
    The same brand and date again replaces the earlier row; an earlier date's row stays as history."""
    _ok, _msg = require_permission('price.write')
    if not _ok:
        return False, _msg
    parsed = parse_chain(chain)
    if parsed is None:
        return False, "Type the discounts as percentages, like 5, 5 (less 5% and less 5%). Each must be below 100."
    if effective_date is None:
        return False, "Pick the date the discount starts."
    brand = " ".join(str(brand or "").split())
    text = chain_text(parsed)
    with write_tx() as conn:
        row = conn.execute("SELECT discount_id FROM supplier_discounts WHERE creditor_id = ? AND lower(brand) = ? AND effective_date = ?", (creditor_id, brand.lower(), str(effective_date))).fetchone()
        if row:
            conn.execute("UPDATE supplier_discounts SET chain = ?, is_active = 1 WHERE discount_id = ?", (text, row[0]))
        else:
            conn.execute("INSERT INTO supplier_discounts (creditor_id, brand, chain, effective_date) VALUES (?, ?, ?, ?)", (creditor_id, brand, text, str(effective_date)))
        _audit(conn, "DISCOUNT_SET", creditor_id, f"{brand or 'all brands'}: {chain_label(text) or 'no discount'} from {effective_date}")
    return True, f"{brand or 'All brands'}: {chain_label(text) or 'no discount'} from {effective_date:%m/%d/%Y} saved."


def supplier_brands(creditor_id: int) -> list[str]:
    """The brands on this supplier's price list (its items), for the discount's Brand list."""
    with get_conn() as conn:
        return [r[0] for r in conn.execute(
            """SELECT DISTINCT i.brand FROM supplier_price_list s JOIN items i ON i.id = s.item_id WHERE s.creditor_id = ? AND s.is_active = 1 AND i.brand <> ''
               ORDER BY lower(i.brand)""", (creditor_id,))]


def remove_discount(creditor_id: int, discount_id: int) -> tuple[bool, str]:
    """Removes a discount row entered by mistake (switched off, kept for the audit). Order lines already saved keep the discount they were given."""
    _ok, _msg = require_permission("price.write")
    if not _ok:
        return False, _msg
    with write_tx() as conn:
        row = conn.execute("SELECT brand, chain, effective_date FROM supplier_discounts WHERE discount_id = ? AND creditor_id = ? AND is_active = 1",
                           (discount_id, creditor_id)).fetchone()
        if not row:
            return False, "That discount is already gone."
        conn.execute("UPDATE supplier_discounts SET is_active = 0 WHERE discount_id = ?", (discount_id,))
        _audit(conn, "DISCOUNT_REMOVED", creditor_id, f"{row[0] or 'all brands'}: {chain_label(row[1]) or 'no discount'} from {us_date(row[2])} removed")
    return True, f"Removed: {row[0] or 'All brands'}, {chain_label(row[1]) or 'no discount'} from {us_date(row[2])}."


def standing_chain(creditor_id: int, item_name: str, on: date | None = None) -> str:
    """The chain in force for this supplier's item on a date: a brand row (the item name starts with the brand) beats the supplier-wide row; the newest start
    date wins; a chain set to blank means no discount. '' when nothing is set."""
    return chain_from(discount_rows(creditor_id, on), item_name)


def discount_rows(creditor_id: int, on: date | None = None) -> list:
    """This supplier's discount rows in force on a date (read once, then chain_from per item: the item picker prices hundreds of items at once)."""
    with get_conn() as conn:
        return conn.execute("SELECT brand, chain, effective_date, discount_id FROM supplier_discounts WHERE creditor_id = ? AND is_active = 1 AND effective_date <= ?",
                            (creditor_id, str(on or manila_today()))).fetchall()


def chain_from(rows: list, item_name: str) -> str:
    name = " ".join(str(item_name or "").split()).lower()
    best = None
    for brand, chain, eff, did in rows:
        b = (brand or "").lower()
        if b and not (name == b or name.startswith(b + " ")):
            continue
        key = (len(b) > 0, len(b), eff, did)                  # a brand row beats the all-brands row; a longer brand name beats a shorter one; then the newest
        if best is None or key > best[0]:
            best = (key, chain or "")
    return best[1] if best else ""


def list_discounts(creditor_id: int, on: date | None = None) -> pd.DataFrame:
    """The chain in force on a date for each brand of this supplier (newest start date per brand), for the settings table."""
    on = on or manila_today()
    with get_conn() as conn:
        df = pd.read_sql_query("SELECT discount_id, brand, chain, effective_date FROM supplier_discounts WHERE creditor_id = ? AND is_active = 1 AND effective_date <= ? "
                               "ORDER BY lower(brand), effective_date DESC, discount_id DESC", conn, params=(creditor_id, str(on)))
    if df.empty:
        return pd.DataFrame(columns=["discount_id", "brand", "chain", "label", "effective_date"])
    df["_k"] = df["brand"].str.lower()
    out = df.groupby("_k", sort=False).head(1).drop(columns="_k").reset_index(drop=True)
    out["label"] = out["chain"].map(chain_label).replace("", "no discount")
    return out[["discount_id", "brand", "chain", "label", "effective_date"]]


def net_price_on(creditor_id: int, item_name: str, on: date | None = None) -> dict | None:
    """The supplier's list price for the item on a date, the standing chain, and the net: {'list_price', 'chain', 'net_price', ...}. None if it has no list price."""
    from engines.price_list_service import price_on
    listed = price_on(creditor_id, item_name, on)
    if not listed:
        return None
    chain = standing_chain(creditor_id, listed["item_name"], on)
    return {**listed, "list_price": listed["unit_price"], "chain": chain, "net_price": net_price(listed["unit_price"], chain)}
