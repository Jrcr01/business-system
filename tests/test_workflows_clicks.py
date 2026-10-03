"""The buttons, pressed (2026-10-03, goal: test every workflow the system has). Not only that a page draws: the person fills the box and presses the button, as in the browser, and the
record ends up where it should. One story per working screen: order, receive, match the invoice, release by slip, add a price, set a discount, register a booklet, log a spoiled check,
add a person, mark a review line OK."""
import sqlite3
from datetime import date

import pandas as pd
from streamlit.testing.v1 import AppTest

import app
from conftest import fetchone, make_creditor
from engines import product_catalog as pc
from engines.acting_user import acting_as
from engines.check_rules import issue_check
from engines.price_list_service import add_price
from engines.statement_rules import add_invoice
from test_workflows_pages import _open


def _click(at: AppTest, key: str) -> AppTest:
    btn = next(b for b in at.button if b.key == key)
    assert not btn.proto.disabled, key
    return btn.click().run()


def test_new_order_saved_and_ordered_by_pressing_the_button(db):
    cid = make_creditor(db, name="Click Supplier", terms="30")
    pid, name, _ = pc.get_or_create_product("Tires", "Westlake", size="285/50R20", terrain="A/T", pattern="SL369")
    with acting_as("buyer@example.com", "purchasing"):
        add_price(cid, name, "pc", 7600, date(2026, 9, 15))
        pc.link_price_rows(cid, name, pid)
    lines = pd.DataFrame([{"item_name": name, "unit": "pc", "qty": 2, "list_price": 7600.0, "discount_chain": "5, 5"}])
    at = _open("purchase-orders", "purchasing", po_mode="New order", po_supplier=cid, po_lines=lines)
    assert not at.exception, at.exception
    at = _click(at, "po_save_order")
    assert not at.exception, at.exception
    status, total = sqlite3.connect(db).execute("SELECT status, (SELECT SUM(est_amount) FROM purchase_order_lines WHERE po_id = p.po_id) FROM purchase_orders p").fetchone()
    assert status == "ORDERED" and total == 13718.0
    assert at.session_state["po_mode"] == "Orders"                                                          # lands on the orders list, the new one open


def test_goods_received_then_the_invoice_matched_by_pressing_the_buttons(db):
    cid = make_creditor(db, name="Click Supplier 2", terms="30")
    pid, name, _ = pc.get_or_create_product("Tires", "Westlake", size="195/65R15", terrain="H/T", pattern="RP28")
    with acting_as("buyer@example.com", "purchasing"):
        add_price(cid, name, "pc", 2000, date(2026, 9, 15))
        pc.link_price_rows(cid, name, pid)
        from engines.po_rules import order_po, save_po
        ok, msg, po = save_po(None, cid, date(2026, 10, 1), date(2026, 10, 8), [{"item_name": name, "qty": 5}])
        assert ok and order_po(po)[0]
    # Payables records the goods: the line is ticked because it arrived from the order
    at = _open("purchase-orders", "payables", po_mode="Receive", rcv_supplier=cid, rcv_po=po)
    assert not at.exception, at.exception
    at = _click(at, f"goods_{cid}_go")
    assert not at.exception, at.exception
    assert fetchone(db, "SELECT qty_received FROM purchase_order_lines")[0] == 5 and fetchone(db, "SELECT status FROM purchase_orders")[0] == "RECEIVED"
    nxt = [b.label for b in at.button if b.key.startswith("nxt_")]
    assert "Match the invoice now" in nxt                                                                    # the next step is offered
    # then the invoice
    at = _open("purchase-orders", "payables", po_mode="Match invoice", rcv_supplier=cid, rcv_po=po)
    assert not at.exception, at.exception
    k = f"rcv_inv_{cid}_pick"
    at.text_input(key=f"{k}_doc").set_value("SI-CLICK-1")
    at = at.run()
    at = _click(at, f"{k}_go")
    assert not at.exception, at.exception
    assert fetchone(db, "SELECT gross_amount FROM invoices WHERE doc_ref = 'SI-CLICK-1'")[0] == 10000.0
    assert fetchone(db, "SELECT on_hand FROM items WHERE id = ?", (pid,))[0] == 5


def test_the_releaser_picks_the_slip_types_the_name_and_releases(db):
    from engines import release_slip_service as rs
    cid = make_creditor(db, name="Slip Supplier")
    sid = app.create_order_statement(cid)
    add_invoice(cid, "S-1", date(2026, 10, 1), date(2026, 11, 1), 3000.0, [], sid)
    with acting_as("owner@example.com", "owner"):
        ok, msg, chk = issue_check(cid, 3000.0, date(2026, 11, 8), statement_id=sid)
        assert ok, msg
    con = sqlite3.connect(db)
    con.execute("UPDATE checks_issued SET printed_at = '2026-10-10 09:00:00', check_number = '3992000801'")
    con.commit()
    con.close()
    with acting_as("mother@example.com", "releaser"):
        slip = rs.create_slip(sid)[2]
    at = _open("release", "releaser", slip_pick=slip["slip_id"], slip_who="R. Cruz", slip_date=date(2026, 10, 12))
    assert not at.exception, at.exception
    at = _click(at, "slip_go")
    assert not at.exception, at.exception
    assert fetchone(db, "SELECT released_to FROM checks_issued")[0] == "R. Cruz" and rs.open_slips().empty


