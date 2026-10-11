"""Stage D2b of the Django rebuild (Purchase orders, docs/mockups/d2_buying.html screens 4-8): an order made and saved as it is built, ordered, its PDF, received,
its invoice matched into a payables batch, and followed to the bank; the daily-limit warning and its lighter day; two people never overwriting each other;
each role seeing only what it may do."""
from tests.conftest import fetchone, make_creditor
from tests.test_web_d0 import _admin, _sign_in_new


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _supplier_with_price(web, db, name="Goodyear Philippines", price="1000"):
    cid = make_creditor(db, name)
    web.post("/price-lists/", {"s": cid, "action": "add_price", "item": "Eagle F1", "unit": "pc", "price": price, "start": "2026-01-01", "code": ""})
    return cid


def _start(web, cid, qty="10", **extra):
    data = {"s": cid, "action": "start", "order_date": "2026-10-01", "delivery": "2026-10-08", "item": "Eagle F1", "qty": qty, "unit": "", "list_price": "", "chain": ""}
    data.update(extra)
    r = web.post("/purchase-orders/new/", data)
    assert r.status_code == 302 and r["Location"].endswith("/edit/"), r.content[:500]
    return int(r["Location"].split("/")[2])


def _stamp(po_id):
    from engines.po_rules import po_stamp
    return po_stamp(po_id)


def _order_it(web, po_id):
    """Owner, 2026-10-11: sent to the supplier, then confirmed in full -- only then is it a purchase order."""
    web.post(f"/purchase-orders/{po_id}/edit/", {"action": "request", "stamp": _stamp(po_id), "order_date": "2026-10-01", "delivery": "2026-10-08", "notes": "", "confirm_over": "1"})
    return web.post(f"/purchase-orders/{po_id}/confirm/", {"stamp": _stamp(po_id), "delivery": "2026-10-08", "confirm_over": "1"})


def test_new_order_takes_the_price_list_and_is_saved_as_a_draft(web, db):
    _admin(web)
    cid = _supplier_with_price(web, db)
    po_id = _start(web, cid)
    assert fetchone(_db(), "SELECT status FROM purchase_orders WHERE po_id = ?", (po_id,))[0] == "DRAFT"
    assert fetchone(_db(), "SELECT est_unit_price, est_amount FROM purchase_order_lines WHERE po_id = ?", (po_id,)) == (1000.0, 10000.0)
    page = web.get(f"/purchase-orders/{po_id}/edit/").content.decode()
    assert "Draft, saved" in page and "10,000.00" in page and "Payables when this order falls due" in page


def test_lines_are_added_changed_and_removed(web, db):
    _admin(web)
    cid = _supplier_with_price(web, db)
    po_id = _start(web, cid)
    web.post(f"/purchase-orders/{po_id}/edit/", {"action": "add", "stamp": _stamp(po_id), "new_item": "Valve caps", "new_qty": "4", "new_unit": "set", "new_list_price": "50", "new_chain": ""})
    assert fetchone(_db(), "SELECT COUNT(*) FROM purchase_order_lines WHERE po_id = ?", (po_id,))[0] == 2
    web.post(f"/purchase-orders/{po_id}/edit/", {"action": "update", "stamp": _stamp(po_id), "qty_0": "12", "list_price_0": "1,000.00", "chain_0": "10", "unit_0": "pc",
                                                 "qty_1": "4", "list_price_1": "50", "chain_1": "0", "unit_1": "set"})
    assert fetchone(_db(), "SELECT qty, est_unit_price FROM purchase_order_lines WHERE po_id = ? AND item_name = 'Eagle F1'", (po_id,)) == (12.0, 900.0)
    web.post(f"/purchase-orders/{po_id}/edit/", {"remove": "1", "stamp": _stamp(po_id)})
    assert fetchone(_db(), "SELECT COUNT(*) FROM purchase_order_lines WHERE po_id = ?", (po_id,))[0] == 1
    r = web.post(f"/purchase-orders/{po_id}/edit/", {"remove": "0", "stamp": _stamp(po_id)}, follow=True)
    assert b"An order needs at least one item" in r.content                    # the last item cannot go: cancel instead


