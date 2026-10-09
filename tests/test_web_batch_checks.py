"""A batch's Checks tab in Django (Phase 2 step 2, owner 2026-10-08): park -> write (Chrysanth or by hand) -> number -> release -> cleared; parked checks
edited or deleted; the skipped-number pop-up (spoiled / check later); void; and each step follows the person's switches. Release is for everyone with the
Release switch."""
from datetime import date

import app
from conftest import fetchone, make_creditor
from tests.test_web_d0 import _admin, _sign_in_new, fetchone_uid
from tests.test_web_d3a import _add_inv, _new_batch


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _batch(web, db, gross="10,000"):
    cid = make_creditor(db, "Goodyear Philippines", terms="30")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, gross=gross)
    return sid


def _park(web, sid, *rows):
    data = {"action": "chk_park"}
    for i, (d, a) in enumerate(rows, start=1):
        data[f"d{i}"], data[f"a{i}"] = d, a
    return web.post(f"/order-statements/{sid}/", data)


def _checks(sid):
    import sqlite3
    with sqlite3.connect(_db()) as conn:
        return conn.execute("SELECT check_id, check_number, amount, printed_at IS NOT NULL, print_batch_id, released_date, status FROM checks_issued "
                            "WHERE statement_id = ? ORDER BY check_id", (sid,)).fetchall()


def test_park_edit_and_delete_parked_checks(web, db):
    _admin(web)
    sid = _batch(web, db)
    page = web.get(f"/order-statements/{sid}/?tab=checks").content.decode()
    assert "Park checks" in page and "10,000.00" in page
    _park(web, sid, ("2026-11-01", "6,000"), ("2026-12-01", "4000"))
    (a, _, amt_a, *_), (b, *_) = _checks(sid)
    assert amt_a == 6000.0
    web.post(f"/order-statements/{sid}/", {"action": "chk_edit", "check_id": a, "date": "2026-11-05", "amount": "5500"})
    assert fetchone(_db(), "SELECT amount, check_date FROM checks_issued WHERE check_id = ?", (a,)) == (5500.0, "2026-11-05")
    web.post(f"/order-statements/{sid}/", {"action": "chk_delete", f"tick_{b}": "1"})
    assert [c[0] for c in _checks(sid)] == [a]
    assert fetchone(_db(), "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CHECK_UNPARKED'")[0] == 1


def test_write_with_chrysanth_gives_the_run_file_and_the_checks_are_written(web, db):
    _admin(web)
    sid = _batch(web, db)
    _park(web, sid, ("2026-11-01", "10000"))
    cid = _checks(sid)[0][0]
    r = web.post(f"/order-statements/{sid}/", {"action": "chk_chrysanth", f"tick_{cid}": "1"})
    assert r.status_code == 200 and r["Content-Disposition"].startswith('attachment; filename="ChequeTransaction_RUN')
    _, number, _, written, run, *_ = _checks(sid)[0]
    assert written and run and number is None
    page = web.get(f"/order-statements/{sid}/?tab=checks").content.decode()
    assert "Written · Chrysanth" in page and "Give numbers" in page
    r = web.get(f"/order-statements/{sid}/?tab=checks&number=1&tick_{cid}=1").content.decode()
    assert f'name="num_{cid}"' in r
    web.post(f"/order-statements/{sid}/", {"action": "chk_number", f"num_{cid}": "0004501"})
    assert _checks(sid)[0][1] == "0004501"
    r = web.post(f"/order-statements/{sid}/", {"action": "chk_delete", f"tick_{cid}": "1"}, follow=True)
    assert b"already has the number" in r.content                                  # a numbered check is never deleted: void it instead


def test_write_by_hand_numbers_and_the_skipped_leaves_pop_up(web, db):
    _admin(web)
    assert app.create_booklet(1, "B-1000", 1000, 1010, date(2026, 1, 1))[0]
    sid = _batch(web, db)
    _park(web, sid, ("2026-11-01", "5000"), ("2026-12-01", "5000"))
    a, b = [c[0] for c in _checks(sid)]
    r = web.post(f"/order-statements/{sid}/", {"action": "chk_hand", f"num_{a}": "1000", f"num_{b}": "1003"}, follow=True)
    page = r.content.decode()
    assert "written by hand and numbered" in page and "Numbers skipped in B-1000" in page and "1001, 1002" in page
    assert all(c[3] for c in _checks(sid))                                          # written
    web.post(f"/order-statements/{sid}/", {"action": "chk_skip_spoiled", "leaves": "1001,1002"})
    assert fetchone(_db(), "SELECT COUNT(*) FROM spoiled_checks")[0] == 2
    web.post(f"/order-statements/{sid}/", {"action": "chk_skip_later", "booklet": "1", "code": "B-1000", "leaves": "1004"})
    assert fetchone(_db(), "SELECT kind FROM review_flags")[0] == "CHECK_NUMBER_SKIPPED"


