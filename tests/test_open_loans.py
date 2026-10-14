"""Open loans (owner, 2026-10-14): no end date; interest every month on the principal still owed, from the month chosen; paid by check or ticked paid in
cash or by transfer, early too; paying the principal lowers the months not yet paid and the last peso closes the loan. Also: loans paid early on Daily
payables (bank loans; lender checks paid another way)."""
import sqlite3
from datetime import date, timedelta

from conftest import make_creditor
from tests.test_web_d0 import _admin


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _q(sql, params=()):
    with sqlite3.connect(_db()) as conn:
        return conn.execute(sql, params).fetchall()


def _month(d, n):
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def _open_loan(web, db, principal="2,000,000", day="13"):
    from engines.clock import manila_today
    cid = make_creditor(db, "Luz", creditor_type="LENDER")
    start = manila_today().replace(day=1)
    r = web.post("/lenders/", {"action": "open_new", "lender": cid, "given_on": "2026-08-15", "principal": principal, "rate": "2", "day": day,
                               "from": start.strftime("%Y-%m")})
    assert r.status_code == 302 and r["Location"].startswith("/lenders/") and r["Location"] != "/lenders/?open_new=1#open", r["Location"]
    return int(r["Location"].split("/")[2]), cid, start


def test_an_open_loan_writes_one_interest_month_at_a_time_from_its_first_month(web, db):
    from engines.open_loans import ensure_interest, principal_owed
    _admin(web)
    lnid, cid, start = _open_loan(web, db)
    rows = _q("SELECT substr(due_date, 1, 7), interest, kind FROM loan_installments WHERE loan_note_id = ? ORDER BY due_date", (lnid,))
    assert [r[0] for r in rows] == [_month(start, i).strftime("%Y-%m") for i in range(3)]          # this month and two ahead, none before
    assert all(r[1] == 40000.0 and r[2] == "INTEREST" for r in rows)
    assert ensure_interest() == 0                                                                   # run again: nothing twice
    assert principal_owed(lnid) == 2000000.0
    page = web.get(f"/lenders/{lnid}/").content.decode()
    assert "Principal still owed" in page and "2,000,000.00" in page and "40,000.00" in page and "Pay principal" in page
    from engines.reports_service import get_combined_ap_summary, get_payment_schedule_all_tracks
    s = get_payment_schedule_all_tracks()
    mine = s[s["reference"].astype(str).str.startswith("Interest · ")]
    assert len(mine) == 3 and float(mine["amount"].sum()) == 120000.0                               # on the Calendar
    lender = get_combined_ap_summary()
    assert float(lender.loc[lender["track"].str.startswith("Private Lender"), "outstanding"].iloc[0]) == 2000000.0


def test_interest_ticked_paid_leaves_the_calendar_and_principal_closes_the_loan(web, db):
    from engines.clock import manila_today
    from engines.reports_service import get_payment_schedule_all_tracks
    _admin(web)
    lnid, cid, start = _open_loan(web, db)
    with sqlite3.connect(_db()) as conn:
        conn.execute("""INSERT INTO guarantee_checks (check_number, creditor_id, loan_note_id, face_amount, given_on, status)
                        VALUES ('3992554212', ?, ?, 2000000, '2026-08-15', 'Held')""", (cid, lnid))
    first = _q("SELECT installment_id, due_date FROM loan_installments WHERE loan_note_id = ? ORDER BY due_date", (lnid,))
    future = first[-1][0]                                                                          # two months ahead: paid early
    r = web.post("/checks/", {"action": "clear_many", "on": manila_today().isoformat(), f"tick_oloan_{future}": "1", f"how_oloan_{future}": "TRANSFER"})
    assert r.status_code == 302
    assert _q("SELECT paid_how FROM loan_installments WHERE installment_id = ?", (future,))[0][0] == "TRANSFER"
    s = get_payment_schedule_all_tracks()
    assert len(s[s["reference"].astype(str).str.startswith("Interest · ")]) == 2
    web.post(f"/lenders/{lnid}/", {"action": "pay_principal", "amount": "500,000", "how": "CASH", "on": manila_today().isoformat()})
    unpaid_later = _q("""SELECT interest FROM loan_installments WHERE loan_note_id = ? AND kind = 'INTEREST' AND paid_at IS NULL AND due_date > ?""",
                      (lnid, manila_today().isoformat()))
    assert unpaid_later and all(r[0] == 30000.0 for r in unpaid_later)                             # 1.5M x 2%
    assert _q("SELECT interest FROM loan_installments WHERE installment_id = ?", (future,))[0][0] == 40000.0   # a paid month never changes
    web.post(f"/lenders/{lnid}/", {"action": "pay_principal", "amount": "1,500,000", "how": "TRANSFER", "on": manila_today().isoformat()})
    assert _q("SELECT status FROM loan_notes WHERE loan_note_id = ?", (lnid,))[0][0] == "Settled"
    page = web.get(f"/lenders/{lnid}/").content.decode()
    assert "fully paid and closed" in page and "3992554212" in page
    web.post(f"/lenders/{lnid}/", {"action": "return_all", "g_" + str(_q("SELECT guarantee_id FROM guarantee_checks")[0][0]): "1", "on": manila_today().isoformat()})
    assert _q("SELECT status FROM guarantee_checks")[0][0] == "Returned"
    s = get_payment_schedule_all_tracks()
    assert s[s["reference"].astype(str).str.startswith("Interest · ")].empty


