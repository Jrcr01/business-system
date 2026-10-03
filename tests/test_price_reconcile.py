"""Reconciling prices with invoices (2026-10-03): the invoice price is the truth, so an item whose last invoice differs from the list is listed, and one step updates the list."""
from datetime import date

import app
from conftest import make_creditor
from engines import discount_rules as dr
from engines import price_reconcile as pr
from engines import product_catalog as pc
from engines.acting_user import acting_as
from engines.po_rules import get_po, order_po, receive_into_batch, save_po
from engines.price_list_service import add_price, price_on


def _supplier_with_invoice(db, invoiced_net):
    cid = make_creditor(db, name="Recon Supplier", terms="30")
    pid, name, _ = pc.get_or_create_product("Tires", "Westlake", size="285/50R20", terrain="A/T", pattern="SL369")
    with acting_as("buyer@example.com", "purchasing"):
        assert add_price(cid, name, "pc", 7600, date(2026, 9, 15))[0]
        pc.link_price_rows(cid, name, pid)
        assert dr.set_discount(cid, "Westlake", "5,5", date(2026, 9, 1))[0]
        ok, msg, po = save_po(None, cid, date(2026, 10, 1), date(2026, 10, 8), [{"item_name": name, "qty": 2}])
        assert ok and order_po(po)[0]
    line = int(get_po(po)["lines"].iloc[0]["line_id"])
    with acting_as("clerk@example.com", "payables"):
        sid = app.create_order_statement(cid)
        assert receive_into_batch(cid, sid, "SI-9", date(2026, 10, 9), [{"line_id": line, "qty": 2, "actual_unit_price": invoiced_net}])[0]
    return cid, name


def test_an_item_invoiced_at_the_listed_net_price_is_not_a_difference(db):
    cid, name = _supplier_with_invoice(db, 6859.00)                         # 7,600 less 5% less 5%
    assert pr.invoice_vs_list(cid, date(2026, 10, 10)).empty


def test_an_item_invoiced_at_another_price_is_listed_with_the_difference_and_its_invoice(db):
    cid, name = _supplier_with_invoice(db, 7200.00)
    df = pr.invoice_vs_list(cid, date(2026, 10, 10))
    assert len(df) == 1
    r = df.iloc[0]
    assert r["item_name"] == name and r["list_net"] == 6859.0 and r["invoiced"] == 7200.0 and r["difference"] == 341.0 and r["difference_pct"] == 5.0 and r["invoice_no"] == "SI-9"


def test_updating_to_the_invoice_price_works_the_list_price_back_through_the_discount_and_keeps_the_history(db):
    cid, name = _supplier_with_invoice(db, 7200.00)
    with acting_as("buyer@example.com", "purchasing"):
        ok, msg = pr.update_to_invoice(cid, name)
    assert ok, msg
    assert price_on(cid, name, date(2026, 10, 10))["unit_price"] == 7977.84                    # 7,200 / 0.9025
    assert price_on(cid, name, date(2026, 9, 20))["unit_price"] == 7600.0                      # the old price still applies before the invoice date
    assert pr.invoice_vs_list(cid, date(2026, 10, 10)).empty


def test_a_role_that_may_not_change_prices_cannot_update_them_and_an_unmatched_item_is_refused(db):
    cid, name = _supplier_with_invoice(db, 7200.00)
    with acting_as("viewer@example.com", "viewer"):
        assert not pr.update_to_invoice(cid, name)[0]
    with acting_as("buyer@example.com", "purchasing"):
        assert not pr.update_to_invoice(cid, "Never invoiced")[0]


def _prices_app():
    import streamlit as st

    from views import price_lists

    st.session_state.setdefault("auth_user", {"user_id": 1, "email": "a@b.c", "name": "A", "role": st.session_state.get("_role", "owner")})
    price_lists.render()


def test_the_prices_screen_shows_what_the_last_invoice_charged_and_only_a_price_writer_gets_the_update_button(db):
    from streamlit.testing.v1 import AppTest
    cid, name = _supplier_with_invoice(db, 7200.00)
    for role, button in (("owner", True), ("viewer", False)):
        at = AppTest.from_function(_prices_app, default_timeout=30)
        at.session_state["_role"] = role
        at.session_state["pl_supplier"] = cid
        at.session_state["pl_as_of"] = date(2026, 10, 10)
        at = at.run()
        assert not at.exception, (role, at.exception)
        assert any(b.key == "pl_recon_go" for b in at.button) is button, role
        assert [t.label for t in at.tabs][0] == "Prices"
