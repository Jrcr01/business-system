"""The price-list Excel template: sheets and dropdowns, a filled file read row by row with problems named by sheet and row, the comparison, and the import."""
import io
from datetime import date

from openpyxl import load_workbook

from conftest import fetchone, make_creditor
from engines import price_template_service as pt
from engines import product_catalog as pc
from engines.price_list_service import price_on

DAY = date(2026, 9, 15)


def _fill(rows: dict[str, list[list]], creditor_id=None) -> bytes:
    """Starts from the real template (so headers and sheets are as downloaded) and writes rows under the headers of each sheet."""
    wb = load_workbook(io.BytesIO(pt.build_template(creditor_id, layout="by_category")))       # the older workbook, still read (the standard sheet: test_price_template_standard.py)
    for cat, data in rows.items():
        ws = wb[cat]
        header = [str(c.value).replace(" *", "") for c in ws[1]]
        for r, rowvals in enumerate(data, start=ws.max_row + 1):
            for key, val in rowvals.items():
                ws.cell(row=r, column=header.index(key) + 1, value=val)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


GOOD = {"Tires": [{"Brand": "Westlake", "Size": "285/50R20", "Terrain": "A/T", "Pattern": "SL369", "Supplier item code": "WL-1", "Unit": "pc", "List price": 7600, "Effective date": DAY},
                  {"Brand": "Westlake", "Size": "305/35R24", "Pattern": "SA57", "List price": "8,400", "Effective date": "2026-09-15"}],
        "Battery": [{"Brand": "Motolite", "Model / group": "NS60", "Volts": 12, "Ah": 45, "Terminal": "L", "List price": 4200, "Effective date": DAY}],
        "Mags": [{"Brand": "Enkei", "Model": "RPF1", "Size": "17x7.5", "PCD": "5x114.3", "Offset": "ET40", "Finish / color": "Silver", "List price": 9800, "Effective date": DAY}],
        "Rims": [{"Brand": "Westlake", "Size": "8.25x22.5", "Holes": 10, "Tube": "TL", "List price": 4150, "Effective date": DAY}]}


def test_the_template_has_a_sheet_per_category_with_the_standard_columns_and_dropdowns():
    wb = load_workbook(io.BytesIO(pt.build_template(layout="by_category")))
    assert wb.sheetnames == ["How to fill", "Tires", "Battery", "Mags", "Rims"]
    tires = [c.value for c in wb["Tires"][1]]
    assert tires == ["Brand *", "Size *", "Ply / load-speed", "Terrain", "Pattern *", "Type", "Tube", "Supplier item code", "Unit", "List price *", "Effective date", "Remarks"]
    assert [c.value for c in wb["Battery"][1]][:7] == ["Brand *", "Model / group *", "Volts", "Ah *", "CCA", "Type", "Terminal"]
    assert not wb["Tires"].data_validations.dataValidation          # no drop-down lists (owner, 2026-10-04: another AI fills it; any value is accepted)
    assert wb["Tires"].freeze_panes == "A2"


def test_a_filled_template_is_read_into_clean_rows():
    parsed = pt.parse_workbook(_fill(GOOD))
    assert parsed["problems"] == [] and len(parsed["rows"]) == 5 and parsed["counts"] == {"Tires": 2, "Battery": 1, "Mags": 1, "Rims": 1}
    names = {r["name"] for r in parsed["rows"]}
    assert {"Westlake 285/50R20 (A/T) SL369", "Motolite NS60 12V 45Ah L", "Enkei RPF1 17x7.5 5x114.3 ET40 Silver", "Westlake 8.25x22.5 10H Tubeless"} <= names
    assert next(r for r in parsed["rows"] if r["name"].endswith("SA57"))["list_price"] == 8400.0                    # '8,400' text is read


def test_every_kind_of_problem_is_named_by_sheet_and_row():
    bad = {"Tires": [{"Brand": "", "Size": "175/70R13", "Pattern": "X", "List price": 100, "Effective date": DAY},
                     {"Brand": "Apollo", "Size": "", "Pattern": "X", "List price": 100, "Effective date": DAY},
                     {"Brand": "Apollo", "Size": "1", "Pattern": "X", "Terrain": "Q/Q", "List price": 100, "Effective date": DAY},
                     {"Brand": "Apollo", "Size": "2", "Pattern": "X", "List price": "call", "Effective date": DAY},
                     {"Brand": "Apollo", "Size": "3", "Pattern": "X", "List price": 5, "Effective date": "someday"},
                     {"Brand": "Apollo", "Size": "4", "Pattern": "X", "List price": 5},
                     {"Brand": "Apollo", "Size": "5", "Pattern": "X", "List price": 5, "Effective date": DAY},
                     {"Brand": "apollo", "Size": "5", "Pattern": "x", "List price": 6, "Effective date": DAY}]}
    parsed = pt.parse_workbook(_fill(bad))
    text = "\n".join(parsed["problems"])
    for needle in ("Tires row 2: Brand is empty", "Tires row 3: Size is empty", "Tires row 5: List price 'call'",
                   "Tires row 6: Effective date 'someday'", "Tires row 7: Effective date is empty", "Tires row 9: the same product and date appears twice"):
        assert needle in text, needle
    assert [r["row"] for r in parsed["rows"]] == [4, 8]                                                            # the good rows survive (an unlisted Terrain is fine since 2026-10-04)
    assert len(pt.parse_workbook(_fill(bad), default_date=DAY)["problems"]) == len(parsed["problems"]) - 1          # a pick-date on upload fills the empty date


