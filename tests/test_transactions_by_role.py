"""End to end, by role (2026-10-02): every step of order -> price -> receive -> batch -> check -> release either works for the role the access matrix allows, or is
refused with a message. Also the fixes from the logic review: who is acting inside a button click or a pop-up, the net-to-list price update on receive, closing an
order short, a typed invoice total, discontinued products and the import message for a price already set on a date."""
import sqlite3
from datetime import date

import app
from conftest import fetchone, make_creditor
from engines import acting_user as au_mod
from engines import discount_rules as dr
from engines import price_template_service as pt
from engines import product_catalog as pc
from engines.acting_user import acting_as
from engines.check_rules import assign_check_numbers_manual, cancel_check, issue_check
from engines.pdc_release_service import mark_released
from engines.permissions import require
from engines.po_rules import close_short, get_po, open_commitments, order_po, po_links_for_batch, receive_into_batch, save_po
from engines.price_list_service import add_price, item_names, price_on

OWNER, CLERK, BUYER, VIEWER = "owner@example.com", "clerk@example.com", "buyer@example.com", "viewer@example.com"
D = date(2026, 10, 1)


def _setup(db):
    cid = make_creditor(db, name="New Century", terms="30")
    pid, name, _ = pc.get_or_create_product("Tires", "Westlake", size="285/50R20", terrain="A/T", pattern="SL369")
    with acting_as(BUYER, "purchasing"):
        assert add_price(cid, name, "pc", 7600, date(2026, 9, 15))[0]
        pc.link_price_rows(cid, name, pid)
        assert dr.set_discount(cid, "Westlake", "5,5", date(2026, 9, 1))[0]
    return cid, name


def test_the_whole_cycle_with_each_role_doing_its_own_part(db):
    cid, name = _setup(db)
    # Purchasing: order at list less 5% less 5%, and send it
    with acting_as(BUYER, "purchasing"):
        ok, msg, po = save_po(None, cid, D, date(2026, 10, 8), [{"item_name": name, "qty": 4}])
        assert ok, msg
        assert get_po(po)["total"] == 27436.00
        assert order_po(po)[0]
        assert not receive_into_batch(cid, 1, "X", D, [])[0]                         # receiving is not Purchasing's
    # Payables: the invoice arrives at 6,900 each; it lands in a batch, and a check is parked
    with acting_as(CLERK, "payables"):
        sid = app.create_order_statement(cid)
        line = get_po(po)["lines"].iloc[0]
        ok, msg, inv = receive_into_batch(cid, sid, "SI-1001", date(2026, 10, 9), [{"line_id": int(line["line_id"]), "qty": 4, "actual_unit_price": 6900}], update_prices=True)
        assert ok, msg
        assert "list price now 7,645.43" in msg                                         # 6,900 is NET: back to list before the 5%, 5% (6,900 / 0.9025)
        assert price_on(cid, name, date(2026, 10, 10))["unit_price"] == 7645.43          # so the next order nets 6,900 again, not 6,227
        ok, msg, chk = issue_check(cid, 27600.0, date(2026, 11, 8), statement_id=sid)
        assert ok, msg
        assert not assign_check_numbers_manual({chk: "5001"}, None)[0]                  # printing and numbering are the owner's
    # Owner: number it (as if printed), then the clerk records the release
    with acting_as(OWNER, "owner"):
        assert assign_check_numbers_manual({chk: "5001"}, None)[0]
    con = sqlite3.connect(db)
    con.execute("UPDATE checks_issued SET printed_at = '2026-10-10 10:00:00' WHERE check_id = ?", (chk,))
    con.commit()
    con.close()
    with acting_as(CLERK, "payables"):
        assert mark_released([chk], date(2026, 10, 12), "Supplier's collector")[0]
        assert not cancel_check(chk, "no")[0]                                           # voiding is the owner's
    assert open_commitments(D, date(2027, 6, 1)).empty                                 # the order became the invoice: nothing counted twice


