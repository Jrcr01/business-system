"""Purchase orders (B2): estimate from the price list, expected pay date from the supplier's terms, permanent numbers, editing rules and the supplier's PDF."""
import sqlite3
from datetime import date

import pymupdf

from conftest import fetchone, make_creditor
from engines.po_pdf import build_po_pdf
from engines.po_rules import cancel_po, expected_pay_date, get_po, list_pos, order_po, save_po
from engines.price_list_service import add_price


def _supplier(db, terms="30"):
    return make_creditor(db, name="Acme Tires", terms=terms)


def _po(db, cid, lines=None, delivery=date(2026, 10, 10), order_date=date(2026, 10, 1)):
    lines = lines if lines is not None else [{"item_name": "Tire 175/70R13", "unit": "pc", "qty": 10}]
    ok, msg, po_id = save_po(None, cid, order_date, delivery, lines, "rush")
    assert ok, msg
    return po_id


def test_the_pay_date_is_delivery_plus_terms_moved_to_a_bank_day(db):
    cid = _supplier(db, terms="30")
    assert expected_pay_date(cid, date(2026, 10, 1)) == date(2026, 11, 2)          # Nov 1 2026 is a Sunday -> Monday
    sqlite3.connect(db).execute("INSERT OR REPLACE INTO calendar_blocked_dates (blocked_date, label, kind) VALUES ('2026-11-02', 'test holiday', 'holiday')").connection.commit()
    assert expected_pay_date(cid, date(2026, 10, 1)) == date(2026, 11, 3)
    assert expected_pay_date(cid, None) is None


def test_a_line_takes_its_estimate_from_the_price_list_on_the_order_date(db):
    cid = _supplier(db)
    add_price(cid, "Tire 175/70R13", "pc", 1850, date(2026, 9, 1))
    add_price(cid, "Tire 175/70R13", "pc", 1900, date(2026, 11, 1))                # not yet in force on Oct 1
    po_id = _po(db, cid)
    po = get_po(po_id)
    line = po["lines"].iloc[0]
    assert line["est_unit_price"] == 1850 and line["est_amount"] == 18500 and po["total"] == 18500
    assert po["header"]["terms_snapshot"] == "30" and po["header"]["expected_pay_date"] == "2026-11-10"
    assert po["header"]["status"] == "DRAFT" and po["header"]["po_no"].startswith("DRAFT-PO-")


def test_a_typed_price_wins_over_the_list_and_an_unlisted_item_can_be_priced_by_hand(db):
    cid = _supplier(db)
    add_price(cid, "Tire", "pc", 1850, date(2026, 9, 1))
    po_id = _po(db, cid, lines=[{"item_name": "Tire", "unit": "pc", "qty": 2, "est_unit_price": 1700.555},
                                {"item_name": "Misc fee", "unit": "", "qty": 1, "est_unit_price": 99}])
    lines = get_po(po_id)["lines"].set_index("item_name")
    assert lines.loc["Tire", "est_amount"] == 3401.11                              # rounded to the centavo
    assert lines.loc["Misc fee", "est_amount"] == 99
    assert lines["price_id"].isna().all()                                          # both prices were typed, so neither came from the list


def test_an_unlisted_item_without_a_price_is_an_estimate_of_zero_not_an_error(db):
    cid = _supplier(db)
    po_id = _po(db, cid, lines=[{"item_name": "Mystery part", "unit": "pc", "qty": 3}])
    assert get_po(po_id)["total"] == 0


def test_bad_orders_are_refused(db):
    cid = _supplier(db)
    assert not save_po(None, cid, date(2026, 10, 1), date(2026, 10, 5), [], "")[0]                       # no lines
    assert not save_po(None, cid, date(2026, 10, 1), date(2026, 10, 5), [{"item_name": "X", "qty": 0}], "")[0]
    assert not save_po(None, cid, date(2026, 10, 1), date(2026, 10, 5), [{"item_name": " ", "qty": 1}], "")[0]
    assert not save_po(None, cid, date(2026, 10, 10), date(2026, 10, 5), [{"item_name": "X", "qty": 1}], "")[0]   # delivery before the order
    assert fetchone(db, "SELECT COUNT(*) FROM purchase_orders")[0] == 0


