"""The price-list Excel template (2026-10-02): one workbook with a sheet per category (Tires, Battery, Mags, Rims) and the standard fields of that category, plus the
common columns Brand, Supplier item code, Unit, List price, Effective date, Remarks. Download it blank or pre-filled with this supplier's current prices, fill it,
upload it. The upload is checked row by row BEFORE anything is saved; problems are reported as 'Tires row 14: ...'. Saving goes through the product catalog (a new
product gets its SKU) and price_list_service.import_price_rows, so the history by effective date and the audit trail are the same as for a typed price.

  * build_template(creditor_id=None)      -- the .xlsx bytes
  * parse_workbook(file_bytes, default_date) -- rows + problems, nothing saved
  * compare(creditor_id, parsed)          -- each row new / changed / same against the prices in force; what the file no longer lists
  * import_workbook(creditor_id, parsed)  -- saves the rows that have no problems"""
from __future__ import annotations

import io
from datetime import date

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from engines import price_list_service as pls
from engines import product_catalog as pc
from engines.clock import manila_today
from engines.common import get_conn, write_tx
from engines.permissions import require as require_permission

MAX_ROWS = 2000
# the one standard sheet for every product (owner, 2026-10-08); the older workbook with a sheet per category is still read
SHEET = "Price list"
STANDARD = [("supplier", "Supplier", False), ("category", "Category", True), ("brand", "Brand", True), ("model", "Model/Pattern", False), ("size", "Size", False), ("terrain", "Terrain", False), ("other", "Other specs", False),
            ("unit", "Unit", False), ("supplier_code", "Supplier item code", False), ("list_price", "List price", True), ("effective_date", "Effective date", False)]
STANDARD_HOW_TO = [
    "How to fill this price list",
    "",
    "INSTRUCTIONS FOR THE AI THAT FILLS THIS FILE (copy everything below this line together with the supplier's price list):",
    "Fill the sheet 'Price list' from the attached supplier price list(s). One row per product. Follow every rule:",
    "1. Supplier: the supplier's name exactly as printed on its price list, on EVERY row. One file = one supplier.",
    "2. Category: Tires, Battery, Mags or Rims.",
    "3. Brand: on EVERY row, even when the brand is only on the logo or in the file name.",
    "4. Size: only the size, e.g. 285/50R20, LT255/55R19, 225/55ZR17, 11R22.5, 7.00R16, 31X10.50R15. (Batteries: leave empty.)",
    "5. Model/Pattern: only the pattern or model code, e.g. NU025, SL369, EZ525. If a line shows two patterns ('NU006 / NU033', 'RF02/RA85'), make ONE ROW PER PATTERN",
    "   with the same size and price.",
    "6. Terrain (tires): A/T, H/T, M/T or R/T only, or leave it empty. Never inside Model/Pattern.",
    "7. Other specs: everything else printed for that line, separated by spaces, written as on the price list: ply (14PR), load/speed (114V, 121/118R), XL, C8,",
    "   tread (Rib, Mix, Lug, M.Lug), TL or TT, Heavy Duty, Mining, Indian Specs ... Include section headings that apply to the line (e.g. 'Heavy Duty').",
    "   Batteries: 12V 45Ah L MF.  Mags: 5x114.3 ET40 Silver.  Rims: 10H TL.",
    "8. List price: exactly as printed, BEFORE any discount (the discount is set in the system).",
    "9. Effective date: the date printed on the price list (e.g. 'Effective Sept 15' -> 2026-09-15), as YYYY-MM-DD.",
    "10. Never repeat a row. Never add a product that is not on the price list. Never skip one.",
    "",
    "How the system reads it: two rows are the same product only when category, brand, size, model/pattern, terrain and other specs all match (capitals, spaces,",
    "dashes and the order of the other specs ignored). So '14PR Rib TT' and '14PR Rib TL' are two different tires with their own prices.",
    "Before anything is saved, the system checks the supplier, shows the rows per brand, and lists every row it could not read.",
]
HOW_TO = [
    "How to fill this price list",
    "",
    "1. One sheet per kind of product: Tires, Battery, Mags, Rims. Use only the sheets you need; empty sheets are ignored.",
    "2. One row per product. Columns marked * must be filled. Brand is always required.",
    "3. List price is the supplier's price BEFORE any discount. The supplier's discount (like less 5%, less 5%) is set in the system, not here.",
    "4. Effective date is the day the price starts, like 2026-09-15. If you leave it empty, the date you pick when uploading is used.",
    "5. Columns with a drop-down (Terrain, Type, Tube, Terminal) accept only the listed values.",
    "6. Supplier item code is the supplier's own code for the product. It is kept next to our own item code (SKU), which the system makes.",
    "7. The same product on a later date is a new price: the old price stays in the history.",
    "",
    "Before anything is saved, the system shows what is new, what changed and every row it could not read.",
]


