"""The control register and My to-do (2026-10-03)."""
from datetime import date
from pathlib import Path

from conftest import make_creditor
from engines import my_todo
from engines.acting_user import acting_as
from engines.controls import CONTROLS, KIND_LABEL
from engines.po_rules import order_po, receive_goods, save_po, get_po

TESTS = Path(__file__).resolve().parent


def test_every_control_names_a_test_that_exists_and_only_four_things_are_refused():
    assert {c[1] for c in CONTROLS} <= set(KIND_LABEL)
    for process, kind, control, what, test in CONTROLS:
        assert (TESTS / test).exists(), (control, test)
        assert control and what
    assert len([c for c in CONTROLS if c[1] == "STOP"]) == 4


def test_my_todo_shows_a_role_only_its_own_steps(db):
    cid = make_creditor(db, name="Todo Supplier")
    with acting_as("buyer@example.com", "purchasing"):
        ok, msg, po = save_po(None, cid, date(2026, 9, 1), date(2026, 9, 5), [{"item_name": "Tire", "qty": 2, "est_unit_price": 100}])
        assert ok and order_po(po)[0]
    buyer = [i["text"] for i in my_todo.items("purchasing")]
    assert any("waiting for delivery" in t and "late" in t for t in buyer) and not any("invoice" in t or "slip" in t for t in buyer)
    assert my_todo.items("viewer") == []
    assert not any("invoice" in i["text"] for i in my_todo.items("releaser"))                    # the Releaser has no invoices to match
    line = int(get_po(po)["lines"].iloc[0]["line_id"])
    with acting_as("clerk@example.com", "payables"):
        assert receive_goods(cid, [{"line_id": line, "qty": 2}])[0]
    clerk = my_todo.items("payables")
    assert any("the SI or DR not recorded yet" in i["text"] and i["state"] == {"po_mode": "Match invoice"} for i in clerk)
    assert not any("delivery" in i["text"] and "late" in i["text"] for i in clerk)               # the order is received: nothing late is left waiting


def test_the_main_ok_checklist_is_ticked_by_the_system_admin_with_who_and_when(db):
    from engines import main_ok
    assert main_ok.progress() == (0, 6)
    with acting_as("family@example.com", "owner"):
        assert not main_ok.tick("release_slip", True)[0]                                   # an owner may look, not decide
    with acting_as("jrcr@example.com", "admin"):
        assert main_ok.tick("release_slip", True)[0] and not main_ok.tick("nonsense", True)[0]
    item = next(i for i in main_ok.status() if i["key"] == "release_slip")
    assert item["done"] and item["by"] == "jrcr@example.com" and item["on"] and main_ok.progress() == (1, 6)
    with acting_as("jrcr@example.com", "admin"):
        assert main_ok.tick("release_slip", False)[0]
    assert main_ok.progress() == (0, 6)
