"""Before the opening count (owner, 2026-10-18: "reconcile later. Important for now the system can be used"): sales still take the stock, but no below-zero
flag is raised and the stock pages say "not counted yet"."""
from conftest import fetchone
from tests.test_web_d0 import _admin
from tests.test_web_stock import _item


def test_a_sale_of_uncounted_stock_raises_no_flag_and_reads_not_counted_yet(web, db):
    from engines.clock import manila_today
    from engines.sales_rules import register_booklet, save_sale
    from engines.settings_registry import stock_not_counted
    _admin(web)
    assert stock_not_counted()                                                               # on by default
    a, _ = _item("Westlake", "185R14", "SC328")
    register_booklet(4501, 4550, "Julian")
    ok, msg, _ = save_sale(4501, manila_today(), None, "Walk-in", [{"item_id": a, "qty": 4, "price": 3000}], [], [{"method": "CASH", "amount": 12000}])
    assert ok, msg
    assert fetchone(db, "SELECT on_hand FROM items WHERE id = ?", (a,))[0] == -4                # the stock is still taken
    assert fetchone(db, "SELECT COUNT(*) FROM review_flags WHERE kind = 'STOCK_NEGATIVE'")[0] == 0
    page = web.get(f"/stock/{a}/").content.decode()
    assert "not counted yet" in page and "-4" not in page.split("On hand")[1][:200]
    assert "Stock is not counted yet" in web.get("/stock/").content.decode()
