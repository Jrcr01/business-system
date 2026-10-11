"""The revenue cycle stays on the sandbox (owner, 2026-10-11: "no plan for deploying revenue cycle keep it in sandbox for the meantime"): off the sandbox
(engines/features.py) Stock and Sell are gone everywhere -- menu, Home, their addresses, the person's access rows, Settings, What's new, the DOT box and
the label link -- while purchase orders and receiving work as before. On the sandbox everything is there."""
import pytest

from tests.test_web_d0 import _admin, _sign_in_new, fetchone_uid
from tests.test_web_po_request import _arrive, _head, _ordered
from tests.test_web_see_and_do import levels_post

ADDRESSES = ["/stock/", "/stock/labels/?receipt=1", "/stock/1/", "/stock-counts/", "/stock-counts/1/", "/affiliates/", "/affiliates/1/", "/sales/",
             "/sales/1/", "/customers/", "/si-booklets/", "/stock/items.json"]
LINKS = ['href="/stock/', 'href="/stock-counts/', 'href="/affiliates/', 'href="/sales/', 'href="/customers/', 'href="/si-booklets/']


@pytest.fixture
def off(monkeypatch):
    monkeypatch.setenv("WEB_REVENUE_CYCLE", "0")


def test_off_the_pages_are_gone_for_the_system_admin_too(web, db, off):
    _admin(web)
    for url in ADDRESSES:
        assert web.get(url).status_code == 403, url
    assert web.post("/stock/learn/", {"barcode": "4801234567890", "item_id": "1"}).status_code == 403
    assert web.post("/sales/", {"action": "save", "si_no": "1"}).status_code == 403
    home = web.get("/").content.decode()
    for link in LINKS:
        assert link not in home, link
    assert "Scan out" not in home and "Items and stock" not in home and "New: Stock and Sell" not in home
    assert 'href="/purchase-orders/' in home                                     # the rest of the system is as before


def test_off_no_access_rows_and_the_switches_keep_their_setting(web, db, off):
    from engines import auth_service as au
    _admin(web)
    _sign_in_new("family@slicks.test", "owner")
    uid = fetchone_uid("family@slicks.test")
    assert au.set_access(uid, {"stock"}, {"sales.use"})[0]
    page = web.get(f"/settings/users/{uid}/").content.decode()
    assert 'name="lv_stock"' not in page and 'name="lv_sales"' not in page and 'name="lv_purchase-orders"' in page
    r = web.post(f"/settings/users/{uid}/", levels_post({"lenders": "see"}), follow=True)
    assert b"saved" in r.content
    assert "stock" in au.get_hidden_pages(uid)                                   # still hidden, though the form had no row for it
    off_now = au.get_restrictions(uid)
    assert "sales.use" in off_now and "loans.use" in off_now and "inventory.use" not in off_now


def test_off_settings_and_whats_new_say_nothing_of_stock(web, db, off):
    from engines.whats_new import latest_id, unseen
    _admin(web)
    assert "stock_old_years" not in web.get("/settings/").content.decode()
    assert latest_id() != "2026-10-13a" and "2026-10-13a" not in [n["id"] for n in unseen(None)]
    assert "2026-10-12a" in [n["id"] for n in unseen(None)]                      # the purchase order note is still told


def test_off_receiving_still_records_the_goods_and_the_payable(web, db, off):
    _admin(web)
    cid, po, lid = _ordered(db)
    page = web.get(f"/purchase-orders/receive/?s={cid}&po={po}").content.decode()
    assert "Goods still to come" in page and f'name="dot_{lid}"' not in page and ">DOT</th>" not in page
    _arrive(web, cid, lid, 2, dr="DR-9")
    assert _head(po)["status"] == "RECEIVED"
    order = web.get(f"/purchase-orders/{po}/").content.decode()
    assert "DR 9" in order and "Print labels" not in order


def test_on_the_sandbox_everything_is_there(web, db):
    from engines.features import revenue_on
    from engines.whats_new import latest_id
    _admin(web)
    assert revenue_on() and latest_id() >= "2026-10-13a"
    assert web.get("/stock/").status_code == 200 and web.get("/sales/").status_code == 200
    home = web.get("/").content.decode()
    for link in LINKS:
        assert link in home, link
    assert "stock_old_years" in web.get("/settings/").content.decode()
    cid, po, lid = _ordered(db)
    assert f'name="dot_{lid}"' in web.get(f"/purchase-orders/receive/?s={cid}&po={po}").content.decode()
