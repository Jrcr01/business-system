"""Lessors and leases (plan D5): a lease is a loan note without interest whose schedule is the rent checks; converting Papruz / Luna keeps every check exactly as it was."""
import sqlite3
from datetime import date, timedelta

import app
from conftest import fetchone, make_creditor
from engines.check_rules import issue_check
from engines.creditor_rules import add_creditor, storage_type, update_creditor
from engines.lessor_service import add_rent_checks, convert_to_lessors, conversion_preview, create_lease, lease_checks, leases
from engines.loan_schedule_service import add_loan_installment
from views.shared import _type_filter


def _rent(n, start=None, amount=8000.0):
    start = start or date.today() + timedelta(days=10)
    return [(start + timedelta(days=30 * i), amount) for i in range(n)]


def test_a_lessor_is_stored_as_a_flagged_lender_and_can_be_edited_back(db):
    cid = add_creditor("rosalina papruz", "LESSOR", "", "", "", "")
    assert fetchone(db, "SELECT creditor_type, is_lessor FROM creditors WHERE creditor_id = ?", (cid,)) == ("LENDER", 1)
    assert storage_type("SUPPLIER") == ("SUPPLIER", 0)
    update_creditor(cid, "Rosalina Papruz", "LENDER", "", "", "", "", "")
    assert fetchone(db, "SELECT creditor_type, is_lessor FROM creditors WHERE creditor_id = ?", (cid,)) == ("LENDER", 0)


def test_pickers_keep_lessors_and_lenders_apart():
    assert "is_lessor = 0" in _type_filter("LENDER")[0] and "is_lessor = 1" in _type_filter("LESSOR")[0]
    assert _type_filter("SUPPLIER") == ("creditor_type = ?", ("SUPPLIER",))


def test_create_lease_makes_a_no_interest_loan_note_with_parked_rent_checks(db):
    lessor = add_creditor("Papruz", "LESSOR", "", "", "", "")

    ok, msg, lease_id = create_lease(lessor, "Store at Main St", _rent(3))

    assert ok, msg
    note = fetchone(db, "SELECT note_ref, principal_amount, total_interest, total_payable, status, is_lease, lease_description FROM loan_notes WHERE loan_note_id = ?", (lease_id,))
    assert note[0].endswith("LS 0001") and note[1:] == (24000.0, 0.0, 24000.0, "Active", 1, "Store at Main St")
    checks = lease_checks(lease_id)
    assert len(checks) == 3 and set(checks["how"]) == {"parked"} and checks["amount"].sum() == 24000.0
    assert fetchone(db, "SELECT COUNT(*), SUM(interest) FROM loan_installments WHERE loan_note_id = ?", (lease_id,)) == (3, 0.0)
    row = leases().iloc[0]
    assert row["lessor"] == "Papruz" and row["checks"] == 3 and row["total"] == 24000.0 and row["last_rent"] == 8000.0


def test_a_second_lease_gets_the_next_number_and_a_lender_cannot_have_a_lease(db):
    lessor = add_creditor("Papruz", "LESSOR", "", "", "", "")
    create_lease(lessor, "", _rent(1))
    ok, _, second = create_lease(lessor, "", _rent(1))
    assert ok and fetchone(db, "SELECT note_ref FROM loan_notes WHERE loan_note_id = ?", (second,))[0].endswith("LS 0002")
    lender = make_creditor(db, name="A Lender", creditor_type="LENDER")
    ok, msg, _ = create_lease(lender, "", _rent(1))
    assert not ok and "not a lessor" in msg
    assert not create_lease(lessor, "", [])[0]


def test_more_rent_checks_grow_the_lease(db):
    lessor = add_creditor("Luna", "LESSOR", "", "", "", "")
    _, _, lease_id = create_lease(lessor, "", _rent(2))

    ok, msg, ids = add_rent_checks(lease_id, _rent(2, start=date.today() + timedelta(days=100)))

    assert ok and len(ids) == 2, msg
    assert fetchone(db, "SELECT principal_amount, total_payable FROM loan_notes WHERE loan_note_id = ?", (lease_id,)) == (32000.0, 32000.0)
    assert len(lease_checks(lease_id)) == 4
    assert not add_rent_checks(999, _rent(1))[0]


def _placeholder_loan(db, name="Rosalina Papruz", rent=25000.0, n=2):
    lender = make_creditor(db, name=name, creditor_type="LENDER")
    ok, _, lnid = app.create_loan_note(lender, date.today() - timedelta(days=5), rent * n, 0.0)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE loan_notes SET note_ref = 'PENDING-109' WHERE loan_note_id = ?", (lnid,))
    for i in range(n):
        d = date.today() + timedelta(days=10 + 30 * i)
        assert add_loan_installment(lnid, d, rent, 0.0)[0]
        inst = fetchone(db, "SELECT installment_id FROM loan_installments WHERE loan_note_id = ? ORDER BY installment_id DESC", (lnid,))[0]
        assert issue_check(lender, rent, d, loan_note_id=lnid, installment_id=inst)[0]
    return lender, lnid


def _snapshot(db):
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT check_id, creditor_id, loan_note_id, check_number, check_date, amount, status FROM checks_issued ORDER BY check_id").fetchall(), \
               conn.execute("SELECT installment_id, loan_note_id, check_id, principal, interest, status FROM loan_installments ORDER BY installment_id").fetchall()


def test_converting_a_lender_to_a_lessor_changes_no_check(db):
    lender, lnid = _placeholder_loan(db)
    before = _snapshot(db)

    prev = conversion_preview([lender])
    assert len(prev) == 1 and prev.iloc[0]["active_checks"] == 2 and prev.iloc[0]["checks_total"] == 50000.0
    assert fetchone(db, "SELECT is_lessor FROM creditors WHERE creditor_id = ?", (lender,))[0] == 0               # the preview writes nothing

    ok, msg = convert_to_lessors([lender], "Rent")

    assert ok, msg
    assert _snapshot(db) == before                                                                                # every check and schedule row exactly as before
    assert fetchone(db, "SELECT is_lessor FROM creditors WHERE creditor_id = ?", (lender,))[0] == 1
    assert fetchone(db, "SELECT is_lease, lease_description FROM loan_notes WHERE loan_note_id = ?", (lnid,)) == (1, "Rent")
    assert leases().iloc[0]["lessor"] == "Rosalina Papruz"
    assert "2 checks kept" in msg or "2 check" in msg
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'LEASE_CONVERTED'")[0] == 1


def test_a_real_loan_with_interest_or_a_supplier_is_not_converted(db):
    lender = make_creditor(db, name="Richie", creditor_type="LENDER")
    ok, _, lnid = app.create_loan_note(lender, date.today() - timedelta(days=5), 100000.0, 3.0)
    assert add_loan_installment(lnid, date.today() + timedelta(days=30), 50000.0, 3000.0)[0]
    ok, msg = convert_to_lessors([lender])
    assert not ok and "interest" in msg
    assert fetchone(db, "SELECT is_lessor FROM creditors WHERE creditor_id = ?", (lender,))[0] == 0
    sup = make_creditor(db, name="Sup")
    assert not convert_to_lessors([sup])[0]
