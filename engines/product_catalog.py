"""The item master (2026-10-02): one row per product in the `items` table, whichever supplier sells it, with OUR OWN item code (TR-0001 / BA- / MG- / RM-).
The same table is the Django ERP's inventory.Item (stock, sales) -- one list for price lists, purchase orders, stock and sales. Several suppliers can sell the same item
at different prices; a supplier price row points at the item (supplier_price_list.item_id), so the prices sit side by side and a purchase order can pick the cheapest.
Each supplier's own code is kept in supplier_items. The standard fields per category are in CATEGORY_FIELDS (one place: the Excel template, its checks, the Items screens
and the item name all read it):

  Tires    size, ply, terrain, pattern, type, tube          "Westlake 285/50R20 (A/T) SL369"
  Battery  model, volts, ah, cca, battery type, terminal    "Motolite NS60 12V 45Ah L"
  Mags     model, size, pcd, offset, bore, finish           "Enkei RPF1 17x7.5 5x114.3 ET40 Silver"
  Rims     size, holes, pcd, offset, tube, thickness, color "Westlake 8.25x22.5 10H Tubeless"

Two items are the same when category, brand, their identity fields and the remark match ignoring case, spaces and hyphens, so a stray space does not split one item in
two. The name keeps the spelling it was first saved with. size_model (the headline: size, or the model for batteries) is a real column; the other fields are in specs.

  * standard_name / product_key / normalise_specs
  * get_or_create_product / list_products / set_product_active / set_supplier_code
  * supplier_prices (side by side)"""
from __future__ import annotations

import json
import re
from datetime import date

import pandas as pd

from engines.clock import manila_today
from engines.common import get_conn, write_tx
from engines.permissions import require as require_permission

TERRAIN = ["A/T", "M/T", "H/T", "R/T"]
TIRE_TYPES = ["PCR", "LT", "TBR", "TBB", "OTR"]
TUBE = ["TL", "TT"]


def _f(key, label, required=False, choices=None, name=False, ident=False, note=""):
    return {"key": key, "label": label, "required": required, "choices": choices, "name": name, "ident": ident, "note": note}


# the order here is the order of the template columns AND of the pieces of the standard name
CATEGORY_FIELDS: dict[str, list[dict]] = {
    "Tires": [_f("size", "Size", True, name=True, ident=True, note="e.g. 285/50R20"), _f("ply", "Ply / load-speed", name=True, ident=True, note="e.g. 10PR, 18PR, 112H"),
              _f("terrain", "Terrain", choices=TERRAIN, name=True, ident=True), _f("pattern", "Pattern", True, name=True, ident=True, note="e.g. SL369"),
              _f("type", "Type", choices=TIRE_TYPES), _f("tube", "Tube", choices=TUBE)],
    "Battery": [_f("model", "Model / group", True, name=True, ident=True, note="e.g. NS60"), _f("volts", "Volts", name=True, note="e.g. 12"),
                _f("ah", "Ah", True, name=True, note="e.g. 45"), _f("cca", "CCA"), _f("battery_type", "Type", choices=["MF", "Conventional"]),
                _f("terminal", "Terminal", choices=["L", "R"], name=True, ident=True)],
    "Mags": [_f("model", "Model", True, name=True, ident=True), _f("size", "Size", True, name=True, ident=True, note="e.g. 17x7.5"),
             _f("pcd", "PCD", True, name=True, ident=True, note="e.g. 5x114.3"), _f("offset", "Offset", True, name=True, ident=True, note="e.g. ET40"),
             _f("bore", "Center bore"), _f("finish", "Finish / color", name=True, ident=True)],
    "Rims": [_f("size", "Size", True, name=True, ident=True, note="e.g. 8.25x22.5"), _f("holes", "Holes", True, name=True, ident=True, note="e.g. 10"),
             _f("pcd", "PCD", ident=True), _f("offset", "Offset"), _f("tube", "Tube", choices=TUBE, name=True, ident=True),
             _f("thickness", "Disc thickness"), _f("color", "Color")],
}
CATEGORIES = tuple(CATEGORY_FIELDS)
CODE = {"Tires": "tire", "Battery": "battery", "Mags": "mags", "Rims": "rims"}          # what the items table stores
LABEL = {v: k for k, v in CODE.items()}
PREFIX = {"Tires": "TR", "Battery": "BA", "Mags": "MG", "Rims": "RM"}
HEADLINE = {"Tires": "size", "Battery": "model", "Mags": "size", "Rims": "size"}          # the field kept in items.size_model


def label_of(category: str) -> str:
    """'tire' / 'Tires' -> 'Tires' (the items table stores 'tire'; screens and the template say 'Tires')."""
    return LABEL.get(category, category)
COMMON_COLUMNS = [("brand", "Brand"), ("supplier_code", "Supplier item code"), ("unit", "Unit"), ("list_price", "List price"), ("effective_date", "Effective date"), ("remarks", "Remarks")]
_NAME_TEXT = {"terrain": lambda v: f"({v})", "holes": lambda v: f"{v}H", "volts": lambda v: f"{v}V", "ah": lambda v: f"{v}Ah",
              "tube": lambda v: {"TL": "Tubeless", "TT": "Tubetype"}.get(v, v)}


