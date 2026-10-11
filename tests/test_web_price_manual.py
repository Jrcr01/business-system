"""Price lists -> Add a price, typed in the template's own columns per type (owner, 2026-10-09: "aligned with the template";
2026-10-11: "align this with the template" -- the category's fields, drop-downs in plain words, the same checks as the file)."""
import sqlite3

from conftest import make_creditor

import engines.db_engine as d
from tests.test_web_d0 import _admin


def _n(sql, params=()):
    with sqlite3.connect(d.DB_PATH) as conn:
        return conn.execute(sql, params).fetchone()[0]


def test_manual_entry_shows_the_categorys_own_columns(web, db):
    _admin(web)
    cid = make_creditor(db, "Battery Depot of Central Luzon")
    page = web.get(f"/price-lists/?s={cid}&tab=add").content.decode()
    for word in ('data-cat="Tires"', "Ply / load-speed", "Pattern", "Tubeless", "All-Terrain",          # the tire sheet's columns and plain words
                 'data-cat="Battery"', "Model / group", "Terminal", 'data-cat="Rims"', "Disc thickness"):
        assert word in page, word
    assert "Other specs" not in page                                                                    # the old free-text column is gone


def test_manual_entry_reads_a_row_like_the_template(web, db):
    _admin(web)
    cid = make_creditor(db, "Battery Depot of Central Luzon")
    row = {"s": cid, "action": "add_row", "category": "Battery", "f_brand": "Ultra", "f_model": "MF Plus NS40", "f_battery_type": "MF",
           "f_unit": "pc", "f_list_price": "2,060", "f_effective_date": "2026-05-01"}
    web.post("/price-lists/", row)
    web.post("/price-lists/", {**row, "f_model": "MF Plus NS60", "f_list_price": "2,710"})
    with sqlite3.connect(d.DB_PATH) as conn:
        names = sorted(r[0] for r in conn.execute("SELECT item_name FROM supplier_price_list WHERE creditor_id = ?", (cid,)))
    assert names == ["Ultra MF Plus NS40", "Ultra MF Plus NS60"]
    r = web.post("/price-lists/", {**row, "f_list_price": ""}, follow=True)
    assert "Not saved" in r.content.decode()                                         # the template's checks apply
    r = web.post("/price-lists/", {**row, "f_model": ""}, follow=True)
    assert "Not saved" in r.content.decode() and 'value="Ultra"' in r.content.decode()    # what was typed comes back


def test_a_tire_typed_with_the_full_words_lands_as_the_short_codes(web, db):
    _admin(web)
    cid = make_creditor(db, "New Century City Marketing Corp.")
    row = {"s": cid, "action": "add_row", "category": "Tires", "f_brand": "Westlake", "f_size": "265/70R16", "f_ply": "112H",
           "f_terrain": "Highway-Terrain", "f_pattern": "SL369", "f_type": "PCR", "f_tube": "Tubeless", "f_unit": "pc",
           "f_list_price": "5,900", "f_effective_date": "2026-09-15"}
    r = web.post("/price-lists/", row, follow=True)
    assert "Not saved" not in r.content.decode()
    with sqlite3.connect(d.DB_PATH) as conn:
        name, price = conn.execute("SELECT item_name, unit_price FROM supplier_price_list WHERE creditor_id = ?", (cid,)).fetchone()
    assert price == 5900.0 and "H/T" in name and "SL369" in name                        # stored as the short codes, as a file row would be
