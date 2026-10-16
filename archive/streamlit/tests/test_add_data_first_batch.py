"""Add data (2026-10-03): a supplier with no open batch gets one automatically when you pick them to add invoices: no separate 'Create the new batch' click first."""
from streamlit.testing.v1 import AppTest

from conftest import fetchone, make_creditor


def _app():
    import streamlit as st

    from views import add_data

    st.session_state.setdefault("auth_user", {"user_id": 1, "email": "a@b.c", "name": "A", "role": "admin"})
    add_data.render()


def test_picking_a_supplier_with_no_batch_makes_the_first_batch_once(db):
    cid = make_creditor(db, name="Fresh Supplier")
    at = AppTest.from_function(_app, default_timeout=60)
    at.session_state["ad_supplier"] = cid
    at = at.run()
    assert not at.exception, at.exception
    assert fetchone(db, "SELECT COUNT(*) FROM order_statements WHERE creditor_id = ?", (cid,))[0] == 1
    at = at.run()                                                                         # looking again makes no second batch
    assert not at.exception, at.exception
    assert fetchone(db, "SELECT COUNT(*) FROM order_statements WHERE creditor_id = ?", (cid,))[0] == 1
