"""The Review list (2026-10-03): controls flag, they do not block. A duty overlap, a price far above its order and a new payee go through at once and land on the list;
only the System admin or an owner can tick a line, and a ticked line keeps who and when."""
from datetime import date

import app
from conftest import fetchone, make_creditor
from engines import product_catalog as pc
from engines import review_flags as rf
from engines.acting_user import acting_as
from engines.creditor_rules import add_creditor
from engines.po_rules import get_po, order_po, receive_goods, receive_into_batch, save_po
from engines.price_list_service import add_price

BUYER, CLERK, ADMIN, FAMILY = "buyer@example.com", "clerk@example.com", "admin@example.com", "family@example.com"
D = date(2026, 10, 1)


def _ordered(db, who=BUYER, role="purchasing"):
    cid = make_creditor(db, name="Flag Supplier", terms="30")
    pid, name, _ = pc.get_or_create_product("Tires", "Westlake", size="285/50R20", terrain="A/T", pattern="SL369")
    with acting_as(who, role):
        assert add_price(cid, name, "pc", 1000, date(2026, 9, 15), trusted=True)[0]
        ok, msg, po = save_po(None, cid, D, date(2026, 10, 8), [{"item_name": name, "qty": 4}])
        assert ok, msg
        assert order_po(po)[0]
    line = int(get_po(po)["lines"].iloc[0]["line_id"])
    return cid, po, line


def test_the_buyer_receiving_his_own_order_goes_through_and_is_flagged(db):
    cid, po, line = _ordered(db)
    with acting_as(BUYER, "purchasing"):
        ok, msg, _ = receive_goods(cid, [{"line_id": line, "qty": 4}])
    assert ok, msg                                                                  # not blocked
    flags = rf.open_flags()
    assert list(flags["kind"]) == ["SOD_ORDERED_RECEIVED"] and BUYER in flags.iloc[0]["detail"]


def test_someone_else_receiving_is_not_flagged(db):
    cid, po, line = _ordered(db)
    with acting_as(CLERK, "payables"):
        assert receive_goods(cid, [{"line_id": line, "qty": 4}])[0]
    assert rf.open_count() == 0


def test_the_same_person_receiving_and_matching_the_invoice_is_flagged(db):
    cid, po, line = _ordered(db)
    with acting_as(CLERK, "payables"):
        assert receive_goods(cid, [{"line_id": line, "qty": 4}])[0]
        sid = app.create_order_statement(cid)
        ok, msg, _ = receive_into_batch(cid, sid, "SI-1", date(2026, 10, 9), [{"line_id": line, "qty": 4, "actual_unit_price": 1000}], goods_received=True)
        assert ok, msg
    assert list(rf.open_flags()["kind"]) == ["SOD_RECEIVED_MATCHED"]


def test_a_price_well_above_the_order_is_flagged_but_saved(db):
    cid, po, line = _ordered(db)
    with acting_as(CLERK, "payables"):
        sid = app.create_order_statement(cid)
        ok, msg, inv = receive_into_batch(cid, sid, "SI-2", date(2026, 10, 9), [{"line_id": line, "qty": 4, "actual_unit_price": 1200}])
        assert ok, msg
    flags = rf.open_flags()
    assert list(flags["kind"]) == ["PRICE_OVER_PO"] and "+20%" in flags.iloc[0]["detail"]
    assert fetchone(db, "SELECT gross_amount FROM invoices WHERE invoice_id = ?", (inv,))[0] == 4800.0


def test_a_new_supplier_by_payables_is_usable_at_once_and_flagged_but_the_admins_is_not(db):
    with acting_as(CLERK, "payables"):
        cid = add_creditor("Delta Trading", "SUPPLIER", "", "", "", "")
    assert cid and list(rf.open_flags()["kind"]) == ["NEW_PAYEE"]
    with acting_as(ADMIN, "admin"):
        add_creditor("Admin Supplier", "SUPPLIER", "", "", "", "")
    assert rf.open_count() == 1


def test_only_admin_or_owner_tick_a_line_and_it_keeps_who_and_when(db):
    cid, po, line = _ordered(db)
    with acting_as(BUYER, "purchasing"):
        receive_goods(cid, [{"line_id": line, "qty": 4}])
    fid = int(rf.open_flags().iloc[0]["flag_id"])
    with acting_as(BUYER, "purchasing"):
        assert not rf.acknowledge(fid)[0]
    with acting_as(FAMILY, "owner"):
        assert rf.acknowledge(fid)[0]
    assert rf.open_count() == 0
    who, when = fetchone(db, "SELECT reviewed_by, reviewed_at FROM review_flags WHERE flag_id = ?", (fid,))
    assert who == FAMILY and when
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'REVIEW_OK'")[0] == 1