def test_two_people_on_the_same_draft_never_overwrite_each_other(web, db):
    _admin(web)
    cid = _supplier_with_price(web, db)
    po_id = _start(web, cid)
    seen = _stamp(po_id)
    other = _sign_in_new("brother@slicks.test", "owner")
    other.post(f"/purchase-orders/{po_id}/edit/", {"action": "update", "stamp": seen, "qty_0": "20", "list_price_0": "1000", "chain_0": "", "unit_0": "pc"})
    r = web.post(f"/purchase-orders/{po_id}/edit/", {"action": "update", "stamp": seen, "qty_0": "5", "list_price_0": "1000", "chain_0": "", "unit_0": "pc"}, follow=True)
    assert b"Someone else changed this order after you opened it" in r.content
    assert fetchone(_db(), "SELECT qty FROM purchase_order_lines WHERE po_id = ?", (po_id,))[0] == 20


def test_the_daily_limit_warns_and_offers_a_lighter_day(web, db):
    _admin(web)
    web.post("/settings/", {"action": "save", "key": "po_daily_payables_limit", "value": "5000"})
    cid = _supplier_with_price(web, db)
    po_id = _start(web, cid)                                                    # 10,000 on one day, over 5,000
    page = web.get(f"/purchase-orders/{po_id}/edit/").content.decode()
    assert "High payables on" in page and "over your daily limit" in page
    assert "Move the order to another week" in page
    # owner, 2026-10-12: the month only -- no Daily / Weekly switch, no bars, even when the old address is typed
    old = web.get(f"/purchase-orders/{po_id}/edit/?view=weekly").content.decode()
    assert 'class="bars"' not in old and ">Weekly<" not in old and ">Daily<" not in old and "Payables by pay day" in old


def test_order_pdf_receive_match_and_follow_it_to_the_bank(web, db):
    _admin(web)
    cid = _supplier_with_price(web, db)
    po_id = _start(web, cid)
    _order_it(web, po_id)
    po_no, status = fetchone(_db(), "SELECT po_no, status FROM purchase_orders WHERE po_id = ?", (po_id,))
    assert status == "ORDERED" and " - PO " in po_no
    pdf = web.get(f"/purchase-orders/{po_id}/pdf/")
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf" and pdf.content[:4] == b"%PDF"
    line_id = fetchone(_db(), "SELECT line_id FROM purchase_order_lines WHERE po_id = ?", (po_id,))[0]
    page = web.get(f"/purchase-orders/receive/?s={cid}&po={po_id}").content.decode()
    assert f'name="pick_{line_id}" value="1" checked' in page                  # the order's lines arrive ticked
    web.post("/purchase-orders/receive/", {"s": cid, "action": "goods", f"pick_{line_id}": "1", f"qty_{line_id}": "10", "kind": "delivery receipt", "ref": "77", "received": "2026-10-03"})
    assert fetchone(_db(), "SELECT status FROM purchase_orders WHERE po_id = ?", (po_id,))[0] == "RECEIVED"
    web.post("/purchase-orders/match/", {"s": cid, "action": "match", f"pick_{line_id}": "1", f"qty_{line_id}": "10", f"price_{line_id}": "1,050.00", "doc": "INV-88",
                                         "inv_date": "2026-10-03", "override": "", "batch": "new"})
    inv = fetchone(_db(), "SELECT amount, statement_id FROM invoices WHERE doc_ref = 'INV-88'")
    assert inv[0] == 10500.0 and inv[1] is not None                            # the price actually charged, in a new batch
    order_page = web.get(f"/purchase-orders/{po_id}/").content.decode()
    assert f'data-rmap="/related/po/{po_id}/"' in order_page and "Follow this order to the bank" not in order_page   # replaced by the relationship map
    assert "SI INV-88" in web.get(f"/related/po/{po_id}/").content.decode()
    diffs = web.get("/purchase-orders/?tab=diffs").content.decode()
    assert "Eagle F1" in diffs and "500.00" in diffs
    again = web.post("/purchase-orders/match/", {"s": cid, "action": "match", f"pick_{line_id}": "1", f"qty_{line_id}": "1", f"price_{line_id}": "1", "doc": "INV-88",
                                                 "inv_date": "2026-10-03", "batch": "new"}, follow=True)
    assert fetchone(_db(), "SELECT COUNT(*) FROM invoices WHERE doc_ref = 'INV-88'")[0] == 1
    assert again.status_code == 200


