"""Recording checks the owner's mother hand-wrote (owner, 2026-09-30/10-01): "these are usually invoices that need a PDC at the same time, or need
correction". One save: the invoice (optional), the handwritten check(s) with their real numbers (marked printed, tagged handwritten, so they never go
to Chrysanth), tied to the invoice; and optionally "this replaces voided check no. ___". Everything is checked BEFORE anything is written, so a
refusal leaves no half-recorded invoice behind."""
import sqlite3
from datetime import date

import app
from conftest import fetchone, make_creditor
from engines.check_rules import cancel_check, link_replacement
from engines.check_run_service import get_pending_check_run
from engines.handwritten_check_service import record_handwritten_checks


def _supplier(db, name="Express Tire"):
    return make_creditor(db, name=name, terms="30")


def _draft_batch(db, creditor_id):
    return app.create_order_statement(creditor_id)


def _invoice(doc="INV-1", gross=30400.0, less=0.0):
    return {"doc_ref": doc, "invoice_date": date(2026, 9, 25), "gross": gross, "less_pct": less}


def _chk(number, amount, on=date(2026, 12, 5)):
    return {"number": number, "date": on, "amount": amount}


def test_a_new_invoice_with_a_handwritten_check_is_recorded_in_one_save(db):
    cid = _supplier(db)
    sid = _draft_batch(db, cid)

    ok, msg, ids = record_handwritten_checks(cid, [_chk("3992554297", 30400.0)], statement_id=sid, invoice=_invoice())

    assert ok, msg
    inv = fetchone(db, "SELECT doc_ref, amount FROM invoices WHERE creditor_id = ?", (cid,))
    assert inv == ("INV-1", 30400.0)                                          # an invoice turns "Paid" only once the check date arrives (post-dated check)
    chk = fetchone(db, "SELECT check_number, amount, status, is_handwritten, printed_at, statement_id FROM checks_issued WHERE check_id = ?", (ids[0],))
    assert chk[:4] == ("3992554297", 30400.0, "Active", 1) and chk[4] and chk[5] == sid
    assert fetchone(db, "SELECT SUM(amount) FROM invoice_check_allocations WHERE check_id = ?", (ids[0],))[0] == 30400.0
    assert get_pending_check_run().empty                                      # a handwritten check never goes to "Checks to print"
    assert "handwritten" in fetchone(db, "SELECT new_values FROM audit_logs WHERE user_action = 'HANDWRITTEN_CHECKS_RECORDED'")[0].lower()


def test_without_a_batch_a_new_one_is_created_for_the_invoice(db):
    cid = _supplier(db)

    ok, msg, ids = record_handwritten_checks(cid, [_chk("3992554297", 1000.0)], invoice=_invoice(gross=1000.0))

    assert ok, msg
    assert fetchone(db, "SELECT COUNT(*) FROM order_statements WHERE creditor_id = ?", (cid,))[0] == 1
    status, ref = fetchone(db, "SELECT status, statement_ref FROM order_statements WHERE creditor_id = ?", (cid,))
    assert status == "Issued" and " - PS " in ref                   # parking checks promotes a Draft batch to Issued with its permanent OS number
    assert fetchone(db, "SELECT statement_id FROM checks_issued WHERE check_id = ?", (ids[0],))[0] == fetchone(db, "SELECT statement_id FROM order_statements")[0]


def test_two_checks_split_one_invoice(db):
    cid = _supplier(db)
    sid = _draft_batch(db, cid)

    ok, msg, ids = record_handwritten_checks(cid, [_chk("3992554297", 20000.0, date(2026, 12, 5)), _chk("3992554298", 10400.0, date(2027, 1, 5))],
                                             statement_id=sid, invoice=_invoice())

    assert ok, msg
    assert len(ids) == 2
    assert fetchone(db, "SELECT COUNT(*), SUM(amount) FROM invoice_check_allocations") == (2, 30400.0)