def _headers(category: str) -> list[tuple[str, str, bool]]:
    """(key, header text, required) in column order: Brand, the category's fields, then the common columns after it."""
    cols = [("brand", "Brand", True)] + [(f["key"], f["label"], f["required"]) for f in pc.CATEGORY_FIELDS[category]]
    return cols + [("supplier_code", "Supplier item code", False), ("unit", "Unit", False), ("list_price", "List price", True), ("effective_date", "Effective date", False), ("remarks", "Remarks", False)]


def _title(text: str, required: bool) -> str:
    return text + (" *" if required else "")


def build_template(creditor_id: int | None = None, on: date | None = None, layout: str = "standard") -> bytes:
    """The workbook: the one standard sheet (layout="standard", what is downloaded) or the older sheet per category ("by_category", still read on upload).
    With a supplier, it is pre-filled with that supplier's current prices (list prices in force on `on`) so a price update is: change, upload."""
    if layout == "standard":
        return _build_standard(creditor_id, on)
    wb = Workbook()
    guide = wb.active
    guide.title = "How to fill"
    for i, line in enumerate(HOW_TO, start=1):
        guide.cell(row=i, column=1, value=line).font = Font(bold=(i == 1), size=14 if i == 1 else 11)
    guide.column_dimensions["A"].width = 130
    current = _current_rows(creditor_id, on or manila_today()) if creditor_id else {}
    head_fill, req_fill = PatternFill("solid", fgColor="1F3A5F"), PatternFill("solid", fgColor="B45309")
    for category in pc.CATEGORIES:
        ws = wb.create_sheet(category)
        headers = _headers(category)
        for j, (key, text, required) in enumerate(headers, start=1):
            c = ws.cell(row=1, column=j, value=_title(text, required))
            c.font, c.fill, c.alignment = Font(bold=True, color="FFFFFF"), (req_fill if required else head_fill), Alignment(wrap_text=True, vertical="center")
            ws.column_dimensions[get_column_letter(j)].width = max(14, len(text) + 6)
        ws.row_dimensions[1].height = 32
        ws.freeze_panes = "A2"
        for key, text, required in headers:
            fld = next((f for f in pc.CATEGORY_FIELDS[category] if f["key"] == key), None)
            j = [h[0] for h in headers].index(key) + 1
            if False:                                   # drop-down lists removed (owner, 2026-10-04: the template is filled by another AI; any value is accepted)
                pass
            elif fld and fld["note"]:
                ws.cell(row=1, column=j).comment = None
        for r, rowvals in enumerate(current.get(category, []), start=2):
            for j, (key, _, _) in enumerate(headers, start=1):
                v = rowvals.get(key, "")
                cell = ws.cell(row=r, column=j, value=v if v != "" else None)
                if key == "effective_date" and v:
                    cell.number_format = "yyyy-mm-dd"
                if key == "list_price":
                    cell.number_format = "#,##0.00"
        ws.cell(row=len(current.get(category, [])) + 2, column=1)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _build_standard(creditor_id: int | None, on: date | None) -> bytes:
    wb = Workbook()
    guide = wb.active
    guide.title = "How to fill"
    for i, line in enumerate(STANDARD_HOW_TO, start=1):
        guide.cell(row=i, column=1, value=line).font = Font(bold=(i == 1), size=14 if i == 1 else 11)
    guide.column_dimensions["A"].width = 150
    ws = wb.create_sheet(SHEET)
    head_fill, req_fill = PatternFill("solid", fgColor="1F3A5F"), PatternFill("solid", fgColor="B45309")
    for j, (key, text, required) in enumerate(STANDARD, start=1):
        c = ws.cell(row=1, column=j, value=_title(text, required))
        c.font, c.fill, c.alignment = Font(bold=True, color="FFFFFF"), (req_fill if required else head_fill), Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[get_column_letter(j)].width = {"other": 34, "model": 22, "size": 16}.get(key, max(14, len(text) + 6))
    ws.row_dimensions[1].height = 32
    ws.freeze_panes = "A2"
    current = _current_rows(creditor_id, on or manila_today(), with_remark=True) if creditor_id else {}
    supplier = _supplier_name(creditor_id) if creditor_id else ""
    r = 2
    for category in pc.CATEGORIES:
        for row in current.get(category, []):
            model, size, other = pc.other_specs_text(category, row, row.get("item_remark", ""))
            vals = {"supplier": supplier, "category": category, "brand": row["brand"], "model": model, "size": size,
                    "terrain": row.get("terrain", "") if category == "Tires" else "", "other": other, "unit": row["unit"],
                    "supplier_code": row["supplier_code"], "list_price": row["list_price"], "effective_date": row["effective_date"]}
            for j, (key, _, _) in enumerate(STANDARD, start=1):
                v = vals.get(key, "")
                cell = ws.cell(row=r, column=j, value=v if v != "" else None)
                if key == "effective_date" and v:
                    cell.number_format = "yyyy-mm-dd"
                if key == "list_price":
                    cell.number_format = "#,##0.00"
            r += 1
    if supplier and r == 2:                                     # no prices yet: the supplier's name on the first row shows who the file is for
        ws.cell(row=2, column=1, value=supplier)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _supplier_name(creditor_id: int) -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT creditor_name FROM creditors WHERE creditor_id = ?", (creditor_id,)).fetchone()
    return row[0] if row else ""


