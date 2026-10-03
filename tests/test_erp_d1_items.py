"""Django-only move, step D1 (2026-10-02): ONE item list. Price lists, purchase orders, stock and sales all point at the `items` table:
  * an item made by the business rules (a price-list import) is the Django inventory.Item, with its code, standard name and specs;
  * an item added in Django (Items and stock) is found by the price-list rules, so a price lands on the SAME item;
  * Rims are a category; their code is RM-nnnn;
  * a supplier's price points at the item and the supplier's own code is remembered."""
import sqlite3
from datetime import date

import pytest

pytest.importorskip("django", reason="Django is installed only in the ERP environment (.venv)")


def _conn():
    from django.conf import settings

    return sqlite3.connect(settings.DATABASES["default"]["NAME"])


def _supplier(name):
    c = _conn()
    cid = c.execute("INSERT INTO creditors (creditor_name, creditor_type, terms) VALUES (?, 'SUPPLIER', '30')", (name,)).lastrowid
    c.commit()
    c.close()
    return cid


def _row(fields):
    """A parsed price-template row (as engines/price_template_service.parse_workbook gives it)."""
    from engines import product_catalog as pc

    category, brand, values, price = fields
    return {"category": category, "row": 2, "brand": brand, "values": values, "supplier_code": "SC-1", "unit": "pc", "list_price": price,
            "effective_date": date(2026, 9, 15), "remarks": "", "name": pc.standard_name(category, brand, **values)}


def test_a_price_list_import_makes_django_items_with_codes_names_and_specs(erp):
    from engines import price_template_service as pt
    from inventory.models import Item

    cid = _supplier("D1 Supplier A")
    parsed = {"rows": [_row(("Tires", "D1Westlake", {"size": "285/50R20", "terrain": "a/t", "pattern": "SL369"}, 7600.0)),
                       _row(("Rims", "D1Westlake", {"size": "8.25x22.5", "holes": "10", "tube": "TL"}, 4150.0))], "counts": {"Tires": 1, "Rims": 1}, "problems": []}
    ok, msg, counts = pt.import_workbook(cid, parsed)
    assert ok and counts["products"] == 2
    tire = Item.objects.get(brand="D1Westlake", category="tire")
    assert tire.code.startswith("TR-") and tire.name == "D1Westlake 285/50R20 (A/T) SL369" and tire.size_model == "285/50R20"
    assert tire.specs == {"pattern": "SL369", "terrain": "A/T"} and tire.on_hand == 0 and tire.description == tire.name
    rim = Item.objects.get(brand="D1Westlake", category="rims")
    assert rim.code.startswith("RM-") and rim.name == "D1Westlake 8.25x22.5 10H Tubeless"
    c = _conn()
    assert c.execute("SELECT item_id FROM supplier_price_list WHERE creditor_id = ? AND unit_price = 7600", (cid,)).fetchone()[0] == tire.pk
    assert c.execute("SELECT supplier_code FROM supplier_items WHERE creditor_id = ? AND item_id = ?", (cid, tire.pk)).fetchone()[0] == "SC-1"


def test_an_item_added_in_django_is_the_one_the_price_list_finds(erp):
    from engines import price_template_service as pt
    from inventory import stock
    from inventory.models import Item

    mine = stock.add_item("tire", "D1Giti", "205/55R16")                                   # typed on the Items and stock page: size only
    assert mine.code.startswith("TR-") and mine.name == "D1Giti 205/55R16" and mine.size_model == "205/55R16"
    cid = _supplier("D1 Supplier B")
    ok, _, counts = pt.import_workbook(cid, {"rows": [_row(("Tires", "D1Giti", {"size": "205/55R16"}, 3900.0))], "counts": {"Tires": 1}, "problems": []})
    assert ok and counts["products"] == 0                                                   # the SAME item: nothing new was made
    assert Item.objects.filter(brand="D1Giti").count() == 1
    c = _conn()
    assert c.execute("SELECT item_id FROM supplier_price_list WHERE creditor_id = ?", (cid,)).fetchone()[0] == mine.pk


def test_rims_can_be_added_and_counted_like_the_other_categories(erp):
    from inventory import stock
    from inventory.models import Category

    rim = stock.add_item(Category.RIMS, "D1Rimco", "9.00x22.5")
    assert rim.code.startswith("RM-") and rim.category == "rims"
    with pytest.raises(stock.StockError, match="already on the list"):
        stock.add_item("rims", " d1rimco ", "9.00x22.5")


def test_the_item_name_and_the_price_list_agree_for_a_discontinued_item(erp):
    from engines import product_catalog as pc
    from inventory.models import Item

    pid, name, _ = pc.get_or_create_product("Battery", "D1Motolite", model="NS60", ah="45", terminal="L")
    assert pc.set_product_active(pid, False)[0]
    assert Item.objects.get(pk=pid).is_active is False and Item.objects.get(pk=pid).code.startswith("BA-")
