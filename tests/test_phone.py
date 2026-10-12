"""The phone pages (owner, 2026-10-12: "We need pricelist, inventory. Stock in, stock out. Those items need mobility without using a laptop"):
/phone/ -- Price list, Stock, Stock in, Stock out -- the same rules as the laptop pages they mirror, the Scanner role for the staff phone, and
the switch in Settings that opens them."""
from conftest import fetchone
from tests.test_web_d0 import _admin, _person, _sign_in_new
from tests.test_web_d2b import _order_it, _start, _supplier_with_price
from tests.test_web_stock import _item, _stock


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _n(sql, params=()):
    return fetchone(_db(), sql, params)[0]


def _on():
    from engines.settings_store import set_setting
    set_setting("phone_pages", "1")


def test_off_until_the_switch_and_then_the_four_buttons(web, db):
    _admin(web)
    assert web.get("/phone/")["Location"] == "/"                                # off: the phone home goes to Home
    _on()
    page = web.get("/phone/").content.decode()
    for href in ("/phone/prices/", "/phone/stock/", "/phone/in/", "/phone/out/"):
        assert f'href="{href}"' in page
    assert 'rel="manifest"' in page and "apple-mobile-web-app-capable" in page
    r = web.get("/phone/manifest.webmanifest")
    assert r.status_code == 200 and r["Content-Type"].startswith("application/manifest+json")
    assert '"start_url": "/"' in b"".join(r.streaming_content).decode()          # the whole site is the app (owner, 2026-10-12: "can we have this")
    assert "On the phone" in web.get("/").content.decode()                     # the laptop's person menu links to it


def test_the_price_list_on_the_phone_shows_each_suppliers_net_price(web, db):
    _admin(web)
    _on()
    cid = _supplier_with_price(web, db, "Express Dymans", "4950")
    from conftest import make_creditor
    other = make_creditor(db, "New Century")
    web.post("/price-lists/", {"s": other, "action": "add_price", "item": "Eagle F1", "unit": "pc", "price": "5120", "start": "2026-01-01", "code": ""})
    web.post("/price-lists/", {"s": other, "action": "add_price", "item": "Eagle Sport", "unit": "pc", "price": "3000", "start": "2026-01-01", "code": ""})
    from engines.phone_lookup import prices
    rows = prices("eagle f1")
    assert [r["name"] for r in rows] == ["Eagle F1"]
    assert [(s["supplier"], s["net"]) for s in rows[0]["suppliers"]] == [("Express Dymans", 4950.0), ("New Century", 5120.0)]      # cheapest first
    assert prices("e") == [] and len(prices("eagle")) == 2
    web.get("/phone/")                                                          # the "saved" notes of the prices added above are shown once, here
    page = web.get("/phone/prices/?q=eagle+f1").content.decode()
    assert "4,950.00" in page and "5,120.00" in page and "Eagle Sport" not in page
    assert "Type a size" in web.get("/phone/prices/").content.decode()


def test_stock_on_the_phone_finds_by_code_searches_and_teaches_a_barcode(web, db):
    _admin(web)
    _on()
    a, name = _item()
    _stock(a, 6, dot="1226")
    code = _n("SELECT code FROM items WHERE id = ?", (a,))
    page = web.get(f"/phone/stock/?q={code}").content.decode()
    assert f"Found: {code}" in page and name in page and ">6<" in page and "12/2026" in page
    assert "2,840" not in page and "Cost and value stay on the laptop" in page   # the cost figure never reaches the phone
    page = web.get("/phone/stock/?q=2055516").content.decode()                  # a search: the list, tap one
    assert name in page and f'href="/phone/stock/?id={a}"' in page
    r = web.post("/phone/stock/", {"action": "barcode", "item_id": a, "barcode": "4712345678901"})
    assert r["Location"] == f"/phone/stock/?id={a}"
    assert _n("SELECT COUNT(*) FROM item_barcodes WHERE item_id = ? AND barcode = '4712345678901'", (a,)) == 1
    page = web.get("/phone/stock/?q=4712345678901").content.decode()
    assert f"Found: {code}" in page and "Barcodes known" in page
    assert "Nothing matches" in web.get("/phone/stock/?q=zzz").content.decode()