_COMPANY_WORDS = {"corp", "corporation", "inc", "incorporated", "co", "company", "trading", "enterprises", "enterprise", "ltd", "the", "and"}


def _name_words(name: str) -> list[str]:
    import re
    return [w for w in re.findall(r"[a-z0-9]+", (name or "").lower()) if w not in _COMPANY_WORDS]


def same_supplier(file_name: str, creditor_name: str) -> bool:
    """Is the supplier named in the file the one chosen? Ignoring capitals, spaces and punctuation, and words like Corp., Inc., Trading; a shortened name
    counts ('New Century' = 'New Century City Marketing Corp.')."""
    a, b = _name_words(file_name), _name_words(creditor_name)
    if not a or not b:
        return False
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return long_[:len(short)] == short


def supplier_check(creditor_id: int, parsed: dict) -> dict:
    """Who the file says it is for, against the supplier chosen (owner, 2026-10-08: a file uploaded for the wrong supplier is refused).
    {'level': 'ok' | 'none' (the file does not say: a warning) | 'mismatch' (refused), 'names': [names in the file], 'chosen': name}."""
    chosen = _supplier_name(creditor_id)
    names = parsed.get("suppliers", [])
    if not names:
        return {"level": "none", "names": [], "chosen": chosen}
    return {"level": "ok" if all(same_supplier(n, chosen) for n in names) else "mismatch", "names": names, "chosen": chosen}