def _clean(text) -> str:
    return " ".join(str(text if text is not None else "").split())


def _norm(text) -> str:
    return re.sub(r"[\s\-_,]+", "", _clean(text).lower()).rstrip(".")


def normalise_specs(category: str, values: dict) -> dict[str, str]:
    """Every field of the category as clean text ('' when not given); choice fields are matched ignoring case ('a/t' -> 'A/T'). Unknown keys are dropped."""
    out = {}
    for fld in CATEGORY_FIELDS[category]:
        v = _clean(values.get(fld["key"]))
        if v and fld["choices"]:
            v = next((c for c in fld["choices"] if c.lower() == v.lower()), v)
        out[fld["key"]] = v
    return out


def standard_name(category: str, brand: str, **values) -> str:
    sp = normalise_specs(category, values)
    parts = [_clean(brand)]
    for fld in CATEGORY_FIELDS[category]:
        if fld["name"] and sp[fld["key"]]:
            parts.append(_NAME_TEXT.get(fld["key"], lambda v: v)(sp[fld["key"]]))
    return _clean(" ".join(parts))


def product_key(category: str, brand: str, remark: str = "", **values) -> str:
    category = label_of(category)
    sp = normalise_specs(category, values)
    return "|".join([CODE[category], _norm(brand)] + [_norm(sp[f["key"]]) for f in CATEGORY_FIELDS[category] if f["ident"]] + ([_norm(remark)] if _clean(remark) else []))


def _prefix_number(code: str, prefix: str) -> int | None:
    head, _, num = str(code).partition("-")
    return int(num) if head == prefix and num.isdigit() else None


def _next_code(conn, category: str) -> str:
    prefix = PREFIX[category]
    used = [n for (c,) in conn.execute("SELECT code FROM items WHERE code LIKE ?", (prefix + "-%",)) if (n := _prefix_number(c, prefix)) is not None]
    return f"{prefix}-{max(used, default=0) + 1:04d}"


def values_of(row) -> dict[str, str]:
    """An items row (category, size_model, specs) as the category's field values: the headline field comes back out of size_model."""
    category = label_of(row["category"])
    values = dict(json.loads(row["specs"] or "{}"))
    values[HEADLINE[category]] = row["size_model"]
    return values


def get_or_create_product(category: str, brand: str, conn=None, unit: str = "", remark: str = "", size_model: str = "", reorder_level: int = 2,
                          **values) -> tuple[int | None, str, bool]:
    """The item for these fields, created (with its code) if new. Returns (item_id, name, created). category may be the screen label ('Tires') or the stored code
    ('tire'). size_model = the headline field (size; for batteries the model) when given instead of through its own name. A missing category or brand: (None, '', False)."""
    category, brand, remark = label_of(category), _clean(brand), _clean(remark)
    if category not in CATEGORY_FIELDS or not brand:
        return None, "", False
    if size_model and not values.get(HEADLINE[category]):
        values[HEADLINE[category]] = size_model
    sp = normalise_specs(category, values)
    key, name = product_key(category, brand, remark, **sp), standard_name(category, brand, **sp)

    def work(c):
        row = c.execute("SELECT id, name FROM items WHERE key = ?", (key,)).fetchone()
        if row:
            return int(row[0]), row[1], False
        headline = sp[HEADLINE[category]]
        if not headline:
            return None, "", False
        other = {k: v for k, v in sp.items() if v and k != HEADLINE[category]}
        code = _next_code(c, category)
        new_id = c.execute("INSERT INTO items (code, category, brand, size_model, remark, key, specs, name, unit, reorder_level, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, acting_user()) RETURNING id",
                           (code, CODE[category], brand, headline, remark, key, json.dumps(other, sort_keys=True), name, _clean(unit) or None, reorder_level)).fetchone()[0]
        c.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'INV_ITEM_ADDED', 'items', ?, ?)", (new_id, f"{code} {name}"))
        return int(new_id), name, True

    if conn is not None:
        return work(conn)
    with write_tx() as c:
        return work(c)


def list_products(category: str | None = None, brand: str | None = None, active_only: bool = True) -> pd.DataFrame:
    """Items as a table. Columns kept from the earlier catalog so the screens and the template read them unchanged: product_id (= items.id), sku (= code), name, brand,
    category (label), specs, size_model, suppliers (how many suppliers price it), is_active."""
    where, params = [], []
    if category:
        where.append("category = ?")
        params.append(CODE.get(category, category))
    if brand:
        where.append("lower(brand) = ?")
        params.append(_clean(brand).lower())
    if active_only:
        where.append("is_active = 1")
    with get_conn() as conn:
        df = pd.read_sql_query(
            "SELECT i.id AS product_id, i.id, i.code AS sku, i.code, i.category, i.brand, i.size_model, i.remark, i.specs, i.name, i.unit, i.on_hand, i.avg_cost, i.is_active, "
            "(SELECT COUNT(DISTINCT creditor_id) FROM supplier_price_list s WHERE s.item_id = i.id AND s.is_active = 1) AS suppliers "
            f"FROM items i {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY i.category, lower(i.brand), lower(i.name)", conn, params=params)
    df["category"] = df["category"].map(label_of)
    return df