def test_stock_in_on_the_phone_is_the_same_delivery_as_receive(web, db):
    _admin(web)
    _on()
    cid = _supplier_with_price(web, db)
    po_id = _start(web, cid, qty="10")
    _order_it(web, po_id)
    page = web.get("/phone/").content.decode()
    assert "1 order waiting" in page
    page = web.get("/phone/in/").content.decode()                               # one order waiting: it is the one, already open
    assert "Eagle F1" in page and 'name="dr"' in page and "Received:" in page
    lid = _n("SELECT line_id FROM purchase_order_lines WHERE po_id = ?", (po_id,))
    r = web.post("/phone/in/", {"po": po_id, f"qty_{lid}": "10", f"dot_{lid}": "3626", "dr": "77210", "doc": "", "inv_date": "2026-10-12"})
    assert r.status_code == 302 and r["Location"] == "/phone/in/"
    assert fetchone(_db(), "SELECT status FROM purchase_orders WHERE po_id = ?", (po_id,)) == ("RECEIVED",)
    assert _n("SELECT COUNT(*) FROM po_receipts WHERE po_id = ?", (po_id,)) == 1
    assert fetchone(_db(), "SELECT doc_ref, dr_no, amount FROM invoices WHERE creditor_id = ?", (cid,)) == ("DR 77210", "77210", 10000.0)      # the DR is the payable
    assert _n("SELECT dot FROM po_receipt_lines WHERE line_id = ?", (lid,)) == "3626"
    assert "No order is waiting" in web.get("/phone/in/").content.decode()
    r = web.post("/phone/in/", {"po": po_id, f"qty_{lid}": "1", "dr": "1"})
    assert r.status_code == 302                                                 # an order already received: nothing to post to, no crash


def test_a_short_delivery_asks_first_then_keeps_the_order_open(web, db):
    _admin(web)
    _on()
    cid = _supplier_with_price(web, db)
    po_id = _start(web, cid, qty="10")
    _order_it(web, po_id)
    lid = _n("SELECT line_id FROM purchase_order_lines WHERE po_id = ?", (po_id,))
    r = web.post("/phone/in/", {"po": po_id, f"qty_{lid}": "4", "dr": "5", "doc": "", "inv_date": "2026-10-12"})
    page = r.content.decode()
    assert r.status_code == 200 and "not arrived" in page and 'name="rest" value="open"' in page
    assert _n("SELECT COUNT(*) FROM po_receipts") == 0                          # nothing saved before the answer
    r = web.post("/phone/in/", {"po": po_id, f"qty_{lid}": "4", "dr": "5", "doc": "", "inv_date": "2026-10-12", "rest": "open", "s": cid})
    assert r.status_code == 302 and fetchone(_db(), "SELECT status FROM purchase_orders WHERE po_id = ?", (po_id,)) == ("PARTIAL",)


def test_stock_out_on_the_phone_makes_the_slip_with_the_scan_switch_off_and_undoes_it(web, db):
    _admin(web)
    _on()
    a, name = _item()
    _stock(a, 12)
    from engines.settings_registry import sales_scan_on
    assert not sales_scan_on()                                                  # the laptop's scan switch is off: the phone still works
    page = web.get("/phone/out/").content.decode()
    assert 'data-mode="out"' in page and "Done" in page and "No slip yet today" in page
    r = web.post("/phone/out/", {"action": "slip", "customer": "", "si_hint": "", "ln_item_0": a, "ln_qty_0": "3", "ln_price_0": ""})
    assert r.status_code == 302 and r["Location"] == "/phone/out/"
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 9              # out the moment Done is pressed
    assert fetchone(_db(), "SELECT status, release_no FROM sales") == ("RELEASED", "RL-0001")
    page = web.get("/phone/out/").content.decode()
    assert "RL-0001" in page and "no SI yet" in page and "1 slip today" in web.get("/phone/").content.decode()
    sale_id = _n("SELECT sale_id FROM sales")
    r = web.post("/phone/out/", {"action": "undo", "sale_id": sale_id})
    assert r.status_code == 302 and _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 12
    assert _n("SELECT status FROM sales WHERE sale_id = ?", (sale_id,)) != "RELEASED"