def mismatch_message(sc: dict) -> str:
    return f"This file is for {', '.join(sc['names'])}, but you chose {sc['chosen']}. Pick the right supplier and check the file again. Nothing was saved."


def _current_rows(creditor_id: int, on: date, with_remark: bool = False) -> dict[str, list[dict]]:
    """This supplier's prices in force on a date, per category, as template rows (only products that are in the catalog)."""
    with get_conn() as conn:
        df = pd.read_sql_query(
            """SELECT p.id, p.category, p.brand, p.size_model, p.specs, p.remark, p.unit AS product_unit, s.unit, s.unit_price, s.effective_date, s.price_id,
                      (SELECT supplier_code FROM supplier_items si WHERE si.creditor_id = s.creditor_id AND si.item_id = p.id) AS supplier_code
               FROM supplier_price_list s JOIN items p ON p.id = s.item_id
               WHERE s.creditor_id = ? AND s.is_active = 1 AND p.is_active = 1 AND s.effective_date <= ?
               ORDER BY s.effective_date DESC, s.price_id DESC""", conn, params=(creditor_id, str(on)))
    out: dict[str, list[dict]] = {c: [] for c in pc.CATEGORIES}
    if df.empty:
        return out
    df = df.drop_duplicates(subset=["id"])
    import json
    df["category"] = df["category"].map(pc.label_of)
    for r in df.sort_values(["category", "brand", "size_model"]).itertuples():
        spec = json.loads(r.specs or "{}")
        row = {"brand": r.brand, "supplier_code": r.supplier_code or "", "unit": r.unit or r.product_unit or "", "list_price": float(r.unit_price),
               "effective_date": date.fromisoformat(str(r.effective_date)[:10]), "remarks": ""}
        if with_remark:
            row["item_remark"] = r.remark or ""
        for fld in pc.CATEGORY_FIELDS[r.category]:
            k = fld["key"]
            row[k] = r.size_model if k == pc.HEADLINE[r.category] else spec.get(k, "")
        out[r.category].append(row)
    return out


# --- reading an uploaded workbook ---------------------------------------------------------------------------------------------------------------

