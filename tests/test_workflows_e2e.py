"""Every workflow, start to finish, chained as the people really do it (2026-10-03, goal: test every workflow the system has). Each test is one business story told through the same
rules the screens call, with the right role doing each step; the last assertion of each is what the owner would check on the bank statement or in the books."""
import sqlite3
from datetime import date

import app
from conftest import fetchone, make_creditor
from engines import discount_rules as dr
from engines import product_catalog as pc
from engines import release_slip_service as rs
from engines import review_flags as rf
from engines.acting_user import acting_as
from engines.bank_statement_service import import_statement, match_checks
from engines.check_rules import assign_check_numbers_manual, cancel_check, issue_check, numbers_in_sequence
from engines.check_run_service import mark_checks_printed
from engines.po_rules import get_po, open_commitments, order_po, po_links_for_batch, receive_goods, receive_into_batch, save_po
from engines.price_list_service import add_price
from engines.statement_rules import add_credit_memo, add_invoice

HEADER = "Transaction Date,Posting Date,Time,Check Number,Transaction Description,Debit Amount,Credit Amount,Balance,Branch/Channel,Subscriber Number/TIN,Sequence Number"


def _csv(rows, opening=2_000_000.0):
    bal, out = opening, []
    for i, (d, chk, desc, debit, credit) in enumerate(rows, 1):
        bal = round(bal - debit + credit, 2)
        out.append(f'{d},{d},23:59:59,{chk},{desc},"{debit:,.2f}","{credit:,.2f}","{bal:,.2f}",,,{i}')
    return (HEADER + "\n" + "\n".join(out) + "\n").encode("utf-8")


def _db(db):
    return sqlite3.connect(db)


def test_the_supplier_cycle_from_price_list_to_the_bank(db):
    """Price -> order (brother) -> goods and invoice (clerk) -> batch -> check parked -> printed and numbered (owner) -> slip -> released (mother) -> cleared by the bank."""
    cid = make_creditor(db, name="Cycle Supplier", terms="30")
    pid, name, _ = pc.get_or_create_product("Tires", "Westlake", size="285/50R20", terrain="A/T", pattern="SL369")
    with acting_as("brother@example.com", "purchasing"):
        assert add_price(cid, name, "pc", 7600, date(2026, 9, 15))[0]
        pc.link_price_rows(cid, name, pid)
        assert dr.set_discount(cid, "Westlake", "5,5", date(2026, 9, 1))[0]
        ok, msg, po = save_po(None, cid, date(2026, 10, 1), date(2026, 10, 8), [{"item_name": name, "qty": 4}])
        assert ok, msg
        assert get_po(po)["total"] == 27436.0 and order_po(po)[0]
        line = int(get_po(po)["lines"].iloc[0]["line_id"])
        assert receive_goods(cid, [{"line_id": line, "qty": 4}], date(2026, 10, 8), "DR-100")[0]               # he receives; nothing flagged: he did not order it alone? he did
    assert [f for f in rf.open_flags()["kind"]] == ["SOD_ORDERED_RECEIVED"]                                   # he ordered and received it: allowed, and flagged for review
    assert open_commitments(date(2026, 10, 1), date(2027, 6, 1))["amount"].sum() == 27436.0                  # goods received, not yet invoiced: still owed
    with acting_as("clerk@example.com", "payables"):
        sid = app.create_order_statement(cid)
        ok, msg, inv = receive_into_batch(cid, sid, "SI-100", date(2026, 10, 9), [{"line_id": line, "qty": 4, "actual_unit_price": 6900}], goods_received=True, update_prices=True)
        assert ok, msg
        assert po_links_for_batch(sid).iloc[0]["difference"] == 164.0                                          # charged 27,600 against 27,436
        ok, msg, chk = issue_check(cid, 27600.0, date(2026, 11, 9), statement_id=sid)
        assert ok, msg
    assert fetchone(db, "SELECT on_hand, avg_cost FROM items WHERE id = ?", (pid,)) == (4, 6900.0)             # stock counts the price actually charged
    with acting_as("owner@example.com", "owner"):
        mark_checks_printed([chk])
        n = numbers_in_sequence("3992000501", 1)
        assert assign_check_numbers_manual({chk: n[0]}, None)[0]
    with acting_as("mother@example.com", "releaser"):
        ok, msg, slip = rs.create_slip(sid)
        assert ok, msg
        ok, msg = rs.release_slip(slip["slip_id"], "R. Cruz (Cycle Supplier)", date(2026, 11, 2))
        assert ok, msg
    assert fetchone(db, "SELECT released_to FROM checks_issued WHERE check_id = ?", (chk,))[0] == "R. Cruz (Cycle Supplier)"
    with acting_as("clerk@example.com", "payables"):
        res = import_statement(_csv([("11/12/2026", "3992000501", "IM IN- CLRNG- BATCH", 27600.0, 0.0)]), "nov.csv")
        match_checks()
    assert res["new_lines"] == 1 and fetchone(db, "SELECT bank_cleared_date FROM checks_issued WHERE check_id = ?", (chk,))[0] == "2026-11-12"
    assert open_commitments(date(2026, 10, 1), date(2027, 6, 1)).empty                                          # nothing is left owed on this order


