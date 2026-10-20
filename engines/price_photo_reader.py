"""Price lists > Read a photo (owner, 2026-10-20; mock-up price_photo_reader.html, "build"). Photos of a supplier's tire price list are read by the AI
(engines/ai_client.py) into rows; a person checks them beside the photo, answers the questions (which price column, tube type, the discount) and
saves. Nothing is saved until then, and the save goes through the same import as Excel (old prices stay as history, audited).

The AI only suggests. Odd figures are flagged here, by rules, not by the AI: a price lower than a smaller size of the same pattern, or far from the
other sizes of its pattern. A row joins an existing item only when every detail matches (the item key); otherwise it is a new item.
Each read is kept for a day in data/web_uploads/photo_reads/<token>/ (the photos and what was read), then removed."""
from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
import shutil
import time
from pathlib import Path

MAX_PHOTOS = 8
MAX_SIDE = 2000                    # pixels: enough to read a printed list, and a fraction of the cost of a full phone photo
KEEP_SECONDS = 86400

SCHEMA = {
    "type": "object",
    "properties": {
        "brand": {"type": "string", "description": "The tire brand the list is for, as printed in its header (e.g. Sonix, Bridgestone). Empty if not shown."},
        "effective_date": {"type": "string", "description": "The date the prices start, as YYYY-MM-DD; empty if not shown."},
        "columns": {"type": "array", "items": {"type": "string"}, "description": "The price columns in the order printed, e.g. [\"GROSS PRICE\", \"NET PRICE\"]."},
        "handwritten": {"type": "string", "description": "Any handwritten note about a discount or terms, word for word (e.g. \"-25 -5\", \"less 5%\"); empty if none."},
        "photos": {"type": "array", "items": {"type": "object", "properties": {
            "photo": {"type": "integer", "description": "Photo number, starting at 1."},
            "same_as": {"type": "integer", "description": "If this photo shows the same page as an earlier photo, that photo's number; else 0."}},
            "required": ["photo", "same_as"], "additionalProperties": False}},
        "rows": {"type": "array", "items": {"type": "object", "properties": {
            "photo": {"type": "integer"},
            "size": {"type": "string", "description": "The size exactly as printed but without spaces, e.g. 205/55R16, 185R14C, LT235/75R15, 31X10.5R15LT, 205/55ZR16."},
            "ply": {"type": "string", "description": "Ply or load/speed when printed in the size or a column, e.g. 8PR, 91H; else empty."},
            "pattern": {"type": "string", "description": "The pattern name as printed, e.g. ECOPRO 99, Dueler H/T 684."},
            "prices": {"type": "array", "items": {"type": "number"}, "description": "The row's prices in the same order as columns; 0 where blank."},
            "unclear": {"type": "boolean", "description": "True when any figure or letter of this row was hard to read."}},
            "required": ["photo", "size", "ply", "pattern", "prices", "unclear"], "additionalProperties": False}},
    },
    "required": ["brand", "effective_date", "columns", "handwritten", "photos", "rows"],
    "additionalProperties": False,
}

PROMPT = ("These photos are pages of a supplier's tire price list in the Philippines (prices in pesos). Read every price row on every page. "
          "If two photos show the same page, read it once and mark the later photo same_as the earlier one. Copy sizes, patterns and prices exactly "
          "as printed; never guess a figure -- if one is hard to read, give your best reading and set unclear. Do not add rows that are not printed. "
          "Report the price columns in the order printed, and any handwritten discount note word for word.")


def _root() -> Path:
    import engines.db_engine as d
    return Path(d.DB_PATH).parent / "web_uploads" / "photo_reads"


def _dir(token: str) -> Path | None:
    if not token or not re.fullmatch(r"[0-9a-f]{32}", token):
        return None
    p = _root() / token
    return p if p.is_dir() else None


def _cleanup() -> None:
    root = _root()
    if not root.is_dir():
        return
    for p in root.iterdir():
        try:
            if time.time() - p.stat().st_mtime > KEEP_SECONDS:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass


def _shrink(data: bytes) -> bytes:
    """A phone photo made smaller (longest side MAX_SIDE, JPEG), upright as taken."""
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((MAX_SIDE, MAX_SIDE))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=85)
        return out.getvalue()


