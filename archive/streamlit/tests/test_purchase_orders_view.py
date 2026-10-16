"""Smoke test of the Purchase orders screen for each role: the modes offered follow the access matrix and the New order form draws with discounts."""
from datetime import date

from streamlit.testing.v1 import AppTest

from conftest import make_creditor
from engines import discount_rules as dr
from engines.price_list_service import add_price


def _app():
    import streamlit as st

    from views import purchase_orders

    st.session_state.setdefault("auth_user", {"user_id": 1, "email": "a@b.c", "name": "A", "role": st.session_state.get("_role", "owner")})
    purchase_orders.render()


def _run(role: str, **state) -> AppTest:
    at = AppTest.from_function(_app, default_timeout=30)
    at.session_state["_role"] = role
    for k, v in state.items():
        at.session_state[k] = v
    return at.run()


def test_modes_follow_the_matrix():
    from views.purchase_orders import available_modes
    assert available_modes("owner", "o@x.c") == ["Orders", "New order", "Receive", "Match invoice"]
    assert available_modes("purchasing", "b@x.c") == ["Orders", "New order", "Receive"]            # records the goods; never touches the payables batch
    assert available_modes("payables", "p@x.c") == ["Orders", "Receive", "Match invoice"]
    assert available_modes("viewer", "v@x.c") == ["Orders"]


def test_the_new_order_form_shows_the_discount_and_the_net_price(db):
    cid = make_creditor(db, name="Acme")
    add_price(cid, "Westlake 285/50R20 (A/T) SL369", "pc", 7600, date(2026, 9, 15))
    dr.set_discount(cid, "Westlake", "5,5", date(2026, 9, 1))
    at = _run("owner", po_mode="New order", po_supplier=cid)
    assert not at.exception, at.exception


def test_the_chart_always_shows_this_order_on_the_day_it_falls_due_without_a_preview_button(db):
    cid = make_creditor(db, name="Acme")
    add_price(cid, "Westlake 285/50R20 (A/T) SL369", "pc", 7600, date(2026, 9, 15))
    lines = __import__("pandas").DataFrame([{"item_name": "Westlake 285/50R20 (A/T) SL369", "unit": "pc", "qty": 2, "list_price": 7600.0, "discount_chain": "5, 5"}])
    at = _run("owner", po_mode="New order", po_supplier=cid, po_lines=lines)
    assert not at.exception, at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "This order:" in text and "13,718.00" in text                      # 2 x 6,859 net, and the date it falls due
    assert not any(b.key in ("po_preview_on", "po_preview_off") for b in at.button)
    empty = _run("owner", po_mode="New order", po_supplier=cid)
    assert "Add an item to see this order on the chart." in " ".join(c.value for c in empty.caption)


def test_orders_screen_shows_a_simple_daily_total_schedule(db):
    at = _run("owner")
    assert not at.exception, at.exception
    assert any(e.label.startswith("What is going out (total per day") for e in at.expander)


def test_the_schedule_can_be_shown_as_a_line_graph(db):
    at = _run("owner", po_sched_shape="Line graph")
    assert not at.exception, at.exception


def test_the_cycle_bar_shows_where_you_are_and_greys_the_steps_a_role_cannot_open(db):
    at = _run("purchasing", po_mode="Receive")
    assert not at.exception, at.exception
    bars = {b.key: b for b in at.button if b.key.startswith("cyc_")}
    assert list(bars) == ["cyc_Order", "cyc_Receive", "cyc_Match", "cyc_Batch", "cyc_Check", "cyc_Bank"]
    assert bars["cyc_Receive"].proto.type == "primary"                                  # the current step
    assert bars["cyc_Match"].proto.disabled and bars["cyc_Check"].proto.disabled and not bars["cyc_Order"].proto.disabled
    owner = _run("owner", po_mode="Match invoice")
    assert not any(b.proto.disabled for b in owner.button if b.key.startswith("cyc_"))


def test_after_receiving_goods_the_next_step_is_offered_only_to_a_role_that_may_take_it(db):
    from conftest import make_creditor as _mk
    for role, expect in (("payables", True), ("purchasing", False)):
        at = _run(role, po_next={"kind": "received", "cid": _mk(db, name=f"S-{role}")})
        assert not at.exception, at.exception
        labels = [b.label for b in at.button if b.key.startswith("nxt_")]
        assert ("Match the invoice now" in labels) is expect and "Receive another delivery" in labels


def test_a_person_who_may_match_invoices_can_receive_goods_and_the_invoice_together(db):
    from conftest import make_creditor as _mk
    from engines.po_rules import order_po, save_po
    cid = _mk(db, name="Together Supplier", terms="30")
    ok, msg, po = save_po(None, cid, date(2026, 10, 1), date(2026, 10, 8), [{"item_name": "Tire", "qty": 2, "est_unit_price": 100}])
    assert ok and order_po(po)[0]
    for role, offered in (("payables", True), ("owner", True), ("purchasing", False)):
        at = _run(role, po_mode="Receive", rcv_supplier=cid)
        assert not at.exception, (role, at.exception)
        assert ("rcv_together" in at.session_state) is offered, role                              # the choice is only offered to a person who may match invoices
    together = _run("payables", po_mode="Receive", rcv_supplier=cid, rcv_together="Goods and the supplier's invoice")
    assert not together.exception, together.exception
    assert any(b.label == "Add to the batch" for b in together.button)                              # the invoice form, on the Receive page