def test_the_supplier_who_sends_the_invoice_with_the_goods_is_done_in_one_step(db):
    cid = make_creditor(db, name="One Step Supplier", terms="30")
    pid, name, _ = pc.get_or_create_product("Tires", "Westlake", size="195/65R15", terrain="H/T", pattern="RP28")
    with acting_as("brother@example.com", "purchasing"):
        assert add_price(cid, name, "pc", 2000, date(2026, 9, 15))[0]
        pc.link_price_rows(cid, name, pid)
        ok, msg, po = save_po(None, cid, date(2026, 10, 1), date(2026, 10, 8), [{"item_name": name, "qty": 10}])
        assert ok and order_po(po)[0]
    line = int(get_po(po)["lines"].iloc[0]["line_id"])
    with acting_as("clerk@example.com", "payables"):
        sid = app.create_order_statement(cid)
        assert receive_into_batch(cid, sid, "SI-ONE", date(2026, 10, 8), [{"line_id": line, "qty": 10, "actual_unit_price": 2000}])[0]
    assert get_po(po)["header"]["status"] == "RECEIVED" and fetchone(db, "SELECT on_hand FROM items WHERE id = ?", (pid,))[0] == 10
    assert rf.open_count() == 0                                                                                # a clerk receiving someone else's order: nothing to review


def test_a_credit_memo_a_void_and_a_replacement_keep_the_batch_balanced(db):
    cid = make_creditor(db, name="Memo Supplier", terms="30")
    sid = app.create_order_statement(cid)
    add_invoice(cid, "M-1", date(2026, 10, 1), date(2026, 11, 1), 10000.0, [], sid)
    with acting_as("clerk@example.com", "payables"):
        assert add_credit_memo(sid, "CM-1", date(2026, 10, 5), 1000.0, "returned tire")[0]                       # net 9,000
        ok, msg, c1 = issue_check(cid, 9000.0, date(2026, 11, 8), statement_id=sid)
        assert ok, msg
        assert not issue_check(cid, 1.0, date(2026, 11, 9), statement_id=sid)[0]                               # the batch is fully covered: nothing more may be paid
    with acting_as("owner@example.com", "owner"):
        assert cancel_check(c1, "wrong date")[0]
    with acting_as("clerk@example.com", "payables"):
        assert issue_check(cid, 9000.0, date(2026, 11, 15), statement_id=sid)[0]                                # the money went back to the batch: the replacement is allowed
    assert fetchone(db, "SELECT COUNT(*) FROM checks_issued WHERE status = 'Active'")[0] == 1