def test_a_viewer_can_change_nothing(db):
    cid, name = _setup(db)
    with acting_as(VIEWER, "viewer"):
        assert not save_po(None, cid, D, None, [{"item_name": name, "qty": 1}])[0]
        assert not add_price(cid, name, "pc", 1, D)[0]
        assert not issue_check(cid, 1.0, D)[0]
        for action in ("po.receive", "check.release", "users.manage", "settings.edit", "price.write"):
            assert not require(action)[0], action


def test_a_click_or_a_popup_acts_as_the_person_signed_in_to_the_tab(db, monkeypatch):
    """Button callbacks and pop-ups run before the page's sign-in step: the session still says who it is."""
    cid, name = _setup(db)
    monkeypatch.setattr(au_mod, "_signed_in", lambda: {"email": "Buyer@Example.com", "role": "purchasing"})
    assert au_mod.acting_email() == BUYER and au_mod.acting_role() == "purchasing" and au_mod.acting_user() == BUYER
    ok, msg, po = save_po(None, cid, D, date(2026, 10, 8), [{"item_name": name, "qty": 1}])          # e.g. 'Save draft' (a button callback)
    assert ok and fetchone(db, "SELECT created_by FROM purchase_orders WHERE po_id = ?", (po,))[0] == BUYER
    assert order_po(po)[0]                                                                          # later 'Order it': the same person, so allowed
    assert not require("po.ack_over_plan")[0]                                                       # 'Noted' in a pop-up is still owner-only
    monkeypatch.setattr(au_mod, "_signed_in", lambda: {"email": VIEWER, "role": "viewer"})
    assert not __import__("engines.po_rules", fromlist=["cancel_po"]).cancel_po(po, "x")[0]        # 'Cancel order' in a pop-up, by a viewer


def test_closing_an_order_short_stops_counting_what_will_not_come(db):
    cid, name = _setup(db)
    with acting_as(OWNER, "owner"):
        _, _, po = save_po(None, cid, D, date(2026, 10, 8), [{"item_name": name, "qty": 10}])
        order_po(po)
        sid = app.create_order_statement(cid)
        line = get_po(po)["lines"].iloc[0]
        assert receive_into_batch(cid, sid, "SI-1", date(2026, 10, 9), [{"line_id": int(line["line_id"]), "qty": 8, "actual_unit_price": float(line["est_unit_price"])}])[0]
        assert not open_commitments(D, date(2027, 6, 1)).empty                           # 2 still to come
        assert not close_short(po, "")[0]                                                 # a reason is required
        ok, msg = close_short(po, "supplier out of stock")
        assert ok and "13,718.00" in msg                                                  # 2 x 6,859
    assert get_po(po)["header"]["status"] == "RECEIVED" and open_commitments(D, date(2027, 6, 1)).empty
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'PO_CLOSED_SHORT'")[0] == 1


def test_a_typed_invoice_total_is_what_the_receipt_records(db):
    cid, name = _setup(db)
    _, _, po = save_po(None, cid, D, date(2026, 10, 8), [{"item_name": name, "qty": 2}])
    order_po(po)
    sid = app.create_order_statement(cid)
    line = get_po(po)["lines"].iloc[0]
    ok, msg, inv = receive_into_batch(cid, sid, "SI-9", date(2026, 10, 9), [{"line_id": int(line["line_id"]), "qty": 2, "actual_unit_price": float(line["est_unit_price"])}],
                                      gross_override=14000.0)                              # the invoice adds freight
    assert ok
    assert fetchone(db, "SELECT actual_amount FROM po_receipts")[0] == 14000.0
    links = po_links_for_batch(sid)
    assert not bool(links["matches"].iloc[0]) and round(float(links["difference"].iloc[0]), 2) == 282.0     # 14,000 against 2 x 6,859


