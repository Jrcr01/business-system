"""The one standard price-list sheet (owner, 2026-10-08): Category, Brand, Model/Pattern, Size, Other specs, Unit, Supplier item code, List price, Effective date,
for every product. Other specs is read into the category's fields; what is left over stays with the item so it still tells products apart. Older files still work."""
import io
from datetime import date

from openpyxl import load_workbook

from conftest import fetchone, make_creditor
from engines import price_template_service as pt
from engines import product_catalog as pc
from engines.price_list_service import price_on

DAY = date(2026, 9, 15)
H = ["Category", "Brand", "Model/Pattern", "Size", "Other specs", "Unit", "Supplier item code", "List price", "Effective date"]
GOOD = [["Tires", "Westlake", "SL369", "285/50R20", "A/T", "pc", "WL-1", 7600, DAY],
        ["tire", "Westlake", "SA57", "305/35R24", "", "pc", "", "8,400", "2026-09-15"],
        ["Battery", "Motolite", "NS60", "", "12V 45Ah L", "pc", "", 4200, DAY],
        ["Mags", "Enkei", "RPF1", "17x7.5", "5x114.3 ET40 Silver", "pc", "", 9800, DAY],
        ["Rims", "Westlake", "", "8.25x22.5", "10H TL", "pc", "", 4150, DAY]]


def _file(rows, creditor_id=None) -> bytes:
    wb = load_workbook(io.BytesIO(pt.build_template(creditor_id)))
    ws = wb[pt.SHEET]
    for r, vals in enumerate(rows, start=ws.max_row + 1):
        for j, v in enumerate(vals, start=1):
            ws.cell(row=r, column=j, value=v)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_the_template_is_one_sheet_with_the_standard_columns():
    wb = load_workbook(io.BytesIO(pt.build_template()))
    assert wb.sheetnames == ["How to fill", "Price list"]
    assert [str(c.value).replace(" *", "") for c in wb["Price list"][1]] == H


def test_rows_are_read_into_the_same_products_as_the_older_sheets():
    parsed = pt.parse_workbook(_file(GOOD))
    assert parsed["problems"] == [] and len(parsed["rows"]) == 5
    names = {r["name"] for r in parsed["rows"]}
    assert {"Westlake 285/50R20 (A/T) SL369", "Westlake 305/35R24 SA57", "Motolite NS60 12V 45Ah L", "Enkei RPF1 17x7.5 5x114.3 ET40 Silver",
            "Westlake 8.25x22.5 10H Tubeless"} == names
    bat = next(r for r in parsed["rows"] if r["category"] == "Battery")
    assert bat["values"]["ah"] == "45" and bat["values"]["volts"] == "12" and bat["values"]["terminal"] == "L"


def test_the_same_product_is_found_ignoring_spaces_dashes_and_capitals():
    parsed = pt.parse_workbook(_file([GOOD[0], ["TIRES", "west-lake", "sl 369", "285/50 R20", "a/t", "pc", "", 7700, DAY]]))
    assert any("the same product and date appears twice" in p for p in parsed["problems"])


def test_specs_not_recognised_stay_with_the_item_and_tell_it_apart():
    vals, rest = pc.split_other_specs("Tires", "SL369", "285/50R20", "10PR A/T LT TL Whitewall")
    assert vals["ply"] == "10PR" and vals["terrain"] == "A/T" and vals["type"] == "LT" and vals["tube"] == "TL" and rest == "Whitewall"
    a = pc.product_key("Tires", "Westlake", rest, **vals)
    b = pc.product_key("Tires", "Westlake", "", **vals)
    assert a != b


def test_problems_are_named_by_row():
    bad = [["Shoes", "Nike", "X", "1", "", "", "", 5, DAY],
           ["Battery", "Motolite", "NS60", "", "12V L", "", "", 5, DAY],
           ["Mags", "Enkei", "RPF1", "17x7.5", "Silver", "", "", 5, DAY],
           ["Tires", "", "SL369", "1", "", "", "", "call", DAY]]
    text = "\n".join(pt.parse_workbook(_file(bad))["problems"])
    for needle in ("Price list row 2: Category 'Shoes'", "Price list row 3: Ah is empty (put it in Other specs)", "Price list row 4: PCD is empty",
                   "Price list row 5: Brand is empty; List price 'call'"):
        assert needle in text, needle


def test_import_then_the_prefilled_download_comes_back_the_same(db):
    cid = make_creditor(db, name="New Century")
    ok, msg, counts = pt.import_workbook(cid, pt.parse_workbook(_file(GOOD)))
    assert ok and counts["added"] == 5 and counts["products"] == 5
    assert price_on(cid, "Motolite NS60 12V 45Ah L", date(2026, 10, 1))["unit_price"] == 4200
    again = pt.parse_workbook(pt.build_template(cid, on=date(2026, 10, 1)))
    assert again["problems"] == [] and len(again["rows"]) == 5 and set(pt.compare(cid, again)["status"]) == {"same"}


def test_an_older_file_with_a_sheet_per_category_still_imports(db):
    cid = make_creditor(db, name="New Century")
    wb = load_workbook(io.BytesIO(pt.build_template(layout="by_category")))
    ws = wb["Battery"]
    header = [str(c.value).replace(" *", "") for c in ws[1]]
    for k, v in {"Brand": "Motolite", "Model / group": "NS60", "Volts": 12, "Ah": 45, "Terminal": "L", "List price": 4200, "Effective date": DAY}.items():
        ws.cell(row=2, column=header.index(k) + 1, value=v)
    buf = io.BytesIO()
    wb.save(buf)
    old = pt.parse_workbook(buf.getvalue())
    assert old["problems"] == [] and pt.import_workbook(cid, old)[0]
    new = pt.parse_workbook(_file([GOOD[2]]))
    assert set(pt.compare(cid, new)["status"]) == {"same"}                       # the same product, whichever file it came from
    assert fetchone(db, "SELECT COUNT(*) FROM items")[0] == 1


def test_rekey_recomputes_keys_with_every_spec_and_never_merges(db):
    from engines.common import write_tx
    cid = make_creditor(db, name="New Century")
    pt.import_workbook(cid, pt.parse_workbook(_file(GOOD)))
    with write_tx() as conn:                                                   # as an item saved before every spec counted
        conn.execute("UPDATE items SET key = 'battery|motolite|ns60|l' WHERE brand = 'Motolite'")
    res = pc.rekey_items()
    assert res == {"changed": 1, "duplicates": []}
    assert fetchone(db, "SELECT key FROM items WHERE brand = 'Motolite'")[0] == "battery|motolite|ns60|l"   # only a report without --apply
    assert pc.rekey_items(apply=True)["changed"] == 1 and pc.rekey_items()["changed"] == 0
    assert "45" in fetchone(db, "SELECT key FROM items WHERE brand = 'Motolite'")[0]
