"""Reports after the review (owner, 2026-10-09): Schedule of payments, one Statement of account (supplier, lender, bank loan or card; a payment counts on the
day it cleared; checks not cleared listed apart; Excel and PDF) (Decision reports dropped); supplier aging on Analytics -> Payables."""
import sqlite3
from datetime import timedelta

from conftest import make_creditor
from engines.clock import manila_today
from tests.test_web_d0 import _admin
from tests.test_web_d3a import _add_inv, _new_batch

TODAY = manila_today()


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def test_a_supplier_statement_of_account_with_its_running_balance(web, db):
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines", terms="30")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, doc="SI-1", gross="10,000", date=str(TODAY - timedelta(days=20)))
    _add_inv(web, sid, doc="", dr="342", gross="5,000", date=str(TODAY - timedelta(days=10)))
    web.post(f"/order-statements/{sid}/", {"action": "chk_park", "d1": str(TODAY - timedelta(days=5)), "a1": "6,000", "d2": str(TODAY - timedelta(days=2)), "a2": "9,000"})
    with sqlite3.connect(_db()) as conn:
        a, b = [r[0] for r in conn.execute("SELECT check_id FROM checks_issued WHERE statement_id = ? ORDER BY check_date", (sid,))]
        conn.execute("UPDATE checks_issued SET check_number = '0001', manual_cleared_at = ? WHERE check_id = ?", (str(TODAY - timedelta(days=4)), a))
        conn.execute("UPDATE checks_issued SET check_number = '0002' WHERE check_id = ?", (b,))
    from engines.account_statement import ledger
    st = ledger("supplier", cid)
    assert (st["charges"], st["payments"], st["closing"], st["waiting_total"]) == (15000.0, 6000.0, 9000.0, 9000.0)
    assert [x["doc"] for x in st["lines"]] == ["SI SI-1", "DR 342", "Check 0001 cleared"]
    page = web.get(f"/reports/?r=soa&kind=supplier&id={cid}").content.decode()
    assert "Statement of account" in page and "9,000.00" in page and "waiting for the bank" in page
    assert web.get(f"/reports/?r=soa&kind=supplier&id={cid}&download=pdf").content[:4] == b"%PDF"
    assert web.get(f"/reports/?r=soa&kind=supplier&id={cid}&download=xlsx").status_code == 200
    narrowed = ledger("supplier", cid, TODAY - timedelta(days=12), TODAY)
    assert narrowed["opening"] == 10000.0 and narrowed["closing"] == 9000.0          # brought forward + the period


def test_the_reports_kept_and_supplier_aging_on_analytics(web, db):
    _admin(web)
    make_creditor(db, "Goodyear Philippines")
    page = web.get("/reports/").content.decode()
    assert "Schedule of payments" in page and "Statement of account" in page and "Decision reports" not in page and "old screens" not in page
    for kind in ("lender", "bankloan", "card"):
        assert web.get(f"/reports/?r=soa&kind={kind}").status_code == 200
    sid = _new_batch(web, make_creditor(db, "Bridgestone", terms="30"))
    _add_inv(web, sid, doc="SI-9", gross="1,000", date=str(TODAY - timedelta(days=90)))
    assert "Supplier aging" in web.get("/analytics/?tab=payables").content.decode()
