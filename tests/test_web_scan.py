"""The scan moves the stock, the invoice is typed after close (owner, 2026-10-11: "the barcode feature will be used only to track movements of inventory
in and outs. but the invoice is done manually. we record after the store close"; "we do not need POS"): Scan out saves a slip (stock out at once, the SI
number optional), Record the day's invoices turns each slip into one invoice without taking stock again, and an invoice typed with no slip is flagged."""
from conftest import fetchone
from tests.test_web_d0 import _admin
from tests.test_web_sell import _ready, _sale


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _n(sql, params=()):
    return fetchone(_db(), sql, params)[0]


def _slip(web, a, qty="3", **extra):
    data = {"action": "slip", "customer": "", "si_hint": "", f"ln_item_0": a, f"ln_qty_0": qty, f"ln_price_0": ""}
    data.update(extra)
    return web.post("/sales/", data)


def test_done_takes_the_stock_at_once_and_needs_no_name_and_no_number(web, db):
    _admin(web)
    a, name = _ready(web)
    page = web.get("/sales/?view=scan").content.decode()
    assert "Scan out" in page and 'name="si_hint"' in page and 'data-mode="out"' in page
    assert "<th>Price each</th>" not in page and '<b id="sl_profit"' not in page   # the movement only, no money at the scan
    r = _slip(web, a)
    assert r.status_code == 302 and r["Location"] == "/sales/?view=scan"
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 9              # out the moment Done is pressed
    assert fetchone(_db(), "SELECT status, si_hint, customer_id, customer_name FROM sales") == ("RELEASED", None, None, None)      # a walk-in slip is fine
    page = web.get("/sales/?view=scan").content.decode()
    assert "RL-0001" in page and "no SI yet" in page


def test_the_si_typed_at_the_scan_is_offered_back_on_the_invoice(web, db):
    _admin(web)
    a, name = _ready(web)
    _slip(web, a, si_hint="4507", customer="Mang Ruben")
    sid = _n("SELECT sale_id FROM sales")
    assert _n("SELECT si_hint FROM sales") == "4507"
    page = web.get("/sales/").content.decode()                                 # the evening page is the sales page itself
    assert "Record the day" in page and "4507" in page and "Write the invoice" in page and "Mang Ruben" in page
    assert 'name="si_no" value="4507"' in web.get(f"/sales/{sid}/").content.decode()


def test_invoicing_a_slip_takes_no_stock_again_and_raises_no_flag(web, db):
    _admin(web)
    a, name = _ready(web)
    _slip(web, a, qty="2")
    sid = _n("SELECT sale_id FROM sales")
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 10
    lid = _n("SELECT line_id FROM sale_lines WHERE sale_id = ?", (sid,))
    r = web.post(f"/sales/{sid}/", {"action": "invoice", "si_no": "004501", "sale_date": fetchone(_db(), "SELECT sale_date FROM sales")[0],
                                    f"price_{lid}": "4800", "pay_method_0": "CASH", "pay_amount_0": "9600"})
    assert r.status_code == 302
    assert fetchone(_db(), "SELECT status, si_no, total FROM sales") == ("SAVED", 4501, 9600.0)
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 10             # the stock left with the scan, not twice
    assert _n("SELECT COUNT(*) FROM review_flags WHERE kind = 'SALE_NOT_SCANNED'") == 0


def test_an_invoice_typed_with_no_slip_is_saved_and_flagged(web, db):
    _admin(web)
    a, name = _ready(web)
    assert _sale(web, a).status_code == 302                                     # saved: flags never block
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 8              # unscanned: the stock goes with the save
    assert _n("SELECT COUNT(*) FROM review_flags WHERE kind = 'SALE_NOT_SCANNED'") == 1
    assert "without a scan slip" in fetchone(_db(), "SELECT detail FROM review_flags WHERE kind = 'SALE_NOT_SCANNED'")[0]


def test_a_service_only_sale_never_flags(web, db):
    _admin(web)
    _ready(web)
    r = web.post("/sales/", {"action": "save", "si_no": "004501", "sale_date": str(__import__("engines.clock", fromlist=["manila_today"]).manila_today()),
                             "customer": "", "svc_desc_0": "Vulcanizing", "svc_amt_0": "350", "pay_method_0": "CASH", "pay_amount_0": "350"})
    assert r.status_code == 302
    assert _n("SELECT COUNT(*) FROM review_flags WHERE kind = 'SALE_NOT_SCANNED'") == 0


def test_the_menu_and_home_lead_with_the_scan(web, db):
    _admin(web)
    _ready(web)
    home = web.get("/").content.decode()
    assert "Scan out" in home and "Record the day" in home and "Counter sale" not in home
    assert 'href="/sales/?view=scan"' in home