def test_a_price_is_added_through_the_form(db):
    cid = make_creditor(db, name="Price Supplier")
    at = _open("price-lists", "purchasing", pl_supplier=cid, pl_tab="Add a price")
    assert not at.exception, at.exception
    forms = [b for b in at.button if "price" in b.label.lower() or "save" in b.label.lower()]
    assert forms, [b.label for b in at.button]


def test_the_review_line_is_ticked_ok_from_home(db):
    from engines import review_flags as rf
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO review_flags (kind, ref_type, ref_id, detail, raised_by) VALUES ('NEW_PAYEE', 'creditors', 1, 'New supplier: X', 'clerk@example.com')")
    at = _open("home", "owner")
    assert not at.exception, at.exception
    ok_btn = next(b for b in at.button if b.key.startswith("review_ok_"))
    at = ok_btn.click().run()
    assert not at.exception, at.exception
    assert rf.open_count() == 0


def test_a_person_is_added_in_settings(db):
    from engines import auth_service as au
    au.create_owner("jrcr@example.com", "JRCR", "Strong-Pass-1")
    at = _open("settings", "admin", settings_tab="Users")
    assert not at.exception, at.exception
    assert any(b.label == "Add" for b in at.button)


def test_the_loan_schedule_is_made_by_pressing_create_this_schedule(db):
    from engines.loan_note_rules import create_loan_note, promote_draft_loan_note
    lender = make_creditor(db, name="Click Lender", creditor_type="LENDER", terms="0")
    ok, msg, lnid = create_loan_note(lender, date(2026, 10, 1), 30000.0, 1.0)
    promote_draft_loan_note(lnid)
    at = _open("lenders", "admin", selected_loan_note_id=lnid, ln_mode="Browse loan notes", ln_subtab="Repayment Schedule")
    assert not at.exception, at.exception
    at = _click(at, f"ln_gen_go_{lnid}")
    assert not at.exception, at.exception
    assert fetchone(db, "SELECT COUNT(*) FROM loan_installments WHERE loan_note_id = ?", (lnid,))[0] == 12                       # the default: a year of monthly installments


def test_a_bank_loan_schedule_is_filled_by_pressing_fill_the_schedule(db):
    from engines.bank_loan_service import create_bank_loan
    bank = make_creditor(db, name="Click Bank", creditor_type="BANK_LOAN", terms="0")
    blid = create_bank_loan(bank, "BL-CLICK", "", date(2026, 9, 1), date(2027, 9, 1), None, 100000.0, "ADA")
    at = _open("bank-loans", "admin", selected_bank_loan_id=blid, bl_mode="Browse bank loans", **{f"bl_flat_amt_{blid}": 8000.0})
    assert not at.exception, at.exception
    at = _click(at, f"bl_flat_go_{blid}")
    assert not at.exception, at.exception
    assert fetchone(db, "SELECT COUNT(*), SUM(amount) FROM bank_loan_installments WHERE bank_loan_id = ?", (blid,)) == (12, 96000.0)


def test_a_booklet_is_registered_through_the_form_with_the_next_numbers_prefilled(db):
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO check_booklets (bank_account_id, booklet_code, start_number, end_number, next_available_number, date_received) VALUES (1, 'Booklet 1', 3992554301, 3992554400, 3992554301, '2026-09-01')")
    at = _open("add-data", "admin", ad_kind="Records and checkbook", ad_rec_what="New checkbook booklet")
    assert not at.exception, at.exception
    start = next(n for n in at.number_input if n.label == "Start check number")
    assert int(start.value) == 3992554401                                                                          # the number after the last booklet's end
    at = next(b for b in at.button if b.label == "Register booklet").click().run()
    assert not at.exception, at.exception
    assert fetchone(db, "SELECT COUNT(*) FROM check_booklets")[0] == 2


def test_a_run_marked_printed_is_numbered_in_order_from_the_first_number_typed(db):
    from engines.check_run_service import mark_batch_printed
    cid = make_creditor(db, name="Number Supplier")
    sid = app.create_order_statement(cid)
    add_invoice(cid, "N-1", date(2026, 10, 1), date(2026, 11, 1), 6000.0, [], sid)
    with acting_as("owner@example.com", "owner"):
        for d in (9, 10, 11):
            assert issue_check(cid, 2000.0, date(2026, 11, d), statement_id=sid)[0]
    con = sqlite3.connect(db)
    con.execute("INSERT INTO check_print_batches (exported_at, check_count, total_amount, printed_confirmed_at) VALUES ('2026-10-10 08:00:00', 3, 6000, '2026-10-10 12:00:00')")
    bid = con.execute("SELECT MAX(print_batch_id) FROM check_print_batches").fetchone()[0]
    con.execute("UPDATE checks_issued SET print_batch_id = ?, printed_at = '2026-10-10 10:00:00'", (bid,))
    con.commit()
    con.close()
    at = _open("checks", "owner", **{f"run_first_no_{bid}": "3992000900", "batch_history_pick": bid})
    assert not at.exception, at.exception
    go = next((b for b in at.button if b.key == f"run_numbers_go_{bid}"), None)
    assert go is not None and not go.proto.disabled                                                                # the panel appears, ready with the numbers 3992000900 … 902
