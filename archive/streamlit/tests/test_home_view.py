"""Smoke test of the Mercury-style Home: the top bar with the welcome, the notice strip, the group bar and a yellow tile per page draw without an error."""
from streamlit.testing.v1 import AppTest


def _app():
    import streamlit as st

    from views import home, shared

    st.session_state.setdefault("auth_user", {"user_id": 1, "email": "o@x.c", "name": "Jeane", "role": "owner"})
    shared.PAGES.clear()
    shared.PAGES.update({"price-lists": st.Page(lambda: None, title="Price lists", url_path="price-lists"),
                         "purchase-orders": st.Page(lambda: None, title="Purchase orders", url_path="purchase-orders")})
    home.render()


def test_home_draws_the_top_bar_the_notice_and_the_tiles(db):
    from engines import settings_registry as sr
    assert sr.save_notice("No check release on Friday")[0]
    at = AppTest.from_function(_app, default_timeout=30).run()
    assert not at.exception, at.exception
    html = " ".join(str(h.proto) for h in at.get("html"))
    assert "Welcome, Jeane" in html and "No check release on Friday" in html and "home-groups" in html and "Purchase orders" in html
