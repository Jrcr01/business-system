"""The Streamlit login view (archived 2026-10-17 with the prototype)."""
import pytest  # noqa: F401


def test_the_sign_in_can_be_skipped_only_on_a_test_database(monkeypatch):
    from views import login
    monkeypatch.setenv("AP_SKIP_LOGIN", "1")
    monkeypatch.setenv("AP_DB_PATH", "data/ap_disbursement_TEST.db")
    assert login._sign_in_off() is True
    monkeypatch.setenv("AP_DB_PATH", "data/ap_disbursement.db")
    assert login._sign_in_off() is False                              # never on the real database
    monkeypatch.setenv("AP_DB_PATH", "data/ap_disbursement_TEST.db")
    monkeypatch.delenv("AP_SKIP_LOGIN")
    assert login._sign_in_off() is False


# moved from tests/test_permissions.py (2026-10-17): the Streamlit login's view-as
from engines.permissions import filter_menu  # noqa: E402


def test_the_system_admin_can_view_the_system_as_another_role_without_changing_any_role(monkeypatch):
    import streamlit as st
    from views import login
    owner = {"user_id": 1, "email": "o@x.c", "name": "O", "role": "admin", "is_active": 1, "must_change_password": 0}
    st.session_state["view_as"] = "purchasing"
    seen = login._apply_view_as(owner)
    assert seen["role"] == "purchasing" and seen["real_role"] == "admin" and owner["role"] == "admin" and seen["email"] == "o@x.c"
    assert filter_menu({"Home": ["home"], "Daily": ["checks"], "Purchasing": ["purchase-orders"]}, seen["role"]) == {"Home": ["home"], "Purchasing": ["purchase-orders"]}
    st.session_state["view_as"] = "janitor"
    assert login._apply_view_as(owner) is owner                                  # an unknown role is ignored
    st.session_state["view_as"] = "admin"
    assert login._apply_view_as(owner) is owner
    family = {**owner, "role": "owner"}
    st.session_state["view_as"] = "viewer"
    assert login._apply_view_as(family) is family                                # a family owner has no developer view
    clerk = {**owner, "role": "payables"}
    st.session_state["view_as"] = "viewer"
    assert login._apply_view_as(clerk) is clerk                                  # only a real System admin can do this
    st.session_state.pop("view_as", None)