def test_the_scanner_role_lives_on_the_phone_and_never_sees_prices(web, db):
    _admin(web)
    _on()
    _item()
    _supplier_with_price(web, db)
    staff = _sign_in_new("warehouse@slicks.test", "scanner")
    assert staff.get("/")["Location"] == "/phone/"                              # Home sends it to the phone
    assert staff.get("/stock/")["Location"] == "/phone/"                        # so does every laptop page
    assert staff.get("/order-statements/")["Location"] == "/phone/"
    page = staff.get("/phone/").content.decode()
    assert 'href="/phone/stock/"' in page and 'href="/phone/in/"' in page and 'href="/phone/out/"' in page
    assert 'href="/phone/prices/"' not in page and "Open the laptop site" not in page
    assert staff.get("/phone/prices/").status_code == 403
    assert staff.get("/stock/items.json").status_code == 200                    # the scan box's list still loads
    assert staff.get("/phone/stock/").status_code == 200 and staff.get("/phone/out/").status_code == 200 and staff.get("/phone/in/").status_code == 200
    from engines.permissions import ROLE_LABEL, ROLES
    assert "scanner" in ROLES and ROLE_LABEL["scanner"] == "Scanner (phone)"


def test_a_page_hidden_from_a_person_is_hidden_on_the_phone_too(web, db):
    _admin(web)
    _on()
    _supplier_with_price(web, db)
    temp = _person("co@slicks.test", "owner")
    from engines.auth_service import change_password
    from engines.common import get_conn
    from tests.test_web_d0 import PW, fetchone_uid, login
    uid = fetchone_uid("co@slicks.test")
    change_password(uid, temp, PW)
    with get_conn() as conn:
        conn.execute("INSERT INTO user_hidden_pages (user_id, page_id) VALUES (?, 'price-lists')", (uid,))
        conn.commit()
    import engines.permissions as perm
    perm._HIDDEN.clear()                                                        # written behind the Settings page's back: forget what was read
    from django.test import Client
    co = Client()
    assert login(co, "co@slicks.test")["Location"] == "/"
    page = co.get("/phone/").content.decode()
    assert 'href="/phone/prices/"' not in page and 'href="/phone/stock/"' in page
    assert co.get("/phone/prices/").status_code == 403


def test_the_site_is_installable_from_any_page_and_the_service_worker_is_served(web, db):
    # owner, 2026-10-12: Chrome on the phone said "This app cannot be installed" on the sign-in page: no manifest there, no service worker anywhere
    _admin(web)
    assert 'rel="manifest"' in web.get("/").content.decode()
    web.post("/logout/")
    page = web.get("/login/").content.decode()                                  # the sign-in page too: Install is pressed there
    assert 'rel="manifest" href="/phone/manifest.webmanifest"' in page and "serviceWorker" in page
    r = web.get("/sw.js")
    assert r.status_code == 200 and r["Content-Type"].startswith("application/javascript") and r["Service-Worker-Allowed"] == "/"
    assert b"addEventListener(\"fetch\"" in b"".join(r.streaming_content)
    r = web.get("/phone/manifest.webmanifest")
    r2 = b"".join(r.streaming_content).decode(); r = web.get("/phone/manifest.webmanifest")
    assert '"id": "/"' in b"".join(r.streaming_content).decode() and '"scope": "/"' in r2


def test_the_phone_pages_are_their_own_app_beside_the_whole_site(web, db):
    # owner, 2026-10-12: "Can we have seperate shortcut for on the phone? This will be used primarily for operations"
    _admin(web)
    _on()
    r = web.get("/phone/manifest-ops.webmanifest")
    ops = b"".join(r.streaming_content).decode()
    assert r.status_code == 200 and '"id": "/phone/"' in ops and '"scope": "/phone/"' in ops and "Slick's Ops" in ops and "icon-ops-192" in ops
    whole = b"".join(web.get("/phone/manifest.webmanifest").streaming_content).decode()
    assert '"id": "/"' in whole                                                 # two ids: Chrome installs them as two apps
    assert 'href="/phone/manifest-ops.webmanifest"' in web.get("/phone/").content.decode()
    assert 'href="/phone/manifest.webmanifest"' in web.get("/").content.decode()
    web.post("/logout/")
    assert web.get("/phone/manifest-ops.webmanifest").status_code == 200       # readable before sign-in, like the other one