def test_a_draft_may_wait_for_a_price_but_ordering_refuses_any_line_without_one(db):
    cid, name = _setup(db)
    ok, msg, po = save_po(None, cid, D, date(2026, 10, 8), [{"item_name": name, "qty": 1}, {"item_name": "Valve caps", "qty": 4}])
    assert ok
    ok, msg = order_po(po)
    assert not ok and "Valve caps" in msg


def test_discontinued_products_are_not_offered_on_new_orders(db):
    cid, name = _setup(db)
    pid = fetchone(db, "SELECT id FROM items")[0]
    assert name in item_names(cid)
    assert pc.set_product_active(pid, False)[0]
    assert name not in item_names(cid) and price_on(cid, name, D) is None
    assert pc.set_product_active(pid, True)[0] and price_on(cid, name, D)["unit_price"] == 7600


def test_the_import_says_when_a_new_price_was_not_saved_because_the_date_already_has_one(db):
    cid, name = _setup(db)
    row = {"category": "Tires", "row": 2, "brand": "Westlake", "values": {"size": "285/50R20", "terrain": "A/T", "pattern": "SL369"}, "supplier_code": "", "unit": "pc",
           "list_price": 7700.0, "effective_date": date(2026, 9, 15), "remarks": "", "name": name}
    parsed = {"rows": [row], "counts": {"Tires": 1}, "problems": []}
    ok, msg, counts = pt.import_workbook(cid, parsed)
    assert ok and counts["kept_old"] == 1 and "NOT saved" in msg
    assert price_on(cid, name, date(2026, 10, 1))["unit_price"] == 7600
    ok, msg, counts = pt.import_workbook(cid, parsed, on_duplicate="replace")
    assert counts["replaced"] == 1 and price_on(cid, name, date(2026, 10, 1))["unit_price"] == 7700


# ---- receiving in two steps: Purchasing records the goods, Payables matches the invoice into a batch -------------------------------------------

def _ordered(db, qty=10):
    from engines.po_rules import order_po
    cid, name = _setup(db)
    with acting_as(BUYER, "purchasing"):
        _, _, po = save_po(None, cid, D, date(2026, 10, 8), [{"item_name": name, "qty": qty}])
        order_po(po)
    return cid, name, po


def test_purchasing_records_the_goods_without_any_invoice_or_batch(db):
    from engines.po_rules import receive_goods
    cid, name, po = _ordered(db)
    line = int(get_po(po)["lines"].iloc[0]["line_id"])
    with acting_as(BUYER, "purchasing"):
        ok, msg, _ = receive_goods(cid, [{"line_id": line, "qty": 6}], date(2026, 10, 9), ref="DR-77")
        assert ok, msg
        assert not receive_into_batch(cid, 1, "SI-1", D, [], goods_received=True)[0]            # the invoice step is not Purchasing's
    assert fetchone(db, "SELECT COUNT(*) FROM invoices")[0] == 0 and fetchone(db, "SELECT COUNT(*) FROM order_statements")[0] == 0
    row = get_po(po)
    assert row["header"]["status"] == "PARTIAL" and row["lines"].iloc[0]["qty_received"] == 6
    assert fetchone(db, "SELECT supplier_invoice_no, invoice_id FROM po_receipts") == ("DR-77", None)
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'PO_GOODS_RECEIVED'")[0] == 1