def test_the_loan_note_cycle_schedule_to_post_dated_checks_to_the_bank(db):
    from engines.loan_note_rules import create_loan_note, promote_draft_loan_note
    from engines.loan_schedule_service import generate_schedule, issue_all_pdcs
    cid = make_creditor(db, name="Richie Lender", creditor_type="LENDER", terms="0")
    with acting_as("clerk@example.com", "payables"):
        ok, msg, lnid = create_loan_note(cid, date(2026, 10, 1), 60000.0, 1.0)
        assert ok, msg
        ref = promote_draft_loan_note(lnid)
        assert " - LN " in ref
        assert generate_schedule(lnid, date(2026, 11, 1), 3)[0]
        ok, msg, n = issue_all_pdcs(lnid)
        assert ok and n == 3, msg
    ids = [r[0] for r in _db(db).execute("SELECT check_id FROM checks_issued WHERE loan_note_id = ? ORDER BY check_date", (lnid,))]
    assert len(ids) == 3
    with acting_as("owner@example.com", "owner"):
        mark_checks_printed(ids)
        assert assign_check_numbers_manual(dict(zip(ids, numbers_in_sequence("3992000601", 3))), None)[0]
    with acting_as("clerk@example.com", "payables"):
        from engines.pdc_release_service import mark_released
        assert mark_released(ids, date(2026, 10, 20), "Richie")[0]
    with acting_as("clerk@example.com", "payables"):
        import_statement(_csv([("11/03/2026", "3992000601", "IM IN- CLRNG- BATCH", 20600.0, 0.0)]), "nov.csv")
        match_checks()
    assert fetchone(db, "SELECT COUNT(*) FROM checks_issued WHERE loan_note_id = ? AND bank_cleared_date IS NOT NULL", (lnid,))[0] == 1


def test_a_bank_loan_installment_is_matched_to_the_banks_debit(db):
    from engines.bank_loan_service import create_bank_loan, fill_flat_schedule
    from engines import data_health_service as dh
    bank = make_creditor(db, name="Metrobank Loans", creditor_type="BANK_LOAN", terms="0")
    blid = create_bank_loan(bank, "BL-77", "", date(2026, 9, 1), date(2027, 9, 1), None, 300000.0, "ADA")
    assert fill_flat_schedule(blid, date(2026, 10, 3), 3, 25000.0)[0]
    # a payment check, so the system has a first check date before the bank lines
    cid = make_creditor(db, name="Anchor Supplier")
    sid = app.create_order_statement(cid)
    add_invoice(cid, "A-1", date(2026, 9, 1), date(2026, 10, 1), 100.0, [], sid)
    with acting_as("owner@example.com", "owner"):
        assert issue_check(cid, 100.0, date(2026, 9, 25), statement_id=sid)[0]
    import_statement(_csv([("10/03/2026", "0000000000", "MO DM", 25000.0, 0.0), ("10/20/2026", "0000000000", "MO CSH DEP", 0.0, 5000.0)]), "oct.csv")
    row = _db(db).execute("SELECT match_status FROM bank_transactions WHERE debit = 25000").fetchone()
    assert row[0] == "matched_loan"
    flagged = [f for f in dh.findings(today=date(2026, 10, 25)) if f["check"] == "Bank loan installment with no bank debit"]
    assert not flagged                                                                                          # the Oct 3 installment has its debit; the later ones are not due yet


def test_a_credit_card_statement_is_paid_by_the_banks_bills_payment(db):
    from engines.card_rules import add_cardholder, add_cc_statement, add_credit_card
    issuer = make_creditor(db, name="BPI", creditor_type="CREDIT_CARD", terms="0")
    holder = add_cardholder("Rhodora Roxas", "Owner", "")
    ok, msg = add_credit_card(issuer, holder, "Mastercard", "4242", 100000.0, None, 15)
    assert ok, msg
    card = _db(db).execute("SELECT card_id FROM credit_cards").fetchone()[0]
    sid_c = add_cc_statement(card, date(2026, 10, 8), 0.0, 8000.0, 0.0, 0.0, "2026-09")
    cid = make_creditor(db, name="Anchor 2")
    sid = app.create_order_statement(cid)
    add_invoice(cid, "B-1", date(2026, 9, 1), date(2026, 10, 1), 100.0, [], sid)
    with acting_as("owner@example.com", "owner"):
        assert issue_check(cid, 100.0, date(2026, 9, 25), statement_id=sid)[0]
    assert fetchone(db, "SELECT status FROM cc_statements WHERE cc_statement_id = ?", (sid_c,))[0] == "Unpaid"
    import_statement(_csv([("10/09/2026", "0000000000", "MO DM PAY UTLTY", 8000.0, 0.0)]), "oct.csv")
    assert fetchone(db, "SELECT status, payment_date FROM cc_statements WHERE cc_statement_id = ?", (sid_c,)) == ("Paid", "2026-10-09")


