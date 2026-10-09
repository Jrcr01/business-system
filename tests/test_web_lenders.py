"""Other payables -> Lenders in Django (Phase 7, owner 2026-10-08): a new loan note on the payment dates chosen, interest by days (balance x monthly rate x days / 30)
with the interest typed over kept; moving a payment works out its interest and the next one's again; parking a payment's check (one or all); the Checks tab is the
batch's flow, except a lender check is parked from the Schedule tab."""
import sqlite3

from conftest import fetchone, make_creditor
from tests.test_web_d0 import _admin


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _note(web, db, rate="3", **extra):
    cid = make_creditor(db, "Richie", creditor_type="LENDER")
    data = {"lender": cid, "issue_date": "2026-11-01", "principal": "100,000", "rate": rate,
            "d1": "2026-12-01", "p1": "50,000", "d2": "2026-12-16", "p2": "50000"}
    data.update(extra)
    r = web.post("/lenders/", data)
    return cid, r


def _inst(lnid):
    with sqlite3.connect(_db()) as conn:
        return conn.execute("SELECT installment_id, due_date, principal, interest, status, check_id FROM loan_installments WHERE loan_note_id = ? ORDER BY due_date",
                            (lnid,)).fetchall()


def _lnid():
    return fetchone(_db(), "SELECT MAX(loan_note_id) FROM loan_notes")[0]


def test_a_new_loan_note_counts_interest_by_days_and_keeps_typed_interest(web, db):
    _admin(web)
    assert "New loan note" in web.get("/lenders/").content.decode()
    _, r = _note(web, db, i2="999.99", io2="1")
    lnid = _lnid()
    assert r.status_code == 302 and r["Location"] == f"/lenders/{lnid}/"
    (_, d1, p1, i1, *_), (_, d2, p2, i2, *_) = _inst(lnid)
    assert (d1[:10], p1, i1) == ("2026-12-01", 50000.0, 3000.0)          # 100,000 x 3% x 30 / 30
    assert (d2[:10], p2, i2) == ("2026-12-16", 50000.0, 999.99)          # typed over: kept
    page = web.get(f"/lenders/{lnid}/").content.decode()
    assert "3,000.00" in page and "Park its check" in page and "Richie" in web.get("/lenders/").content.decode()


def test_a_payment_before_the_loan_date_is_refused_and_nothing_is_made(web, db):
    _admin(web)
    _, r = _note(web, db, d1="2026-10-15")
    assert r["Location"] == "/lenders/?new=1"
    assert fetchone(_db(), "SELECT COUNT(*) FROM loan_installments")[0] == 0


def test_moving_a_payment_works_out_its_interest_and_the_next_again(web, db):
    _admin(web)
    _note(web, db)
    lnid = _lnid()
    first = _inst(lnid)[0][0]
    web.post(f"/lenders/{lnid}/", {"action": "move", "installment_id": first, "new_date": "2026-11-16"})
    (_, d1, _, i1, *_), (_, _, _, i2, *_) = _inst(lnid)
    assert d1[:10] == "2026-11-16" and i1 == 1500.0                        # 15 days on 100,000
    assert i2 == 1500.0                                                     # 30 days on 50,000


def test_park_one_then_all_and_the_checks_tab_deletes_a_parked_one(web, db):
    _admin(web)
    _note(web, db)
    lnid = _lnid()
    first = _inst(lnid)[0][0]
    web.post(f"/lenders/{lnid}/", {"action": "park_one", "installment_id": first})
    rows = _inst(lnid)
    assert rows[0][4] == "Active" and rows[0][5] and rows[1][5] is None
    assert fetchone(_db(), "SELECT amount FROM checks_issued WHERE check_id = ?", (rows[0][5],))[0] == 53000.0
    r = web.post(f"/lenders/{lnid}/", {"action": "move", "installment_id": first, "new_date": "2026-11-20"})
    assert _inst(lnid)[0][1][:10] == "2026-12-01"                          # has its check: the date stays
    web.post(f"/lenders/{lnid}/", {"action": "park_all"})
    assert all(x[5] for x in _inst(lnid))
    page = web.get(f"/lenders/{lnid}/?tab=checks").content.decode()
    assert "Parked" in page and "Park checks" not in page
    cid = _inst(lnid)[1][5]
    r = web.post(f"/lenders/{lnid}/", {"action": "chk_delete", f"tick_{cid}": "1"})
    assert r["Location"] == f"/lenders/{lnid}/?tab=checks"
    assert _inst(lnid)[1][5] is None and _inst(lnid)[1][4] == "Pending"
    r = web.post(f"/lenders/{lnid}/", {"action": "chk_park", "d1": "2026-12-01", "a1": "100"})
    assert fetchone(_db(), "SELECT COUNT(*) FROM checks_issued WHERE loan_note_id = ?", (lnid,))[0] == 1