def test_checks_only_go_against_the_invoices_already_in_the_batch(db):
    cid = _supplier(db)
    sid = _draft_batch(db, cid)
    app.add_invoice(cid, "OLD-1", date(2026, 9, 1), date(2026, 10, 1), 5000.0, [], sid)

    ok, msg, ids = record_handwritten_checks(cid, [_chk("3992554297", 5000.0)], statement_id=sid)

    assert ok, msg
    assert fetchone(db, "SELECT SUM(amount) FROM invoice_check_allocations WHERE check_id = ?", (ids[0],))[0] == 5000.0


def test_nothing_is_written_when_a_check_number_is_already_used(db):
    cid = _supplier(db)
    sid = _draft_batch(db, cid)
    assert record_handwritten_checks(cid, [_chk("3992554297", 100.0)], statement_id=sid, invoice=_invoice("A", 100.0))[0]

    ok, msg, ids = record_handwritten_checks(cid, [_chk("3992554297", 200.0)], statement_id=sid, invoice=_invoice("B", 200.0))

    assert not ok and "3992554297" in msg
    assert fetchone(db, "SELECT COUNT(*) FROM invoices")[0] == 1                      # invoice B was not left behind
    assert fetchone(db, "SELECT COUNT(*) FROM checks_issued")[0] == 1


def test_the_same_number_twice_in_one_save_is_refused(db):
    cid = _supplier(db)
    ok, msg, _ = record_handwritten_checks(cid, [_chk("3992554297", 100.0), _chk("3992554297", 100.0)], invoice=_invoice(gross=200.0))
    assert not ok and "twice" in msg.lower()
    assert fetchone(db, "SELECT COUNT(*) FROM invoices")[0] == 0


def test_a_duplicate_invoice_number_for_the_supplier_is_refused_and_nothing_is_written(db):
    cid = _supplier(db)
    sid = _draft_batch(db, cid)
    assert record_handwritten_checks(cid, [_chk("3992554297", 100.0)], statement_id=sid, invoice=_invoice("INV-1", 100.0))[0]
    sid2 = _draft_batch(db, cid)                  # the first batch is fully covered, so it locked itself: the same invoice number is tried in a new batch

    ok, msg, _ = record_handwritten_checks(cid, [_chk("3992554298", 100.0)], statement_id=sid2, invoice=_invoice("INV-1", 100.0))

    assert not ok and "INV-1" in msg
    assert fetchone(db, "SELECT COUNT(*) FROM checks_issued")[0] == 1


def test_checks_that_add_up_to_more_than_is_payable_are_refused_and_nothing_is_written(db):
    cid = _supplier(db)
    sid = _draft_batch(db, cid)

    ok, msg, _ = record_handwritten_checks(cid, [_chk("3992554297", 20000.0), _chk("3992554298", 20000.0)], statement_id=sid, invoice=_invoice(gross=30400.0))

    assert not ok and "exceed" in msg.lower()
    assert fetchone(db, "SELECT COUNT(*) FROM invoices")[0] == 0 and fetchone(db, "SELECT COUNT(*) FROM checks_issued")[0] == 0


def test_a_locked_batch_is_refused(db):
    cid = _supplier(db)
    sid = _draft_batch(db, cid)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE order_statements SET is_locked = 1 WHERE statement_id = ?", (sid,))

    ok, msg, _ = record_handwritten_checks(cid, [_chk("3992554297", 100.0)], statement_id=sid, invoice=_invoice(gross=100.0))

    assert not ok and "locked" in msg.lower()


def test_a_number_inside_a_registered_booklet_is_tied_to_that_booklet(db):
    assert app.create_booklet(1, "Mom - urgent", 3992554201, 3992554300, date(2026, 1, 1))[0]
    cid = _supplier(db)

    ok, msg, ids = record_handwritten_checks(cid, [_chk("3992554297", 100.0)], invoice=_invoice(gross=100.0))

    assert ok, msg
    assert fetchone(db, "SELECT booklet_id FROM checks_issued WHERE check_id = ?", (ids[0],))[0] == fetchone(db, "SELECT booklet_id FROM check_booklets")[0]