def parse_workbook(file_bytes: bytes, default_date: date | None = None) -> dict:
    """Reads the template. Returns {'rows': [valid rows], 'problems': ['Tires row 14: ...'], 'counts': {sheet: rows read}, 'sheets_missing': [...]}.
    A row with any problem is not in 'rows'; a blank row is skipped silently. Never raises."""
    out = {"rows": [], "problems": [], "counts": {}, "sheets_missing": [], "notes": [], "suppliers": [], "brands": {}, "tidied": []}
    try:
        wb = load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    except Exception as e:
        out["problems"].append(f"Could not open the file as an Excel workbook: {e}")
        return out
    seen: set[tuple] = set()
    if SHEET in wb.sheetnames:
        _parse_standard(list(wb[SHEET].iter_rows(values_only=True)), default_date, out, {})
        if not out["rows"] and not out["problems"]:
            out["problems"].append("The file has no product rows.")
        return out
    for category in pc.CATEGORIES:
        if category not in wb.sheetnames:
            out["sheets_missing"].append(category)
            continue
        rows = list(wb[category].iter_rows(values_only=True))
        if not rows:
            continue
        headers = _headers(category)
        by_title = {" ".join(str(h or "").replace("*", "").split()).lower(): i for i, h in enumerate(rows[0])}
        idx = {}
        for key, text, required in headers:
            i = by_title.get(text.lower())
            if i is None:
                if required:
                    out["problems"].append(f"{category}: the column '{text}' is missing. Download a fresh template.")
                continue
            idx[key] = i
        if any(p.startswith(f"{category}: the column") for p in out["problems"]):
            continue
        n = 0
        for rno, row in enumerate(rows[1:], start=2):
            def cell(k):
                i = idx.get(k)
                v = row[i] if i is not None and i < len(row) else None
                return "" if v is None else (v if isinstance(v, (int, float, date)) and not isinstance(v, bool) else str(v).strip())
            if not any(str(v).strip() for v in row if v is not None):
                continue
            n += 1
            where, bad = f"{category} row {rno}", []
            brand = pc._clean(cell("brand"))
            if not brand:
                bad.append("Brand is empty")
            values = {}
            for fld in pc.CATEGORY_FIELDS[category]:
                v = cell(fld["key"])
                v = ("%g" % v) if isinstance(v, float) and v == int(v) else v
                v = pc._clean(v)
                if fld["required"] and not v:
                    bad.append(f"{fld['label']} is empty")
                values[fld["key"]] = v
            raw_price = cell("list_price")
            price = pls._to_price(raw_price)
            if price is None:
                bad.append(f"List price '{raw_price}' is not a number" if raw_price != "" else "List price is empty")
            raw_date = cell("effective_date")
            when = pls._to_date(raw_date) if raw_date != "" else default_date
            if when is None:
                bad.append(f"Effective date '{raw_date}' could not be read" if raw_date != "" else "Effective date is empty (or pick a start date when uploading)")
            if not bad:
                key = (category, pc.product_key(category, brand, **values), str(when))
                if key in seen:
                    bad.append("the same product and date appears twice in the file")
                seen.add(key)
            if bad:
                out["problems"].append(f"{where}: " + "; ".join(bad) + ".")
                continue
            out["brands"][brand] = out["brands"].get(brand, 0) + 1
            out["rows"].append({"category": category, "row": rno, "brand": brand, "values": values, "supplier_code": pc._clean(cell("supplier_code")), "unit": pc._clean(cell("unit")),
                                "list_price": price, "effective_date": when, "remarks": pc._clean(cell("remarks")),
                                "name": pc.standard_name(category, brand, **values)})
        out["counts"][category] = n
    if not out["rows"] and not out["problems"]:
        out["problems"].append("The file has no product rows.")
    return out