def test_the_loan_sheet_downloads(web, db):
    _admin(web)
    _note(web, db)
    r = web.get(f"/lenders/{_lnid()}/sheet.pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"


def test_guarantee_checks_on_the_lenders_page(web, db):
    # owner, 2026-10-09: "how about guarantee checks? where is it" -- a tab on Lenders; a loan note lists its own
    _admin(web)
    cid = make_creditor(db, "Luz", creditor_type="LENDER")
    web.post("/lenders/", {"lender": cid, "issue_date": "2026-11-01", "principal": "2,000,000", "rate": "2", "d1": "2026-12-01", "p1": "2,000,000"})
    lnid = _lnid()
    assert "Guarantee checks" in web.get("/lenders/").content.decode()
    web.post("/lenders/", {"action": "gc_record", "lender": cid, "number": "3992554212", "amount": "2,000,000", "given_on": "2026-10-01", "loan_note": lnid,
                           "note": "2M guarantee"})
    page = web.get("/lenders/?tab=guarantees").content.decode()
    assert "3992554212" in page and "Luz" in page and "1 held" in page and "10/01/2026" in page
    assert "3992554212" in web.get(f"/lenders/{lnid}/").content.decode()
    gid = fetchone(_db(), "SELECT guarantee_id FROM guarantee_checks WHERE check_number = '3992554212'")[0]
    web.post("/lenders/", {"action": "gc_return", "guarantee_id": gid, "returned_on": "2026-10-05"})
    assert fetchone(_db(), "SELECT status FROM guarantee_checks WHERE guarantee_id = ?", (gid,))[0] == "Returned"
    web.post("/lenders/", {"action": "gc_undo", "guarantee_id": gid})
    assert fetchone(_db(), "SELECT status FROM guarantee_checks WHERE guarantee_id = ?", (gid,))[0] == "Held"


def test_a_guarantee_check_can_be_edited_but_not_once_the_bank_paid_it(web, db):
    # owner, 2026-10-09: "why i cannot edit?"
    import sqlite3
    _admin(web)
    cid = make_creditor(db, "Felix T Cruz", creditor_type="LENDER")
    web.post("/lenders/", {"action": "gc_record", "lender": cid, "number": "3992542830", "given_on": "2026-04-09", "note": "Open Check"})
    gid = fetchone(_db(), "SELECT guarantee_id FROM guarantee_checks WHERE check_number = '3992542830'")[0]
    assert "Save" in web.get(f"/lenders/?tab=guarantees&edit={gid}").content.decode()
    web.post("/lenders/", {"action": "gc_edit", "guarantee_id": gid, "amount": "500,000", "given_on": "2026-04-10", "note": "open check, 500K"})
    assert fetchone(_db(), "SELECT face_amount, given_on, note FROM guarantee_checks WHERE guarantee_id = ?", (gid,)) == (500000.0, "2026-04-10", "open check, 500K")
    assert fetchone(_db(), "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'GUARANTEE_CHECK_EDITED'")[0] == 1
    with sqlite3.connect(_db()) as conn:
        conn.execute("UPDATE guarantee_checks SET status = 'Presented' WHERE guarantee_id = ?", (gid,))
    web.post("/lenders/", {"action": "gc_edit", "guarantee_id": gid, "amount": "1", "given_on": "2026-04-10", "note": ""})
    assert fetchone(_db(), "SELECT face_amount FROM guarantee_checks WHERE guarantee_id = ?", (gid,))[0] == 500000.0