def read_photos(cid: int, files: list[tuple[str, bytes]]) -> tuple[bool, str, str]:
    """Reads the photos (name, bytes). Returns (ok, message, token of the read to check)."""
    from engines import ai_client
    from engines.permissions import require
    ok, why = require("price.write")
    if not ok:
        return False, why, ""
    files = [(n, b) for n, b in files if b]
    if not files:
        return False, "Add at least one photo.", ""
    if len(files) > MAX_PHOTOS:
        return False, f"At most {MAX_PHOTOS} photos at a time.", ""
    _cleanup()
    shrunk, seen, dup = [], {}, {}
    for i, (name, data) in enumerate(files, start=1):
        h = hashlib.sha256(data).hexdigest()
        if h in seen:                                   # the very same file twice: not sent again
            dup[i] = seen[h]
            shrunk.append(None)
            continue
        seen[h] = i
        try:
            shrunk.append(_shrink(data))
        except Exception:                               # noqa: BLE001 -- not a picture
            return False, f"{name or 'Photo ' + str(i)} is not a photo (JPEG or PNG).", ""
    blocks = []
    for i, b in enumerate(shrunk, start=1):
        if b is not None:
            blocks += [{"type": "text", "text": f"Photo {i}:"}, ai_client.image_block(b, "image/jpeg")]
    blocks.append({"type": "text", "text": PROMPT})
    ok, text, msg = ai_client.ask("price_photo", blocks, schema=SCHEMA, max_tokens=16000)
    if not ok:
        return False, msg, ""
    try:
        read = json.loads(text)
    except ValueError:
        return False, "The AI's answer could not be read. Try again, or type the prices in.", ""
    token = secrets.token_hex(16)
    d = _root() / token
    d.mkdir(parents=True, exist_ok=True)
    for i, b in enumerate(shrunk, start=1):
        if b is not None:
            (d / f"{i}.jpg").write_bytes(b)
    same = {int(p.get("photo") or 0): int(p.get("same_as") or 0) for p in read.get("photos", [])}
    same.update(dup)
    read["photos"] = [{"photo": i, "same_as": same.get(i, 0), "has_image": shrunk[i - 1] is not None} for i in range(1, len(files) + 1)]
    read["rows"] = [r for r in read.get("rows", []) if not same.get(int(r.get("photo") or 0))]      # rows read again from a repeated photo: dropped
    read["cid"] = int(cid)
    (d / "read.json").write_text(json.dumps(read), encoding="utf-8")
    n = len(read["rows"])
    return True, f"{n} row{'s' if n != 1 else ''} read. Check them against the photos, then save.", token


def photo(token: str, i: int) -> bytes | None:
    d = _dir(token)
    if d is None:
        return None
    p = d / f"{int(i)}.jpg"
    return p.read_bytes() if p.is_file() else None


def _rim_width(size: str) -> tuple[int, int]:
    m = re.search(r"R(\d{2})", size.upper())
    w = re.match(r"(?:LT)?(\d+)", size.upper())
    return (int(m.group(1)) if m else 0, int(w.group(1)) if w else 0)


