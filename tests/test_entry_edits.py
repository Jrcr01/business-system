"""Saved entries corrected in place (owner, 2026-10-19: "make all things for now editable since my brother is adapting the system"; "my worry is they
incorrectly input data"): a sale edited with its stock and payment following, the before / after kept, and the owner's Changed today list."""
import sqlite3

from conftest import fetchone
from tests.test_web_d0 import _admin
from tests.test_web_stock import _item, _stock


def _sale(qty=2, price=3200):
    from engines.clock import manila_today
    from engines.sales_rules import save_sale
    a, _ = _item("Westlake", "215/70R15", "RP36")
    _stock(a, 10, 2900.0)
    ok, msg, sid = save_sale(87990, manila_today(), None, "Walk-in", [{"item_id": a, "qty": qty, "price": price}], [{"description": "Tire Valve", "amount": 100}],
                             [{"method": "CASH", "amount": qty * price + 100}])
    assert ok, msg
    return a, sid


def test_a_saved_sale_is_corrected_in_place_and_the_change_is_kept(web, db):
    from engines.clock import manila_today
    from engines.entry_changes import changes
    from engines.sales_rules import edit_sale
    _admin(web)
    a, sid = _sale()
    assert fetchone(db, "SELECT on_hand FROM items WHERE id = ?", (a,))[0] == 8
    ok, msg = edit_sale(sid, "087990", manila_today(), None, "", [{"item_id": a, "qty": 1, "price": 3300}], [{"description": "Tire Valve", "amount": 100}],
                        [{"method": "CASH", "amount": 3400}], "typed wrong qty and price")
    assert ok, msg
    assert fetchone(db, "SELECT total FROM sales WHERE sale_id = ?", (sid,))[0] == 3400.0
    assert fetchone(db, "SELECT on_hand FROM items WHERE id = ?", (a,))[0] == 9                      # one went back to stock
    assert fetchone(db, "SELECT SUM(amount) FROM sale_payments WHERE sale_id = ?", (sid,))[0] == 3400.0
    ch = changes()
    assert len(ch) == 1 and "2 x" in ch[0]["before"] and "1 x" in ch[0]["after"] and ch[0]["why"] == "typed wrong qty and price"
    assert edit_sale(sid, "087990", manila_today(), None, "", [{"item_id": a, "qty": 1, "price": 3300}], [{"description": "Tire Valve", "amount": 100}],
                     [{"method": "CASH", "amount": 3400}]) == (False, "Nothing was changed.")
    page = web.get(f"/sales/{sid}/").content.decode()
    assert "Edit this sale" in page and "Changes to this sale" in page
    form = web.get(f"/sales/{sid}/edit/").content.decode()
    assert "Editing SI 087990" in form and 'value="087990"' in form and "Tire Valve" in form


def test_the_edit_form_saves_and_the_owner_sees_it_on_changed_today(web, db):
    from engines.entry_changes import changes
    _admin(web)
    a, sid = _sale()
    r = web.post(f"/sales/{sid}/edit/", {"action": "save", "si_no": "087990", "sale_date": str(__import__("engines.clock", fromlist=["manila_today"]).manila_today()),
                                         "customer": "", "ln_item_0": a, "ln_qty_0": "2", "ln_price_0": "3300", "svc_desc_0": "Tire Valve", "svc_amt_0": "100",
                                         "pay_method_0": "CASH", "pay_amount_0": "6700", "why": "price"})
    assert r.status_code == 302 and r["Location"] == f"/sales/{sid}/"
    page = web.get("/review/?tab=changes").content.decode()
    assert "Changed today" in page and "SI 087990" in page and "3,300.00" in page
    cid = changes()[0]["id"]
    web.post("/review/?tab=changes", {"action": "seen", "change_id": cid})
    assert changes()[0]["seen_by"]


def test_edit_a_delivery_undoes_it_and_opens_receive_filled_in(web, db):
    from engines.entry_changes import changes
    from tests.test_web_po_request import _arrive, _n, _ordered
    _admin(web)
    cid, po, lid = _ordered(db)
    _arrive(web, cid, lid, 2, dr="DR-77")
    rid = _n("SELECT receipt_id FROM po_receipts")
    assert "Edit this delivery" in web.get(f"/purchase-orders/{po}/").content.decode()
    r = web.post(f"/purchase-orders/{po}/", {"action": "edit_delivery", "receipt_id": rid, "why": "qty was 3"})
    assert r.status_code == 302 and "/purchase-orders/receive/" in r["Location"]
    assert _n("SELECT COUNT(*) FROM po_receipts") == 0                                              # undone
    page = web.get(r["Location"]).content.decode()
    assert "Correcting DR 77" in page and 'id="redo_data"' in page
    ch = changes()
    assert ch and ch[0]["kind"] == "DELIVERY_EDITED" and "DR 77" in ch[0]["before"] and ch[0]["why"] == "qty was 3"


def test_a_check_paid_in_cash_is_done_and_loans_and_cards_do_not_wait_for_the_bank(web, db):
    """Owner, 2026-10-19: "we only use one bank which is metrobank. no waiting for loans and credit card. but for checks, there are checks that paid in cash"."""
    from engines.clock import manila_today
    from tests.test_web_batch_checks import _checks, _park
    from tests.test_web_d3a import _add_inv, _new_batch
    from conftest import make_creditor
    _admin(web)
    cid = make_creditor(db, "CSS Autoparts Inc.", terms="30")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, doc="110744", gross="6,000")
    today = manila_today()
    _park(web, sid, (str(today), "6,000"))
    chk = _checks(sid)[0][0]
    web.post(f"/order-statements/{sid}/", {"action": "chk_hand", f"num_{chk}": "7001"})
    page = web.get("/checks/").content.decode()
    assert f'name="how_check_{chk}"' in page and "paid in cash" in page
    web.post("/checks/", {"action": "clear_many", "on": str(today), f"tick_check_{chk}": "1", f"how_check_{chk}": "CASH"})
    assert fetchone(db, "SELECT settled_outside_at IS NOT NULL FROM checks_issued WHERE check_id = ?", (chk,))[0] == 1     # done, not waiting
    assert "7001" not in web.get("/checks/?show=ticked").content.decode()