def test_goods_received_but_not_invoiced_still_count_as_owed_until_the_invoice_is_matched(db):
    from engines.po_rules import invoiceable_lines, receive_goods
    cid, name, po = _ordered(db)
    line = int(get_po(po)["lines"].iloc[0]["line_id"])
    with acting_as(BUYER, "purchasing"):
        assert receive_goods(cid, [{"line_id": line, "qty": 10}], D)[0]                         # everything arrived
    owed = open_commitments(D, date(2027, 6, 1))
    assert get_po(po)["header"]["status"] == "RECEIVED" and round(float(owed["amount"].sum()), 2) == 68590.00      # 10 x 6,859 still owed: no invoice yet
    waiting = invoiceable_lines(cid)
    assert list(waiting["remaining"]) == [10.0]
    with acting_as(CLERK, "payables"):
        sid = app.create_order_statement(cid)
        ok, msg, inv = receive_into_batch(cid, sid, "SI-2001", date(2026, 10, 12), [{"line_id": line, "qty": 4, "actual_unit_price": 6900}], goods_received=True)
        assert ok, msg
        assert not receive_into_batch(cid, sid, "SI-2002", date(2026, 10, 12), [{"line_id": line, "qty": 7, "actual_unit_price": 6900}], goods_received=True)[0]   # only 6 left
    assert fetchone(db, "SELECT gross_amount FROM invoices WHERE doc_ref = 'SI-2001'")[0] == 27600.0
    assert round(float(open_commitments(D, date(2027, 6, 1))["amount"].sum()), 2) == 41154.00        # 6 x 6,859 are still waiting for their invoice
    assert get_po(po)["lines"].iloc[0]["qty_received"] == 10                                     # matching the invoice does not change what was received
    with acting_as(CLERK, "payables"):
        assert receive_into_batch(cid, sid, "SI-2003", date(2026, 10, 12), [{"line_id": line, "qty": 6, "actual_unit_price": 6900}], goods_received=True)[0]
    assert open_commitments(D, date(2027, 6, 1)).empty and invoiceable_lines(cid).empty


def test_a_viewer_cannot_record_goods_and_the_one_step_receive_still_works(db):
    from engines.po_rules import receive_goods
    cid, name, po = _ordered(db)
    line = int(get_po(po)["lines"].iloc[0]["line_id"])
    with acting_as(VIEWER, "viewer"):
        assert not receive_goods(cid, [{"line_id": line, "qty": 1}], D)[0]
    with acting_as(CLERK, "payables"):                                                          # receive and invoice in one go, as before
        sid = app.create_order_statement(cid)
        assert receive_into_batch(cid, sid, "SI-3001", D, [{"line_id": line, "qty": 10, "actual_unit_price": 6859}])[0]
    assert open_commitments(D, date(2027, 6, 1)).empty and get_po(po)["header"]["status"] == "RECEIVED"


def test_closing_short_keeps_counting_only_what_was_delivered_and_not_yet_invoiced(db):
    from engines.po_rules import receive_goods
    cid, name, po = _ordered(db)
    line = int(get_po(po)["lines"].iloc[0]["line_id"])
    with acting_as(OWNER, "owner"):
        assert receive_goods(cid, [{"line_id": line, "qty": 8}], D)[0]
        assert close_short(po, "supplier out of stock")[0]
    assert round(float(open_commitments(D, date(2027, 6, 1))["amount"].sum()), 2) == 54872.00     # 8 x 6,859 delivered, invoice still to come; the 2 that will not come are gone


def test_a_supplier_without_delivery_receipts_gives_the_invoice_number_when_the_goods_arrive(db):
    from engines.po_rules import invoiceable_lines, receive_goods
    cid, name, po = _ordered(db)
    line = int(get_po(po)["lines"].iloc[0]["line_id"])
    with acting_as(BUYER, "purchasing"):
        assert receive_goods(cid, [{"line_id": line, "qty": 4}], D, ref="SI-555", doc_type="invoice")[0]
        assert receive_goods(cid, [{"line_id": line, "qty": 2}], D, ref="778", doc_type="delivery receipt")[0]
    assert [r[0] for r in __import__("sqlite3").connect(db).execute("SELECT supplier_invoice_no FROM po_receipts ORDER BY receipt_id")] == ["SI-555", "DR 778"]
    waiting = invoiceable_lines(cid)
    assert list(waiting["remaining"]) == [6.0] and waiting["received_ref"].iloc[0] == "DR 778"      # the latest paper; an invoice number is offered when it is the only kind
