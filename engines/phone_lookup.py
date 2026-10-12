"""What the phone pages look up (owner, 2026-10-12: "We need pricelist, inventory. Stock in, stock out. Those items need mobility without using a
laptop"). Read-only helpers; every save the phone makes goes through the same engines as the laptop (po_rules, sales_rules, stock_rules)."""
from __future__ import annotations

import re
from datetime import date

from engines.clock import manila_today
from engines.common import get_conn


def _words(query: str) -> list[str]:
    return [w for w in " ".join(str(query or "").lower().split()).split(" ") if w]


def _matches(words: list[str], *texts) -> bool:
    """Every word is in the text, or in the text with the punctuation taken out, or in its digits run together ('2055516' finds 205/55R16)."""
    text = " ".join(t for t in texts if t).lower()
    flat, digits = re.sub(r"[^a-z0-9]", "", text), re.sub(r"\D", "", text)
    return all(w in text or w in flat or w in digits for w in words)


def prices(query: str, limit: int = 40) -> list[dict]:
    """The price list on the phone: the items whose name or code matches every word typed, each with every supplier's net price in force today
    (list price less the standing discount), newest first. [{'name', 'code', 'unit', 'suppliers': [{'supplier', 'creditor_id', 'list', 'chain',
    'net', 'since'}]}]. Nothing for fewer than two characters. Prices switched off and discontinued items are left out, as on Price lists."""
    from engines.discount_rules import chain_from, discount_rows, net_price
    words = _words(query)
    if len("".join(words)) < 2:
        return []
    today = str(manila_today())
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT s.creditor_id, c.creditor_name, s.item_name, COALESCE(s.item_code, ''), COALESCE(s.unit, ''), s.unit_price, s.effective_date
               FROM supplier_price_list s JOIN creditors c ON c.creditor_id = s.creditor_id
               WHERE s.is_active = 1 AND s.effective_date <= ? AND (s.item_id IS NULL OR s.item_id IN (SELECT id FROM items WHERE is_active = 1))
               ORDER BY lower(s.item_name), s.creditor_id, s.effective_date DESC, s.price_id DESC""", (today,)).fetchall()
    seen, items, rules = set(), {}, {}
    for cid, supplier, name, code, unit, price, since in rows:
        key = (cid, name.lower())
        if key in seen:                                          # only the newest price per supplier and item
            continue
        seen.add(key)
        if not _matches(words, name, code):
            continue
        if cid not in rules:
            rules[cid] = discount_rows(cid, manila_today())
        chain = chain_from(rules[cid], name)
        it = items.setdefault(name.lower(), {"name": name, "code": code, "unit": unit, "suppliers": []})
        if code and not it["code"]:
            it["code"] = code
        it["suppliers"].append({"supplier": supplier, "creditor_id": int(cid), "list": float(price), "chain": chain, "net": net_price(float(price), chain),
                               "since": date.fromisoformat(str(since)[:10])})
    out = sorted(items.values(), key=lambda x: x["name"].lower())
    for it in out:
        it["suppliers"].sort(key=lambda s: s["net"])
    return out[:limit]


def waiting_orders() -> list[dict]:
    """Orders with goods still to come, every supplier (the phone picks the order, not the supplier): [{'po_id', 'po_no', 'creditor_id', 'supplier',
    'lines', 'pcs', 'ordered_on'}], oldest first."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT p.po_id, p.po_no, p.creditor_id, c.creditor_name, COUNT(*), SUM(l.qty - l.qty_received), substr(p.order_date, 1, 10)
               FROM purchase_orders p JOIN purchase_order_lines l ON l.po_id = p.po_id JOIN creditors c ON c.creditor_id = p.creditor_id
               WHERE p.status IN ('ORDERED', 'PARTIAL') AND l.qty - l.qty_received > 0.0000001
               GROUP BY p.po_id ORDER BY p.order_date, p.po_id""").fetchall()
    return [{"po_id": int(r[0]), "po_no": r[1], "creditor_id": int(r[2]), "supplier": r[3], "lines": int(r[4]), "pcs": round(float(r[5] or 0), 2), "ordered_on": r[6]}
            for r in rows]


def stock_card(item_id: int) -> dict | None:
    """One item for the phone: what stock_rules.item gives, plus the barcodes learned for it. Cost and value are left out on purpose (owner, 2026-10-12:
    the staff hold the phone)."""
    from engines.stock_rules import item
    it = item(item_id)
    if not it:
        return None
    with get_conn() as conn:
        codes = [r[0] for r in conn.execute("SELECT barcode FROM item_barcodes WHERE item_id = ? ORDER BY barcode", (int(item_id),))]
    keep = ("id", "code", "name", "category", "on_hand", "reorder_level", "dot", "age", "last_in", "last_out", "last_counted_at", "low", "negative", "old", "uncounted")
    return {**{k: it.get(k) for k in keep}, "barcodes": codes}


def slips_today() -> list[dict]:
    """The slips made today, newest first: what the phone lists under Stock out with Undo."""
    from engines.sales_rules import open_releases
    today = str(manila_today())
    return [r for r in open_releases() if str(r["day"])[:10] == today]
