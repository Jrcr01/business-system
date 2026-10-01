"""Guarantee checks (owner, 2026-10-01: "check Luz 2M ... usually no date, guarantee checks, later cancelled or withdrawn once we settled the loan").
A guarantee check is given to a lender as security; it is NOT a payment. It is recorded on its own list (holder, optional loan note, optional face amount),
stays out of payables / the schedule / the cash forecast, counts as an accounted-for leaf in its booklet, and is closed with "Returned" when the loan is
settled. If the bank ever pays one, the bank line is flagged and the guarantee turns "Presented"."""
import sqlite3
from datetime import date, timedelta

import app
from conftest import fetchone, make_creditor
from engines.bank_statement_service import exceptions, import_statement
from engines.check_rules import assign_check_number
from engines.checkbook_service import unrecorded_leaves
from engines.data_health_service import findings as scan
from engines.guarantee_check_service import (
    exposure, guarantee_checks, record_guarantee_check, return_guarantee_check, undo_return_guarantee,
)
from engines.reports_service import get_payment_schedule_all_tracks
from test_bank_statement_import import _csv, _row

LEAF = "3992554212"


def _lender(db, name="Luz"):
    return make_creditor(db, name=name, creditor_type="LENDER")


def test_a_guarantee_check_is_recorded_as_held_without_a_date_or_amount_on_the_check(db):
    luz = _lender(db)

    ok, msg, gid = record_guarantee_check(luz, LEAF, face_amount=2_000_000.0, given_on=date(2026, 8, 15), note='old file: "2M"')

    assert ok, msg
    row = fetchone(db, "SELECT check_number, creditor_id, face_amount, given_on, status FROM guarantee_checks WHERE guarantee_id = ?", (gid,))
    assert row == (LEAF, luz, 2_000_000.0, "2026-08-15", "Held")
    assert fetchone(db, "SELECT COUNT(*) FROM checks_issued")[0] == 0                       # it is not a payment
    assert "2M" in fetchone(db, "SELECT new_values FROM audit_logs WHERE user_action = 'GUARANTEE_CHECK_RECORDED'")[0]


def test_the_face_amount_may_be_left_out_and_a_short_number_is_made_full_length(db):
    luz = _lender(db)
    ok, msg, gid = record_guarantee_check(luz, "2554212")
    assert ok, msg
    assert fetchone(db, "SELECT check_number, face_amount FROM guarantee_checks")[0] == LEAF
    assert fetchone(db, "SELECT face_amount FROM guarantee_checks")[0] is None


def test_refusals(db):
    luz = _lender(db)
    other = _lender(db, "Felix")
    assert not record_guarantee_check(luz, "")[0]
    assert not record_guarantee_check(luz, "ABC123")[0]
    assert not record_guarantee_check(9999, LEAF)[0]
    assert not record_guarantee_check(luz, LEAF, face_amount=-1)[0]
    assert not record_guarantee_check(luz, LEAF, given_on=date.today() + timedelta(days=2))[0]
    ok, _, ln_id = app.create_loan_note(other, date(2026, 1, 1), 1000.0, 2.0)
    ok, msg, _ = record_guarantee_check(luz, LEAF, loan_note_id=ln_id)
    assert not ok and "loan note" in msg.lower()                                  # that note belongs to another lender
    assert record_guarantee_check(luz, LEAF)[0]
    ok, msg, _ = record_guarantee_check(other, LEAF)
    assert not ok and LEAF in msg                                                  # a leaf can only be recorded once


def test_the_number_cannot_be_used_by_a_real_check_or_logged_spoiled(db):
    luz = _lender(db)
    record_guarantee_check(luz, LEAF)
    supplier = make_creditor(db, name="ACME")
    sid = app.create_order_statement(supplier)
    app.add_invoice(supplier, "I-1", date(2026, 9, 1), date(2026, 10, 1), 500.0, [], sid)
    ok, _, cid = app.issue_check(supplier, 500.0, date(2026, 10, 5), statement_id=sid)

    ok, msg = assign_check_number(cid, None, None, LEAF)
    assert not ok and "already" in msg.lower()
    ok, msg = app.log_spoiled_check(LEAF, "jam")
    assert not ok and "guarantee" in msg.lower()