def test_a_handwritten_check_can_replace_a_voided_one_and_the_two_are_linked(db):
    cid = _supplier(db)
    sid = _draft_batch(db, cid)
    ok, _, first = record_handwritten_checks(cid, [_chk("3992565434", 30400.0)], statement_id=sid, invoice=_invoice())
    assert ok
    assert cancel_check(first[0], "wrong date")[0]
    assert fetchone(db, "SELECT COALESCE(SUM(amount), 0) FROM invoice_check_allocations")[0] == 0      # voided -> the invoice is open again

    ok, msg, ids = record_handwritten_checks(cid, [_chk("3992554297", 30400.0)], statement_id=sid, replaces_check_id=first[0])

    assert ok, msg
    old = fetchone(db, "SELECT status, replacement_check_id, replacement_reason FROM checks_issued WHERE check_id = ?", (first[0],))
    assert old[0] == "Replaced" and old[1] == ids[0] and "3992554297" in old[2]
    assert fetchone(db, "SELECT SUM(amount) FROM invoice_check_allocations WHERE check_id = ?", (ids[0],))[0] == 30400.0


def test_replacing_a_still_active_check_voids_it_first(db):
    cid = _supplier(db)
    sid = _draft_batch(db, cid)
    ok, _, first = record_handwritten_checks(cid, [_chk("3992565434", 30400.0)], statement_id=sid, invoice=_invoice())

    ok, msg, ids = record_handwritten_checks(cid, [_chk("3992554297", 30400.0)], statement_id=sid, replaces_check_id=first[0])

    assert ok, msg
    assert fetchone(db, "SELECT status FROM checks_issued WHERE check_id = ?", (first[0],))[0] == "Replaced"
    assert fetchone(db, "SELECT SUM(amount) FROM checks_issued WHERE status = 'Active'")[0] == 30400.0


def test_cannot_replace_a_check_of_another_supplier(db):
    cid, other = _supplier(db), _supplier(db, "Other Supplier")
    sid = _draft_batch(db, cid)
    ok, _, first = record_handwritten_checks(cid, [_chk("3992565434", 100.0)], statement_id=sid, invoice=_invoice(gross=100.0))
    cancel_check(first[0])
    sid2 = _draft_batch(db, other)

    ok, msg, _ = record_handwritten_checks(other, [_chk("3992554297", 100.0)], statement_id=sid2, invoice=_invoice("X", 100.0), replaces_check_id=first[0])

    assert not ok and "same supplier" in msg.lower()
    assert fetchone(db, "SELECT COUNT(*) FROM invoices")[0] == 1


def test_link_replacement_on_its_own(db):
    cid = _supplier(db)
    sid = _draft_batch(db, cid)
    ok, _, a = record_handwritten_checks(cid, [_chk("3992565434", 100.0)], statement_id=sid, invoice=_invoice(gross=100.0))
    cancel_check(a[0])
    ok, _, b = record_handwritten_checks(cid, [_chk("3992554297", 100.0)], statement_id=sid)
    assert ok

    ok, msg = link_replacement(a[0], b[0], "typed by hand")

    assert ok, msg
    assert fetchone(db, "SELECT status, replacement_check_id FROM checks_issued WHERE check_id = ?", (a[0],)) == ("Replaced", b[0])
    assert not link_replacement(a[0], b[0])[0]                                         # already replaced
    assert not link_replacement(b[0], b[0])[0]                                         # a check can't replace itself


def test_a_handwritten_check_for_a_loan_note(db):
    lender = make_creditor(db, name="Richie", creditor_type="LENDER")
    ok, _, loan_note_id = app.create_loan_note(lender, date(2026, 1, 1), 100000.0, 2.0)

    ok, msg, ids = record_handwritten_checks(lender, [_chk("3992554297", 20000.0)], loan_note_id=loan_note_id)

    assert ok, msg
    assert fetchone(db, "SELECT loan_note_id, is_handwritten FROM checks_issued WHERE check_id = ?", (ids[0],)) == (loan_note_id, 1)
    ok, msg, _ = record_handwritten_checks(lender, [_chk("3992554298", 999999.0)], loan_note_id=loan_note_id)
    assert not ok and "exceed" in msg.lower()