def _parse_standard(rows: list, default_date: date | None, out: dict, seen: dict) -> None:
    """The one standard sheet: rows named 'Price list row 14: ...'. Each row is read into its category's fields (product_catalog.split_other_specs).
    seen: (category, product key, date) -> (row, price) of the first row. A later row exactly like it is skipped with a note (out['notes']); the same product
    and date at another price is a problem, since only the owner knows which price is right."""
    if not rows:
        return
    by_title = {" ".join(str(h or "").replace("*", "").split()).lower(): i for i, h in enumerate(rows[0])}
    idx = {key: by_title[text.lower()] for key, text, _ in STANDARD if text.lower() in by_title}
    missing = [text for key, text, required in STANDARD if required and key not in idx]
    if missing:
        out["problems"].append(f"{SHEET}: the column(s) {', '.join(repr(m) for m in missing)} are missing. Download a fresh template.")
        return
    for rno, row in enumerate(rows[1:], start=2):
        def cell(k):
            i = idx.get(k)
            v = row[i] if i is not None and i < len(row) else None
            if isinstance(v, float) and v == int(v) and k not in ("list_price",):
                v = "%g" % v
            return "" if v is None else (v if isinstance(v, (int, float, date)) and not isinstance(v, bool) else str(v).strip())
        sup = pc._clean(cell("supplier"))
        if sup and sup.lower() not in (s.lower() for s in out["suppliers"]):
            out["suppliers"].append(sup)
        if not any(str(v).strip() for i, v in enumerate(row) if v is not None and i != idx.get("supplier")):
            continue                                            # a blank row (or only the supplier's name, as on a fresh supplier template)
        where, bad = f"{SHEET} row {rno}", []
        raw_cat = cell("category")
        category = pc.category_of(raw_cat)
        if category is None:
            bad.append(f"Category '{raw_cat}' is not Tires, Battery, Mags or Rims" if raw_cat != "" else "Category is empty")
        brand = pc._clean(cell("brand"))
        if not brand:
            bad.append("Brand is empty")
        values, remark = {}, ""
        if category == "Tires":                                 # brand, size, pattern, terrain kept clean; everything else = the others (owner, 2026-10-08)
            out["counts"][category] = out["counts"].get(category, 0) + 1
            values, remark, tire_bad = pc.tidy_tire(str(cell("model")), str(cell("size")), str(cell("terrain")), str(cell("other")))
            values = pc.normalise_specs(category, values)
            bad += tire_bad
            if values["size"] != pc._clean(cell("size")).replace(" ", "").upper() and not tire_bad:
                out["tidied"].append(f"{where}: {pc._clean(cell('size'))} → size {values['size']}, {remark}")
        elif category:
            out["counts"][category] = out["counts"].get(category, 0) + 1
            other = " ".join(x for x in (str(cell("terrain")), str(cell("other"))) if x.strip())
            values, remark = pc.split_other_specs(category, str(cell("model")), str(cell("size")), other)
            values = pc.normalise_specs(category, values)
            for fld in pc.CATEGORY_FIELDS[category]:
                if fld["required"] and not values[fld["key"]]:
                    bad.append(f"{fld['label']} is empty" + ("" if fld["key"] in ("size", "pattern", "model") else " (put it in Other specs)"))
        raw_price = cell("list_price")
        price = pls._to_price(raw_price)
        if price is None:
            bad.append(f"List price '{raw_price}' is not a number" if raw_price != "" else "List price is empty")
        raw_date = cell("effective_date")
        when = pls._to_date(raw_date) if raw_date != "" else default_date
        if when is None:
            bad.append(f"Effective date '{raw_date}' could not be read" if raw_date != "" else "Effective date is empty (or pick a start date when uploading)")
        if not bad:
            key = (category, pc.product_key(category, brand, remark, **values), str(when))
            first = seen.get(key)
            if first is not None and abs(first[1] - price) < 0.005:          # an exact repeat (an AI-filled file may write a line twice): skipped, not a problem
                out["notes"].append(f"{where}: the same as row {first[0]} (same product, date and price), so it is skipped.")
                continue
            if first is not None:
                bad.append(f"the same product and date as row {first[0]}, but a different price ({first[1]:,.2f} there): keep the right one")
            else:
                seen[key] = (rno, price)
        if bad:
            out["problems"].append(f"{where}: " + "; ".join(bad) + ".")
            continue
        out["brands"][brand] = out["brands"].get(brand, 0) + 1
        out["rows"].append({"category": category, "row": rno, "brand": brand, "values": values, "item_remark": remark, "supplier_code": pc._clean(cell("supplier_code")),
                            "unit": pc._clean(cell("unit")), "list_price": price, "effective_date": when, "remarks": "",
                            "name": pc.standard_name(category, brand, **values) + (f" {remark}" if remark else "")})


