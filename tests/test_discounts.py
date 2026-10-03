"""Supplier chain discounts on list prices ("less 5%, less 5%"): the maths, the standing chain per supplier and brand, and PO lines that carry list, chain and net."""
from datetime import date

import pytest

from conftest import fetchone, make_creditor
from engines import discount_rules as dr
from engines.po_rules import get_po, save_po
from engines.price_list_service import add_price


@pytest.mark.parametrize("text, expected", [("5,5", [5, 5]), ("5% 5%", [5, 5]), ("less 5, less 5", [5, 5]), ("5+5", [5, 5]), ("", []), (None, []), ("7.5", [7.5])])
def test_chains_are_read_from_the_ways_people_type_them(text, expected):
    assert dr.parse_chain(text) == expected


@pytest.mark.parametrize("text", ["abc", "-5", "100", "5,,x", "5-5"])
def test_unreadable_or_impossible_chains_are_refused(text):
    assert dr.parse_chain(text) is None


def test_net_is_each_discount_taken_in_turn_not_added():
    assert dr.net_price(7600, "5,5") == 6859.00                      # not 6,840 (10% off)
    assert dr.net_price(7600, "") == 7600.00
    assert dr.net_price(1000, "10,5,2") == 837.90
    assert dr.chain_label("5,5") == "less 5% + 5%" and dr.chain_label("") == "" and dr.chain_text("less 5, less 5") == "5,5"


def test_a_brand_row_beats_the_supplier_wide_row_and_the_newest_date_wins(db):
    cid = make_creditor(db)
    assert dr.set_discount(cid, "", "3", date(2026, 1, 1))[0]
    assert dr.set_discount(cid, "Westlake", "5,5", date(2026, 1, 1))[0]
    assert dr.standing_chain(cid, "Westlake 305/35R24 SA57", date(2026, 6, 1)) == "5,5"
    assert dr.standing_chain(cid, "Apollo 12R22.5 X", date(2026, 6, 1)) == "3"              # another brand falls back to the supplier-wide row
    assert dr.standing_chain(cid, "Westlake 305/35R24 SA57", date(2025, 12, 31)) == ""      # before any start date
    dr.set_discount(cid, "Westlake", "5", date(2026, 9, 1))
    assert dr.standing_chain(cid, "Westlake 305/35R24 SA57", date(2026, 8, 31)) == "5,5"    # history kept
    assert dr.standing_chain(cid, "Westlake 305/35R24 SA57", date(2026, 9, 1)) == "5"
    dr.set_discount(cid, "Westlake", "", date(2026, 10, 1))                                  # 'no discount from now'
    assert dr.standing_chain(cid, "Westlake 305/35R24 SA57", date(2026, 10, 2)) == ""
    assert not dr.set_discount(cid, "Westlake", "abc", date(2026, 10, 1))[0]
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'DISCOUNT_SET'")[0] == 4


def test_net_price_on_uses_the_discount_in_force_on_the_date(db):
    cid = make_creditor(db)
    add_price(cid, "Westlake 285/50R20 (A/T) SL369", "pc", 7600, date(2026, 9, 15))
    dr.set_discount(cid, "Westlake", "5,5", date(2026, 9, 1))
    got = dr.net_price_on(cid, "westlake 285/50r20 (a/t) sl369", date(2026, 10, 1))
    assert got["list_price"] == 7600 and got["chain"] == "5,5" and got["net_price"] == 6859.00
    assert dr.net_price_on(cid, "Nothing", date(2026, 10, 1)) is None


def _po(cid, lines):
    ok, msg, po_id = save_po(None, cid, date(2026, 10, 1), date(2026, 10, 8), lines)
    assert ok, msg
    return get_po(po_id)


def test_a_po_line_takes_list_chain_and_net_from_the_list_and_the_standing_discount(db):
    cid = make_creditor(db)
    add_price(cid, "Westlake 285/50R20 (A/T) SL369", "pc", 7600, date(2026, 9, 15))
    dr.set_discount(cid, "Westlake", "5,5", date(2026, 9, 1))
    po = _po(cid, [{"item_name": "Westlake 285/50R20 (A/T) SL369", "qty": 4}])
    ln = po["lines"].iloc[0]
    assert ln["list_price"] == 7600 and ln["discount_chain"] == "5,5" and ln["est_unit_price"] == 6859.00 and ln["est_amount"] == 27436.00
    assert po["total"] == 27436.00                                                          # the order, the payable and the schedule all use net


def test_a_line_can_override_the_chain_or_type_a_net_price(db):
    cid = make_creditor(db)
    add_price(cid, "Westlake X", "pc", 1000, date(2026, 9, 15))
    dr.set_discount(cid, "Westlake", "5,5", date(2026, 9, 1))
    lines = _po(cid, [{"item_name": "Westlake X", "qty": 1, "discount_chain": "10"},          # overridden
                      {"item_name": "Westlake X", "qty": 1, "discount_chain": ""},            # no discount on this line
                      {"item_name": "Typed", "qty": 1, "est_unit_price": 500},                # a net price typed straight in
                      {"item_name": "Westlake X", "qty": 1, "list_price": 2000}])["lines"]    # another list price, standing chain applies
    assert list(lines["est_unit_price"]) == [900.0, 1000.0, 500.0, 1805.0]
    assert list(lines["discount_chain"].fillna("-")) == ["10", "", "-", "5,5"]
    assert not save_po(None, cid, date(2026, 10, 1), None, [{"item_name": "Westlake X", "qty": 1, "discount_chain": "zzz"}])[0]


def test_orders_without_any_discount_keep_their_old_totals(db):
    cid = make_creditor(db)
    add_price(cid, "Tire", "pc", 1850, date(2026, 9, 1))
    po = _po(cid, [{"item_name": "Tire", "qty": 10}])
    assert po["total"] == 18500 and po["lines"].iloc[0]["discount_chain"] == ""


def test_the_supplier_pdf_shows_list_discount_and_net_only_when_there_is_a_discount(db):
    import pymupdf

    from engines.po_pdf import build_po_pdf
    from engines.po_rules import order_po

    def text_of(po_id):
        order_po(po_id)
        return "".join(p.get_text() for p in pymupdf.open(stream=build_po_pdf(po_id), filetype="pdf"))

    cid = make_creditor(db)
    add_price(cid, "Westlake 285/50R20 (A/T) SL369", "pc", 7600, date(2026, 9, 15))
    plain = text_of(_po(cid, [{"item_name": "Westlake 285/50R20 (A/T) SL369", "qty": 4}])["header"]["po_id"])
    assert "List price" not in plain and "30,400.00" in plain
    dr.set_discount(cid, "Westlake", "5,5", date(2026, 9, 1))
    disc = text_of(_po(cid, [{"item_name": "Westlake 285/50R20 (A/T) SL369", "qty": 4}])["header"]["po_id"])
    for needed in ("List price", "7,600.00", "less 5% + 5%", "6,859.00", "27,436.00"):
        assert needed in disc, needed


def test_a_po_line_keeps_the_catalog_product_of_the_price_it_used(db):
    from engines import product_catalog as pc
    cid = make_creditor(db)
    pid, name, _ = pc.get_or_create_product("Tires", "Westlake", size="285/50R20", terrain="A/T", pattern="SL369")
    add_price(cid, name, "pc", 7600, date(2026, 9, 15))
    pc.link_price_rows(cid, name, pid)
    lines = _po(cid, [{"item_name": name, "qty": 1}, {"item_name": "Free text", "qty": 1, "est_unit_price": 5}])["lines"]
    assert lines["item_id"].iloc[0] == pid and lines["item_id"].isna().iloc[1]