def test_an_existing_note_made_open_keeps_its_rows_and_a_normal_note_is_untouched(web, db):
    from engines.clock import manila_today
    from engines.open_loans import ensure_interest
    from engines.loan_schedule_service import create_note_with_payments
    from engines.acting_user import acting_as
    _admin(web)
    cid = make_creditor(db, "Felix T Cruz", creditor_type="LENDER")
    with acting_as("admin@slicks.test", "admin"):
        ok, msg, plain = create_note_with_payments(cid, date(2026, 9, 1), 100000.0, 2.0, [(date(2026, 12, 1), 100000.0)])
        assert ok, msg
        ok, msg, other = create_note_with_payments(cid, date(2026, 9, 1), 1000000.0, 0.0, [(date(2026, 10, 5), 0.0)])
    before = _q("SELECT COUNT(*) FROM loan_installments WHERE loan_note_id = ?", (plain,))[0][0]
    r = web.post(f"/lenders/{other}/", {"action": "make_open", "principal": "1,000,000", "rate": "2", "day": "7", "from": _month(manila_today(), 1).strftime("%Y-%m")})
    assert r.status_code == 302
    rows = _q("SELECT kind, interest FROM loan_installments WHERE loan_note_id = ? ORDER BY due_date", (other,))
    assert rows[0][0] == "" and [r for r in rows if r[0] == "INTEREST"] and all(r[1] == 20000.0 for r in rows if r[0] == "INTEREST")
    ensure_interest()
    assert _q("SELECT COUNT(*) FROM loan_installments WHERE loan_note_id = ?", (plain,))[0][0] == before         # a normal loan note: unchanged


def test_loans_paid_early_on_daily_payables(web, db):
    from engines.clock import manila_today
    from engines.loan_schedule_service import create_note_with_payments
    from engines.acting_user import acting_as
    from engines.check_rules import issue_check
    _admin(web)
    today = manila_today()
    cid = make_creditor(db, "Ramon Mendoza", creditor_type="LENDER")
    with acting_as("admin@slicks.test", "admin"):
        ok, msg, lnid = create_note_with_payments(cid, today - timedelta(days=10), 50000.0, 0.0, [(today + timedelta(days=20), 50000.0)])
        assert ok, msg
    inst = _q("SELECT installment_id, due_date FROM loan_installments WHERE loan_note_id = ?", (lnid,))[0]
    with acting_as("admin@slicks.test", "admin"):
        ok, msg, chk = issue_check(cid, 50000.0, date.fromisoformat(inst[1]), loan_note_id=lnid, installment_id=inst[0])
        assert ok, msg
    with sqlite3.connect(_db()) as conn:
        conn.execute("UPDATE checks_issued SET check_number = '3992000777' WHERE check_id = ?", (chk,))
    page = web.get("/checks/?show=all").content.decode()
    assert f'name="tick_check_{chk}"' in page and f'name="how_check_{chk}"' in page
    r = web.post("/checks/", {"action": "clear_many", "on": today.isoformat(), f"tick_check_{chk}": "1", f"how_check_{chk}": "CASH"})
    assert r.status_code == 302
    settled = _q("SELECT settled_outside_at, settled_outside_reason, settled_outside_note FROM checks_issued WHERE check_id = ?", (chk,))[0]
    assert settled[0] == today.isoformat() and settled[1] == "Paid in cash" and "take the check back" in settled[2]
    # a bank loan installment, due later, ticked paid today
    bank = make_creditor(db, "BPI", creditor_type="BANK_LOAN")
    with sqlite3.connect(_db()) as conn:
        blid = conn.execute("INSERT INTO bank_loans (creditor_id, loan_ref, status) VALUES (?, 'RAP BPI', 'Active')", (bank,)).lastrowid
        biid = conn.execute("INSERT INTO bank_loan_installments (bank_loan_id, installment_no, due_date, amount) VALUES (?, 1, ?, 7746.73)",
                            (blid, (today + timedelta(days=15)).isoformat())).lastrowid
    from engines.clearing import mark_loan_cleared
    with acting_as("admin@slicks.test", "admin"):
        ok, msg = mark_loan_cleared(biid, today)
    assert ok, msg



def test_the_open_loans_tab_and_an_unpaid_month(web, db):
    from engines.clock import manila_today
    from engines.open_loans import listing, unpaid_months
    _admin(web)
    lnid, cid, start = _open_loan(web, db)
    page = web.get("/lenders/?tab=open").content.decode()
    assert "Open loans" in page and "2,000,000.00" in page and "40,000.00" in page and "Months unpaid" in page
    before = unpaid_months()                                 # this month's day may already have passed
    with sqlite3.connect(_db()) as conn:                     # a later month moved into the past, nothing paid
        conn.execute("UPDATE loan_installments SET due_date = ? WHERE installment_id = (SELECT MAX(installment_id) FROM loan_installments WHERE loan_note_id = ?)",
                     ((manila_today() - timedelta(days=3)).isoformat(), lnid))
    assert unpaid_months() == before + 1 and listing()[0]["late_amt"] == 40000.0 * (before + 1)
    from engines.home_tiles import _lenders
    assert "open-loan month" in _lenders()[1] and _lenders()[2] == "warn"
