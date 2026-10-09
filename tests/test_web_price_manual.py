"""Price lists -> Add a price, typed in the template's columns (owner, 2026-10-09)."""
def test_manual_entry_reads_a_row_like_the_template(web, db):
    # owner, 2026-10-09: "for manual entry, this should be aligned with the template"
    import sqlite3
    from conftest import make_creditor
    from tests.test_web_d0 import _admin
    import engines.db_engine as d
    _admin(web)
    cid = make_creditor(db, "Battery Depot of Central Luzon")
    page = web.get(f"/price-lists/?s={cid}&tab=add").content.decode()
    assert "Model/Pattern" in page and "Other specs" in page and "Supplier item code" in page and "Terrain" in page
    row = {"s": cid, "action": "add_row", "category": "Battery", "brand": "Ultra", "model": "MF Plus", "size": "NS40", "unit": "pc", "list_price": "2,060",
           "effective_date": "2026-05-01"}
    web.post("/price-lists/", row)
    web.post("/price-lists/", {**row, "size": "NS60", "list_price": "2,710"})
    with sqlite3.connect(d.DB_PATH) as conn:
        names = sorted(r[0] for r in conn.execute("SELECT item_name FROM supplier_price_list WHERE creditor_id = ?", (cid,)))
    assert names == ["Ultra MF Plus NS40", "Ultra MF Plus NS60"]                     # two batteries, as in the file
    r = web.post("/price-lists/", {**row, "list_price": ""}, follow=True)
    assert "Not saved" in r.content.decode()                                         # the template's checks apply