def test_receive_goods_and_the_invoice_in_one_step(web, db):
    _admin(web)
    cid = _supplier_with_price(web, db)
    po_id = _start(web, cid)
    _order_it(web, po_id)
    line_id = fetchone(_db(), "SELECT line_id FROM purchase_order_lines WHERE po_id = ?", (po_id,))[0]
    page = web.get(f"/purchase-orders/receive/?s={cid}").content.decode()        # one page (owner, 2026-10-11): what arrived, with its SI no. or DR no.
    assert "Goods still to come" in page and 'name="doc"' in page and 'name="dr"' in page and "Goods only" not in page
    web.post("/purchase-orders/receive/", {"s": cid, "action": "both", f"pick_{line_id}": "1", f"qty_{line_id}": "10", f"price_{line_id}": "1000", "doc": "INV-1",
                                           "inv_date": "2026-10-03", "batch": "new"})
    assert fetchone(_db(), "SELECT status FROM purchase_orders WHERE po_id = ?", (po_id,))[0] == "RECEIVED"
    assert fetchone(_db(), "SELECT amount FROM invoices WHERE doc_ref = 'INV-1'")[0] == 10000.0


def test_cancel_and_close_short(web, db):
    _admin(web)
    cid = _supplier_with_price(web, db)
    a = _start(web, cid)
    web.post(f"/purchase-orders/{a}/", {"action": "cancel", "reason": "typo"})
    assert fetchone(_db(), "SELECT status FROM purchase_orders WHERE po_id = ?", (a,))[0] == "CANCELLED"
    b = _start(web, cid)
    _order_it(web, b)
    line_id = fetchone(_db(), "SELECT line_id FROM purchase_order_lines WHERE po_id = ?", (b,))[0]
    web.post("/purchase-orders/receive/", {"s": cid, "action": "goods", f"pick_{line_id}": "1", f"qty_{line_id}": "4", "kind": "delivery receipt", "received": "2026-10-03"})
    assert b"Close short" in web.get(f"/purchase-orders/{b}/").content
    web.post(f"/purchase-orders/{b}/", {"action": "close_short", "reason": "supplier out of stock"})
    assert fetchone(_db(), "SELECT status FROM purchase_orders WHERE po_id = ?", (b,))[0] == "RECEIVED"


def test_the_list_and_its_tabs(web, db):
    _admin(web)
    cid = _supplier_with_price(web, db)
    po_id = _start(web, cid)
    _order_it(web, po_id)
    page = web.get("/purchase-orders/").content.decode()
    assert "Goodyear Philippines" in page and "Ordered" in page and "10,000.00" in page
    for tab in ("out", "pipeline", "diffs"):
        assert web.get(f"/purchase-orders/?tab={tab}").status_code == 200
    assert web.get("/purchase-orders/?mode=receive")["Location"] == "/purchase-orders/receive/"
    assert web.get("/purchase-orders/?mode=match")["Location"] == "/purchase-orders/match/"
    assert web.get("/purchase-orders/match/?s=1&po=2")["Location"] == "/purchase-orders/receive/?s=1&po=2"     # Match invoice is part of Receive now


def test_the_item_box_fills_from_the_price_list(web, db):
    _admin(web)
    cid = _supplier_with_price(web, db)
    hint = web.get(f"/purchase-orders/price/?s={cid}&item=Eagle%20F1&on=2026-10-01").json()
    assert hint["found"] and hint["list_price"] == 1000.0 and hint["unit"] == "pc"
    assert web.get(f"/purchase-orders/price/?s={cid}&item=Nothing").json() == {"found": False}


def test_roles(web, db):
    _admin(web)
    cid = _supplier_with_price(web, db)
    buyer = _sign_in_new("buyer@slicks.test", "purchasing")
    mine = _start(buyer, cid)
    theirs = _start(web, cid)
    assert buyer.get(f"/purchase-orders/{mine}/edit/").status_code == 200
    r = buyer.get(f"/purchase-orders/{theirs}/edit/", follow=True)
    assert b"can no longer be changed here" in r.content                        # Purchasing changes only its own orders
    page = buyer.get(f"/purchase-orders/receive/?s={cid}").content.decode()    # receiving always records the payable (owner, 2026-10-11): the same form for all
    assert "Record goods received" not in page
    payables = _sign_in_new("ap@slicks.test", "payables")
    assert payables.get("/purchase-orders/new/").status_code == 403
    assert payables.get("/purchase-orders/match/", follow=True).status_code == 200
    mother = _sign_in_new("mother@slicks.test", "releaser")
    assert mother.get("/purchase-orders/").status_code == 403