def test_a_guarantee_leaf_is_an_accounted_for_leaf_in_its_booklet(db):
    assert app.create_booklet(1, "Mom - urgent", 3992554201, 3992554300, date(2026, 1, 1))[0]
    booklet_id = fetchone(db, "SELECT booklet_id FROM check_booklets")[0]
    from engines.checkbook_service import set_reported_up_to
    set_reported_up_to(booklet_id, 3992554215)
    assert 3992554212 in unrecorded_leaves(booklet_id)

    record_guarantee_check(_lender(db), LEAF)

    assert 3992554212 not in unrecorded_leaves(booklet_id)


def test_held_guarantees_are_exposure_not_payables(db):
    luz = _lender(db)
    record_guarantee_check(luz, LEAF, face_amount=2_000_000.0)
    record_guarantee_check(luz, "3992554213")                                      # no amount stated

    assert exposure() == {"held": 2, "total": 2_000_000.0, "no_amount": 1}
    assert get_payment_schedule_all_tracks().empty


def test_returning_a_guarantee_closes_it_and_can_be_undone(db):
    luz = _lender(db)
    _, _, gid = record_guarantee_check(luz, LEAF, face_amount=2_000_000.0)

    back = date.today() - timedelta(days=1)
    ok, msg = return_guarantee_check(gid, back, "loan settled, check taken back")
    assert ok, msg
    assert fetchone(db, "SELECT status, returned_on, return_note FROM guarantee_checks") == ("Returned", back.isoformat(), "loan settled, check taken back")
    assert exposure()["held"] == 0
    assert not return_guarantee_check(gid, date.today())[0]                       # already returned

    assert undo_return_guarantee(gid)[0]
    assert fetchone(db, "SELECT status, returned_on FROM guarantee_checks") == ("Held", None)
    assert exposure()["held"] == 1
    assert not undo_return_guarantee(gid)[0]
    assert not return_guarantee_check(gid, date.today() + timedelta(days=3))[0]   # not in the future
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action IN ('GUARANTEE_CHECK_RETURNED', 'GUARANTEE_CHECK_RETURN_UNDONE')")[0] == 2


def test_the_list_shows_holder_loan_note_amount_and_status(db):
    luz = _lender(db)
    ok, _, ln_id = app.create_loan_note(luz, date(2026, 1, 1), 1000000.0, 2.0)
    record_guarantee_check(luz, LEAF, face_amount=2_000_000.0, loan_note_id=ln_id, given_on=date(2026, 8, 15))

    df = guarantee_checks()

    row = df.iloc[0]
    assert (row["check_number"], row["holder"], row["face_amount"], row["status"]) == (LEAF, "Luz", 2_000_000.0, "Held")
    assert row["loan_note"] and row["given_on"] == "2026-08-15"


def test_when_the_bank_pays_a_held_guarantee_check_the_line_is_flagged_and_it_turns_presented(db, monkeypatch, tmp_path):
    monkeypatch.setenv("AP_DRIVE_DIR", str(tmp_path / "drive"))
    luz = _lender(db)
    _, _, gid = record_guarantee_check(luz, LEAF, face_amount=2_000_000.0)

    import_statement(_csv([_row("10/05/2026", LEAF, "IM IN- CLRNG- BATCH", "2,000,000.00", "0.00", "98,000.00")]), "s.csv")

    flagged = [e for e in exceptions() if e["match_status"] == "guarantee_check_presented"]
    assert len(flagged) == 1 and flagged[0]["check_number"] == LEAF
    assert fetchone(db, "SELECT status, presented_on FROM guarantee_checks WHERE guarantee_id = ?", (gid,)) == ("Presented", "2026-10-05")
    assert exposure()["held"] == 0
    assert not return_guarantee_check(gid, date.today())[0]                       # paid by the bank — it can't be "returned"


def test_data_health_flags_a_settled_loan_whose_guarantee_check_is_still_held(db):
    luz = _lender(db)
    ok, _, ln_id = app.create_loan_note(luz, date(2026, 1, 1), 1000000.0, 2.0)
    _, _, gid = record_guarantee_check(luz, LEAF, loan_note_id=ln_id)
    assert not [f for f in scan() if f["check"] == "Guarantee check still held after the loan ended"]

    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE loan_notes SET status = 'Settled' WHERE loan_note_id = ?", (ln_id,))

    findings = [f for f in scan() if f["check"] == "Guarantee check still held after the loan ended"]
    assert len(findings) == 1 and LEAF in findings[0]["detail"]
    return_guarantee_check(gid, date.today(), "returned")
    assert not [f for f in scan() if f["check"] == "Guarantee check still held after the loan ended"]