def odd_prices(rows: list[dict], col: int) -> dict[int, str]:
    """{row index: why it looks odd}: lower than a smaller size of the same pattern and rim, or far from the other sizes of its pattern."""
    out: dict[int, str] = {}
    by_pattern: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        by_pattern.setdefault(r["pattern"].strip().upper(), []).append(i)

    def price(i):
        p = rows[i]["prices"]
        return float(p[col]) if col < len(p) and p[col] else 0.0
    for idx in by_pattern.values():
        prices = sorted(price(i) for i in idx if price(i) > 0)
        med = prices[len(prices) // 2] if prices else 0
        for i in idx:
            pi = price(i)
            if pi <= 0:
                continue
            ri, wi = _rim_width(rows[i]["size"])
            for j in idx:
                rj, wj = _rim_width(rows[j]["size"])
                if j != i and rj == ri and wj < wi and price(j) > pi:
                    out[i] = f"lower than {rows[j]['size']}"
                    break
            if i not in out and len(prices) >= 3 and (pi > med * 1.4 or pi < med * 0.6):
                out[i] = "far from the other sizes of this pattern"
    return out


def review(token: str, col: int = 0, brand: str = "", tube: str = "TL") -> dict | None:
    """What was read, ready to check: each row with its name, whether it is already in the catalog, and why it looks odd."""
    from engines import product_catalog as pc
    from engines.common import get_conn
    d = _dir(token)
    if d is None:
        return None
    read = json.loads((d / "read.json").read_text(encoding="utf-8"))
    brand = brand or read.get("brand") or ""
    cols = read.get("columns") or ["Price"]
    col = col if 0 <= col < len(cols) else 0
    rows = read.get("rows", [])
    odd = odd_prices(rows, col)
    out = []
    with get_conn() as conn:
        for i, r in enumerate(rows):
            values = {"size": r["size"], "pattern": r["pattern"], "tube": tube}
            if r.get("ply"):
                values["ply"] = r["ply"]
            code = None
            if brand:
                sp = pc.normalise_specs("Tires", values)
                hit = conn.execute("SELECT code FROM items WHERE key = ?", (pc.product_key("Tires", brand, "", **sp),)).fetchone()
                code = hit[0] if hit else None
            p = r["prices"]
            out.append({"i": i, "photo": r.get("photo"), "size": r["size"], "ply": r.get("ply", ""), "pattern": r["pattern"],
                        "price": float(p[col]) if col < len(p) and p[col] else 0.0, "unclear": bool(r.get("unclear")), "odd": odd.get(i, ""),
                        "code": code, "name": pc.standard_name("Tires", brand, **values) if brand else ""})
    return {"token": token, "cid": read.get("cid"), "brand": brand, "effective_date": read.get("effective_date") or "", "columns": cols, "col": col, "col_name": cols[col],
            "handwritten": read.get("handwritten") or "", "photos": read.get("photos", []), "rows": out,
            "to_check": sum(1 for r in out if r["unclear"] or r["odd"]), "new": sum(1 for r in out if not r["code"])}


def save(token: str, cid: int, brand: str, effective_date, tube: str, rows: list[dict], discount: str = "", discount_brand_only: bool = True,
         replace: bool = False) -> tuple[bool, str]:
    """Saves the checked rows [{'size', 'ply', 'pattern', 'price'}] for the supplier, through the Excel import. The discount, when given, is saved
    for the brand only (or all the supplier's brands)."""
    from datetime import date
    from engines import product_catalog as pc
    from engines.discount_rules import parse_chain, set_discount
    from engines.price_template_service import import_workbook
    d = _dir(token)
    if d is None:
        return False, "This reading has expired. Read the photos again."
    brand = " ".join((brand or "").split())
    if not brand:
        return False, "Type the brand."
    try:
        eff = effective_date if isinstance(effective_date, date) else date.fromisoformat(str(effective_date)[:10])
    except ValueError:
        return False, "Type the date the prices start."
    clean = []
    for n, r in enumerate(rows, start=1):
        size, pattern = "".join(str(r.get("size") or "").split()).upper(), " ".join(str(r.get("pattern") or "").split())
        try:
            price = float(str(r.get("price") or "").replace(",", ""))
        except ValueError:
            return False, f"Row {n} ({size}): '{r.get('price')}' is not a price."
        if not size and not pattern:
            continue
        if not size or not pattern or price <= 0:
            return False, f"Row {n}: give the size, the pattern and a price above zero (or clear the whole row)."
        values = {"size": size, "pattern": pattern}
        ply = "".join(str(r.get("ply") or "").split()).upper()
        if ply:
            values["ply"] = ply
        if tube:
            values["tube"] = tube
        clean.append({"category": "Tires", "row": n, "brand": brand, "values": values, "item_remark": "", "supplier_code": "", "unit": "piece",
                      "list_price": price, "effective_date": eff, "remarks": "", "name": pc.standard_name("Tires", brand, **values)})
    if not clean:
        return False, "No rows to save."
    if discount.strip() and parse_chain(discount) is None:
        return False, f"'{discount}' is not a discount. Type it like 25+5 or 28."
    ok, msg, counts = import_workbook(int(cid), {"rows": clean, "problems": [], "notes": [], "counts": {"Tires": len(clean)}, "suppliers": []},
                                      on_duplicate="replace" if replace else "skip")
    if not ok:
        return False, msg
    if discount.strip():
        dok, dmsg = set_discount(int(cid), brand if discount_brand_only else "", discount, eff)
        msg += " " + dmsg
        if not dok:
            return False, msg
    shutil.rmtree(d, ignore_errors=True)
    return True, msg