def test_a_handwritten_check_and_a_guarantee_check_are_recorded_without_touching_the_print_run(db):
    from engines.guarantee_check_service import record_guarantee_check
    from engines.handwritten_check_service import record_handwritten_checks
    from engines.check_run_service import get_pending_check_run
    cid = make_creditor(db, name="Hand Supplier", terms="30")
    lender = make_creditor(db, name="Hand Lender", creditor_type="LENDER", terms="0")
    with acting_as("owner@example.com", "owner"):
        ok, msg, ids = record_handwritten_checks(cid, [{"number": "3992000701", "date": date(2026, 10, 10), "amount": 4000.0}],
                                                 invoice={"doc_ref": "HW-1", "invoice_date": date(2026, 10, 1), "gross": 4000.0, "less_pct": 0})
        assert ok, msg
        ok, msg, gid = record_guarantee_check(lender, "3992000702", 500000.0, date(2026, 10, 2))
        assert ok, msg
    assert get_pending_check_run().empty                                                                        # neither goes to Chrysanth
    assert fetchone(db, "SELECT is_handwritten FROM checks_issued WHERE check_id = ?", (ids[0],))[0] == 1


def test_a_new_supplier_by_the_clerk_is_used_at_once_flagged_and_ticked_off_by_a_family_owner(db):
    from engines.creditor_rules import add_creditor
    with acting_as("clerk@example.com", "payables"):
        cid = add_creditor("Brand New Trading", "SUPPLIER", "", "", "", "")
        sid = app.create_order_statement(cid)
        add_invoice(cid, "N-1", date(2026, 10, 1), date(2026, 11, 1), 1000.0, [], sid)
        assert issue_check(cid, 1000.0, date(2026, 11, 8), statement_id=sid)[0]                                  # usable at once
    flags = rf.open_flags()
    assert list(flags["kind"]) == ["NEW_PAYEE"]
    with acting_as("family@example.com", "owner"):
        assert rf.acknowledge(int(flags.iloc[0]["flag_id"]))[0]
    assert rf.open_count() == 0


def test_the_first_user_is_the_system_admin_and_adds_people_who_sign_in(db):
    from engines import auth_service as au
    assert au.needs_setup()
    ok, msg, admin_id = au.create_owner("jrcr@example.com", "JRCR", "Strong-Pass-1")
    assert ok, msg
    with acting_as("jrcr@example.com", "admin"):
        ok, msg, uid, temp = au.create_user("brother@example.com", "Brother", "purchasing")
        assert ok, msg
        ok, msg, mother, temp2 = au.create_user("mother@example.com", "Mother", "releaser")
        assert ok, msg
    ok, user, msg = au.authenticate("brother@example.com", temp)
    assert ok and user["role"] == "purchasing" and user["must_change_password"] == 1
    with acting_as("brother@example.com", "purchasing"):
        assert not au.set_role(uid, "admin")[0]                                                                  # only the System admin changes roles


def test_marking_a_day_makes_a_new_order_skip_it_but_leaves_an_invoices_due_date_alone(db):
    """Marked days are skipped by a NEW order's pay date (and the planner); an invoice's stored due date is not moved."""
    from engines.calendar_marks import mark_day, unmark_day
    from engines.po_rules import expected_pay_date
    from engines.statement_rules import resolve_due_date
    cid = make_creditor(db, name="Calendar Supplier", terms="30")
    delivery = date(2026, 10, 7)                                                                                # 30 days later: Fri Nov 6 (the same day of the next month is Nov 7, a Saturday: Monday)
    before = expected_pay_date(cid, delivery)
    assert before.weekday() < 5
    invoice_due = resolve_due_date(delivery, "30")
    with acting_as("clerk@example.com", "payables"):
        assert mark_day(before, "custom", "No checks")[0]
        after = expected_pay_date(cid, delivery)
        assert after > before and after.weekday() < 5
        assert resolve_due_date(delivery, "30") == invoice_due                                                    # an invoice's due date is its own rule: unchanged by the mark
        assert unmark_day(before)[0]
    assert expected_pay_date(cid, delivery) == before