def test_a_damaged_or_wrong_file_is_explained():
    assert "Could not open" in pt.parse_workbook(b"not excel")["problems"][0]
    assert pt.parse_workbook(pt.build_template())["problems"] == ["The file has no product rows."]


def test_the_import_creates_products_with_skus_and_dated_prices_and_a_second_import_adds_nothing(db):
    cid = make_creditor(db, name="New Century")
    parsed = pt.parse_workbook(_fill(GOOD))
    ok, msg, counts = pt.import_workbook(cid, parsed)
    assert ok and counts["added"] == 5 and counts["products"] == 5
    assert fetchone(db, "SELECT COUNT(*) FROM items WHERE code IS NOT NULL")[0] == 5
    assert price_on(cid, "Westlake 285/50R20 (A/T) SL369", date(2026, 10, 1))["unit_price"] == 7600
    assert fetchone(db, "SELECT supplier_code FROM supplier_items WHERE creditor_id = ? AND supplier_code <> ''", (cid,))[0] == "WL-1"
    assert fetchone(db, "SELECT COUNT(*) FROM supplier_price_list WHERE item_id IS NULL")[0] == 0
    again = pt.import_workbook(cid, pt.parse_workbook(_fill(GOOD)))
    assert again[2]["added"] == 0 and again[2]["skipped"] == 5 and again[2]["products"] == 0
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'PRICE_TEMPLATE_IMPORTED'")[0] == 2


def test_the_download_with_current_prices_comes_back_as_all_same_and_one_change_is_one_new_dated_price(db):
    cid = make_creditor(db, name="New Century")
    pt.import_workbook(cid, pt.parse_workbook(_fill(GOOD)))
    prefilled = pt.build_template(cid, on=date(2026, 10, 1), layout="by_category")
    parsed = pt.parse_workbook(prefilled)
    assert parsed["problems"] == [] and len(parsed["rows"]) == 5
    assert set(pt.compare(cid, parsed)["status"]) == {"same"}
    wb = load_workbook(io.BytesIO(prefilled))
    ws = wb["Tires"]
    header = [str(c.value).replace(" *", "") for c in ws[1]]
    for r in range(2, ws.max_row + 1):
        if ws.cell(row=r, column=header.index("Pattern") + 1).value == "SL369":
            ws.cell(row=r, column=header.index("List price") + 1, value=7000)
            ws.cell(row=r, column=header.index("Effective date") + 1, value=date(2026, 10, 15))
    buf = io.BytesIO()
    wb.save(buf)
    changed = pt.parse_workbook(buf.getvalue())
    cmp = pt.compare(cid, changed)
    assert list(cmp["status"].value_counts().to_dict().items()) == [("same", 4), ("changed", 1)] and cmp[cmp["status"] == "changed"]["change_pct"].iloc[0] == -7.9
    ok, _, counts = pt.import_workbook(cid, changed)
    assert ok and counts["added"] == 1
    assert price_on(cid, "Westlake 285/50R20 (A/T) SL369", date(2026, 10, 1))["unit_price"] == 7600                  # the old price still answers for the old date
    assert price_on(cid, "Westlake 285/50R20 (A/T) SL369", date(2026, 10, 20))["unit_price"] == 7000


def test_products_the_file_no_longer_lists_are_reported_not_deleted(db):
    cid = make_creditor(db, name="New Century")
    pt.import_workbook(cid, pt.parse_workbook(_fill(GOOD)))
    smaller = pt.parse_workbook(_fill({"Tires": [GOOD["Tires"][0]]}))
    cmp = pt.compare(cid, smaller)
    missing = cmp[cmp["status"] == "missing"]
    assert list(missing["name"]) == ["Westlake 305/35R24 SA57"]                  # only inside a category the file covers: the Battery, Mags, Rims products are not 'missing'
    assert fetchone(db, "SELECT COUNT(*) FROM supplier_price_list")[0] == 5