def compare(creditor_id: int, parsed: dict, on: date | None = None) -> pd.DataFrame:
    """One line per valid row: status 'new' (no price yet for this product), 'changed' or 'same' against the price in force on the row's date, with the old price and
    the % change; plus a 'missing' line for each product this supplier had a price for in a category the file covers but the file no longer lists. Information only."""
    lines, listed = [], set()
    with get_conn() as conn:
        prices = pd.read_sql_query(
            """SELECT p.key AS product_key, p.name, p.category, s.unit_price, s.effective_date, s.price_id FROM supplier_price_list s JOIN items p ON p.id = s.item_id
               WHERE s.creditor_id = ? AND s.is_active = 1 ORDER BY s.effective_date DESC, s.price_id DESC""", conn, params=(creditor_id,))
    prices["category"] = prices["category"].map(pc.label_of)
    for r in parsed["rows"]:
        key = pc.product_key(r["category"], r["brand"], r.get("item_remark", ""), **r["values"])
        listed.add(key)
        hist = prices[(prices["product_key"] == key) & (prices["effective_date"] <= str(r["effective_date"]))]
        old = float(hist.iloc[0]["unit_price"]) if not hist.empty else None
        new = r["list_price"]
        status = "new" if old is None else ("same" if abs(old - new) < 0.005 else "changed")
        lines.append({"category": r["category"], "row": r["row"], "name": r["name"], "old_price": old, "new_price": new,
                      "change_pct": None if old in (None, 0) or status == "new" else round((new - old) / old * 100, 1), "status": status})
    covered = set(parsed["counts"]) & {r["category"] for r in parsed["rows"]}
    if not prices.empty:
        now = prices.drop_duplicates("product_key")
        for p in now.itertuples():
            if p.category in covered and p.product_key not in listed:
                lines.append({"category": p.category, "row": None, "name": p.name, "old_price": float(p.unit_price), "new_price": None, "change_pct": None, "status": "missing"})
    return pd.DataFrame(lines, columns=["category", "row", "name", "old_price", "new_price", "change_pct", "status"])


def import_workbook(creditor_id: int, parsed: dict, on_duplicate: str = "skip") -> tuple[bool, str, dict]:
    """Saves the rows without problems. A product that is new gets its SKU; a price equal to the one already in force adds nothing. Returns (ok, message, counts)."""
    _ok, _msg = require_permission("price.write")
    if not _ok:
        return False, _msg, {}
    if not parsed["rows"]:
        return False, "No rows to save.", {}
    sc = supplier_check(creditor_id, parsed)
    if sc["level"] == "mismatch":                               # never saved under the wrong supplier (owner, 2026-10-08)
        return False, mismatch_message(sc), {}
    cmp = compare(creditor_id, parsed)
    same = {(r.category, r.row) for r in cmp.itertuples() if r.status == "same"}
    counts = {"added": 0, "replaced": 0, "skipped": 0, "products": 0, "kept_old": 0}
    price_rows, links = [], []
    with write_tx() as conn:
        for r in parsed["rows"]:
            pid, name, created = pc.get_or_create_product(r["category"], r["brand"], conn=conn, unit=r["unit"], remark=r.get("item_remark", ""), **r["values"])
            counts["products"] += int(created)
            pc.set_supplier_code(creditor_id, pid, r["supplier_code"], conn=conn)
            links.append((name, pid))
            if (r["category"], r["row"]) in same:
                counts["skipped"] += 1
                continue
            price_rows.append({"item_name": name, "unit": r["unit"] or "pc", "unit_price": r["list_price"], "item_code": r["supplier_code"], "effective_date": r["effective_date"]})
    res = pls.import_price_rows(creditor_id, price_rows, manila_today(), on_duplicate) if price_rows else {"added": 0, "replaced": 0, "skipped": 0}
    for k in ("added", "replaced"):
        counts[k] += res[k]
    counts["kept_old"] = res["skipped"]                      # a different price for a date that already has one: kept the old one (choose Replace to change it)
    counts["skipped"] += res["skipped"]
    with write_tx() as conn:
        for name, pid in links:
            pc.link_price_rows(creditor_id, name, pid, conn=conn)
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'PRICE_TEMPLATE_IMPORTED', 'supplier_price_list', ?, ?)",
                     (creditor_id, f"{counts['added']} added, {counts['replaced']} replaced, {counts['skipped']} unchanged/skipped, {counts['products']} new product(s)"))
    unchanged = counts["skipped"] - counts["kept_old"]
    return True, (f"{counts['added']} price(s) added, {counts['replaced']} replaced, {unchanged} unchanged" + (f", {counts['products']} new product(s)" if counts["products"] else "")
                  + (f". {counts['kept_old']} new price(s) NOT saved because that date already has a price: upload again with 'Replace' to change them" if counts["kept_old"] else "")
                  + "."), counts