def spec_columns(df: pd.DataFrame, category: str) -> pd.DataFrame:
    """The items of one category with their fields as columns (the headline from size_model, the rest from specs), for the Items screens."""
    out = df.copy()
    specs = out["specs"].map(lambda t: json.loads(t or "{}"))
    for fld in CATEGORY_FIELDS[category]:
        k = fld["key"]
        out[k] = out["size_model"] if k == HEADLINE[category] else specs.map(lambda d: d.get(k, ""))
    return out


def brands(category: str | None = None) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT brand FROM items " + ("WHERE category = ? " if category else "") + "ORDER BY lower(brand)", (CODE.get(category, category),) if category else ()).fetchall()
    return [r[0] for r in rows]


def set_product_active(product_id: int, active: bool) -> tuple[bool, str]:
    """Discontinues an item (or brings it back). Items are never deleted: their prices, stock history and sales stay."""
    _ok, _msg = require_permission("price.write")
    if not _ok:
        return False, _msg
    with write_tx() as conn:
        row = conn.execute("SELECT name, code FROM items WHERE id = ?", (product_id,)).fetchone()
        if not row:
            return False, "Item not found."
        conn.execute("UPDATE items SET is_active = ? WHERE id = ?", (1 if active else 0, product_id))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), ?, 'items', ?, ?)",
                     ("INV_ITEM_REACTIVATED" if active else "INV_ITEM_DEACTIVATED", product_id, f"{row[1]} {row[0]}"))
    return True, f"{row[0]} {'is active again' if active else 'is discontinued'}."


def set_supplier_code(creditor_id: int, product_id: int, supplier_code: str, conn=None) -> None:
    """Remembers what this supplier calls the item (their own item code). A blank code does not erase one already saved."""
    code = _clean(supplier_code)

    def work(c):
        c.execute("INSERT INTO supplier_items (creditor_id, item_id, supplier_code) VALUES (?, ?, ?) "
                  "ON CONFLICT(creditor_id, item_id) DO UPDATE SET supplier_code = CASE WHEN excluded.supplier_code <> '' THEN excluded.supplier_code ELSE supplier_code END",
                  (creditor_id, product_id, code))

    if conn is not None:
        work(conn)
    else:
        with write_tx() as c:
            work(c)


def link_price_rows(creditor_id: int, item_name: str, product_id: int, conn=None) -> None:
    """Points this supplier's price rows for the item name at an item (all dates, so the history follows)."""
    sql = "UPDATE supplier_price_list SET item_id = ? WHERE creditor_id = ? AND lower(item_name) = ?"
    args = (product_id, creditor_id, _clean(item_name).lower())
    if conn is not None:
        conn.execute(sql, args)
    else:
        with write_tx() as c:
            c.execute(sql, args)


def supplier_prices(product_id: int | None = None, category: str | None = None, on: date | None = None) -> pd.DataFrame:
    """Side by side: for each item, each supplier's list price in force on a date, the discount chain, the net price, and a 'cheapest' flag on the lowest net.
    One item (product_id) or a whole category."""
    from engines.discount_rules import net_price, standing_chain
    on = on or manila_today()
    where, params = ["s.is_active = 1", "s.effective_date <= ?", "p.is_active = 1"], [str(on)]
    if product_id:
        where.append("p.id = ?")
        params.append(product_id)
    if category:
        where.append("p.category = ?")
        params.append(CODE.get(category, category))
    with get_conn() as conn:
        df = pd.read_sql_query(
            f"""SELECT p.id AS product_id, p.code AS sku, p.name AS product, p.brand, s.creditor_id, c.creditor_name AS supplier, s.item_name, s.unit_price AS list_price, s.effective_date, s.price_id
                FROM supplier_price_list s JOIN items p ON p.id = s.item_id JOIN creditors c ON c.creditor_id = s.creditor_id
                WHERE {' AND '.join(where)} ORDER BY s.effective_date DESC, s.price_id DESC""", conn, params=params)
    cols = ["product_id", "sku", "product", "brand", "supplier", "list_price", "chain", "net_price", "effective_date", "cheapest"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    df = df.groupby(["product_id", "creditor_id"], sort=False).head(1).copy()                  # the newest row in force, per item and supplier
    df["chain"] = [standing_chain(int(r.creditor_id), r.item_name, on) for r in df.itertuples()]
    df["net_price"] = [net_price(r.list_price, r.chain) for r in df.itertuples()]
    df["cheapest"] = df["net_price"].eq(df.groupby("product_id")["net_price"].transform("min")) & (df.groupby("product_id")["net_price"].transform("count") > 1)
    return df.sort_values(["product", "net_price"]).reset_index(drop=True)[cols]
