"""Smoke test of the Price lists screen: every tab draws without an error, and a role that may only look is not offered the tabs that change prices."""
from streamlit.testing.v1 import AppTest

from conftest import make_creditor


def _app():
    import streamlit as st

    from views import price_lists

    st.session_state.setdefault("auth_user", {"user_id": 1, "email": "a@b.c", "name": "A", "role": st.session_state.get("_role", "owner")})
    price_lists.render()


def _run(role: str) -> AppTest:
    at = AppTest.from_function(_app, default_timeout=30)
    at.session_state["_role"] = role
    return at.run()


def test_the_owner_sees_every_tab_and_none_fail(db):
    make_creditor(db, name="New Century")
    at = _run("owner")
    assert not at.exception, at.exception
    assert [t.label for t in at.tabs] == ["Current prices", "Add a price", "Import Excel", "Discounts", "Products"]


def test_a_viewer_sees_only_the_prices_and_the_catalog(db):
    make_creditor(db, name="New Century")
    at = _run("viewer")
    assert not at.exception, at.exception
    assert [t.label for t in at.tabs] == ["Current prices", "Products"]