def test_editing_replaces_the_lines_and_recomputes_while_the_order_is_open(db):
    cid = _supplier(db)
    add_price(cid, "Tire", "pc", 1000, date(2026, 9, 1))
    po_id = _po(db, cid, lines=[{"item_name": "Tire", "unit": "pc", "qty": 1}])
    ok, msg, same = save_po(po_id, cid, date(2026, 10, 1), date(2026, 10, 20), [{"item_name": "Tire", "unit": "pc", "qty": 5}], "changed")
    assert ok and same == po_id
    po = get_po(po_id)
    assert len(po["lines"]) == 1 and po["total"] == 5000 and po["header"]["expected_delivery_date"] == "2026-10-20" and po["header"]["notes"] == "changed"
    assert po["header"]["expected_pay_date"] == "2026-11-20"
    assert order_po(po_id)[0]
    assert save_po(po_id, cid, date(2026, 10, 1), date(2026, 10, 25), [{"item_name": "Tire", "unit": "pc", "qty": 6}], "")[0]    # still open after ordering


def test_ordering_gives_a_permanent_number_once(db):
    cid = _supplier(db)
    add_price(cid, "Tire 175/70R13", "pc", 100, date(2026, 9, 1))
    a, b = _po(db, cid), _po(db, cid)
    ok, msg = order_po(a)
    assert ok and get_po(a)["header"]["po_no"] == "2026 - PO 0001" and get_po(a)["header"]["status"] == "ORDERED"
    assert order_po(b)[0] and get_po(b)["header"]["po_no"] == "2026 - PO 0002"
    assert order_po(a)[0] and get_po(a)["header"]["po_no"] == "2026 - PO 0001"       # already ordered: the number is kept
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'PO_ORDERED'")[0] == 2


def test_ordering_needs_a_delivery_date_and_a_priced_order(db):
    cid = _supplier(db)
    no_date = _po(db, cid, delivery=None)
    ok, msg = order_po(no_date)
    assert not ok and "delivery" in msg.lower()
    unpriced = _po(db, cid, lines=[{"item_name": "Mystery", "qty": 1}])
    ok, msg = order_po(unpriced)
    assert not ok and "price" in msg.lower()


def test_a_cancelled_order_cannot_be_ordered_or_edited(db):
    cid = _supplier(db)
    po_id = _po(db, cid)
    assert cancel_po(po_id, "changed mind")[0]
    assert get_po(po_id)["header"]["status"] == "CANCELLED"
    assert not order_po(po_id)[0]
    assert not save_po(po_id, cid, date(2026, 10, 1), date(2026, 10, 5), [{"item_name": "X", "qty": 1, "est_unit_price": 1}], "")[0]
    assert not cancel_po(po_id)[0]


def test_an_order_with_a_receipt_cannot_be_cancelled_or_edited(db):
    cid = _supplier(db)
    add_price(cid, "Tire", "pc", 100, date(2026, 9, 1))
    po_id = _po(db, cid, lines=[{"item_name": "Tire", "unit": "pc", "qty": 1}])
    order_po(po_id)
    sqlite3.connect(db).execute("INSERT INTO po_receipts (po_id, received_date, actual_amount) VALUES (?, '2026-10-05', 100)", (po_id,)).connection.commit()
    assert not cancel_po(po_id)[0]
    assert not save_po(po_id, cid, date(2026, 10, 1), date(2026, 10, 5), [{"item_name": "Tire", "qty": 1}], "")[0]


def test_the_list_filters_and_flags_late_deliveries(db):
    cid = _supplier(db)
    add_price(cid, "Tire", "pc", 100, date(2026, 9, 1))
    late = _po(db, cid, lines=[{"item_name": "Tire", "unit": "pc", "qty": 1}], delivery=date(2026, 10, 5))
    soon = _po(db, cid, lines=[{"item_name": "Tire", "unit": "pc", "qty": 1}], delivery=date(2026, 10, 25))
    for p in (late, soon):
        order_po(p)
    df = list_pos(today=date(2026, 10, 10)).set_index("po_id")
    assert bool(df.loc[late, "delivery_overdue"]) and not bool(df.loc[soon, "delivery_overdue"])
    assert list(list_pos(status="DRAFT")["po_id"]) == []
    assert set(list_pos(status="ORDERED", creditor_id=cid)["po_id"]) == {late, soon}


def test_the_supplier_pdf_has_the_order_and_no_cash_information(db):
    cid = _supplier(db, terms="60")
    add_price(cid, "Tire 175/70R13", "pc", 1850, date(2026, 9, 1))
    po_id = _po(db, cid)
    assert build_po_pdf(po_id) is None                                              # a draft has no number yet, so nothing to send
    order_po(po_id)
    pdf = build_po_pdf(po_id)
    assert pdf[:4] == b"%PDF"
    text = "".join(p.get_text() for p in pymupdf.open(stream=pdf, filetype="pdf"))
    for needed in ("PURCHASE ORDER", "2026 - PO 0001", "Acme Tires", "Tire 175/70R13", "18,500.00", "Oct 10, 2026", "60"):
        assert needed in text, needed
    for forbidden in ("cash", "balance", "forecast", "bank"):
        assert forbidden not in text.lower(), forbidden