def test_release_is_for_everyone_with_the_switch_and_void_needs_a_reason(web, db):
    from engines.auth_service import set_restrictions
    _admin(web)
    sid = _batch(web, db)
    _park(web, sid, ("2026-11-01", "4000"), ("2026-12-01", "6000"))
    a, b = [c[0] for c in _checks(sid)]
    web.post(f"/order-statements/{sid}/", {"action": "chk_hand", f"num_{a}": "7001", f"num_{b}": "7002"})
    family = _sign_in_new("family@slicks.test", "owner")
    family.post(f"/order-statements/{sid}/", {"action": "chk_release", f"tick_{a}": "1", "released_to": "Rider Jun", "released_date": str(date.today())})
    assert _checks(sid)[0][5] == str(date.today())
    assert "Released" in family.get(f"/order-statements/{sid}/?tab=checks").content.decode()
    set_restrictions(fetchone_uid("family@slicks.test"), {"check.release"})
    family.post(f"/order-statements/{sid}/", {"action": "chk_release", f"tick_{b}": "1", "released_date": str(date.today())})
    assert _checks(sid)[1][5] is None                                               # switched off: refused
    web.post(f"/order-statements/{sid}/", {"action": "chk_void", "check_id": b, "reason": ""})
    assert _checks(sid)[1][6] == "Active"
    web.post(f"/order-statements/{sid}/", {"action": "chk_void", "check_id": b, "reason": "wrong amount written"})
    assert _checks(sid)[1][6] == "Cancelled"
    web.post(f"/order-statements/{sid}/", {"action": "chk_unrelease", "check_id": a})
    assert _checks(sid)[0][5] is None


def test_void_and_replace_right_away_with_or_without_a_number(web, db):
    _admin(web)
    sid = _batch(web, db)
    _park(web, sid, ("2026-11-01", "4000"), ("2026-12-01", "6000"))
    a, b = [c[0] for c in _checks(sid)]
    web.post(f"/order-statements/{sid}/", {"action": "chk_hand", f"num_{a}": "8001", f"num_{b}": "8002"})
    page = web.get(f"/order-statements/{sid}/?tab=checks&void={a}").content.decode()
    assert "Void and replace" in page and "Void only" in page
    web.post(f"/order-statements/{sid}/", {"action": "chk_replace", "check_id": a, "reason": "damaged", "new_date": "2026-11-03", "new_number": "8003"})
    rows = _checks(sid)
    assert rows[0][6] == "Replaced" and rows[-1][1] == "8003" and rows[-1][2] == 4000.0 and rows[-1][3]          # written at once, same amount
    web.post(f"/order-statements/{sid}/", {"action": "chk_release", f"tick_{rows[-1][0]}": "1", "released_date": str(date.today())})
    assert _checks(sid)[-1][5] == str(date.today())                                                             # and released like any written check
    web.post(f"/order-statements/{sid}/", {"action": "chk_replace", "check_id": b, "reason": "wrong date", "new_date": "2026-12-05", "new_number": ""})
    last = _checks(sid)[-1]
    assert _checks(sid)[1][6] == "Replaced" and last[1] is None and not last[3] and last[2] == 6000.0             # parked: to be written


def test_the_skipped_number_pop_up_works_with_full_ten_digit_booklets(web, db):
    _admin(web)
    assert app.create_booklet(1, "Booklet 2", 3992575601, 3992575700, date(2026, 1, 1))[0]
    sid = _batch(web, db)
    _park(web, sid, ("2026-11-01", "10000"))
    a = _checks(sid)[0][0]
    page = web.post(f"/order-statements/{sid}/", {"action": "chk_hand", f"num_{a}": "3992575603"}, follow=True).content.decode()
    assert "Numbers skipped in Booklet 2" in page and "3992575601, 3992575602" in page


def test_the_split_choices_and_more_than_six_checks_at_once(web, db):
    # owner, 2026-10-09: "where is my divide functions like in streamlit before?" -- by number of checks / one check per invoice; rows up to 50
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines", terms="30")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, doc="INV-1", gross="8,000")
    _add_inv(web, sid, doc="INV-2", gross="2,000")
    page = web.get(f"/order-statements/{sid}/?tab=checks").content.decode()
    assert "How to split" in page and "One check per invoice (2)" in page and 'name="d50"' in page and '"remaining": 8000.0' in page
    _park(web, sid, *[(f"2026-11-{d:02d}", "1,250") for d in range(1, 9)])          # 8 checks
    rows = _checks(sid)
    assert len(rows) == 8 and sum(r[2] for r in rows) == 10000.0


def test_home_has_daily_payables_and_no_payment_run(web, db):
    _admin(web)
    home = web.get("/").content.decode()
    assert "Daily payables" in home and "Payment run" not in home and "/payment-run/" not in home


def test_numbers_come_from_the_booklet_as_in_the_old_screens(web, db):
    # owner, 2026-10-09: "make the assigning of checks easier like in the /more" -- pick the booklet, the first free leaf is filled in, used or spoiled
    # leaves are stepped over, one click numbers them all
    import sqlite3
    _admin(web)
    sid = _batch(web, db, gross="30,000")
    _park(web, sid, ("2026-11-01", "10,000"), ("2026-11-15", "10,000"), ("2026-12-01", "10,000"))
    with sqlite3.connect(_db()) as conn:
        bk = conn.execute("INSERT INTO check_booklets (bank_account_id, booklet_code, start_number, end_number, next_available_number, date_received) "
                          "VALUES (1, 'BK-77', 5001, 5050, 5001, '2026-10-01')").lastrowid
        conn.execute("INSERT INTO spoiled_checks (bank_account_id, check_number, reason) VALUES (1, '5002', 'torn')")
    ids = [r[0] for r in _checks(sid)]
    q = "&".join(f"tick_{i}=1" for i in ids)
    page = web.get(f"/order-statements/{sid}/?tab=checks&number=1&{q}").content.decode()
    assert "From booklet" in page and "BK-77" in page and "next free 5001" in page and "Stepping over 1" in page
    web.post(f"/order-statements/{sid}/", {"action": "chk_booklet", "bk": bk, "start": "5001", "ids": ",".join(map(str, ids))})
    assert [r[1] for r in _checks(sid)] == ["5001", "5003", "5004"]
