"""Editing an invoice of a payment statement that checks cover, in one step (owner, 2026-10-18: "too complicated when editing locked batch because i
have to delete first checks to modify invoices"; answer B: a numbered check that would pay too much is replaced under a new number)."""
import sqlite3

from conftest import fetchone, make_creditor
from tests.test_web_batch_checks import _checks, _db, _park
from tests.test_web_d0 import _admin
from tests.test_web_d3a import _add_inv, _inv, _new_batch, _stamp_of


def _two(web, db):
    cid = make_creditor(db, "CSS Autoparts Inc.", terms="30")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, doc="110744", gross="6,000")
    _add_inv(web, sid, doc="112107", gross="4,000")
    return sid


def _locked(sid):
    return fetchone(_db(), "SELECT is_locked FROM order_statements WHERE statement_id = ?", (sid,))[0]


def test_a_parked_check_follows_an_invoice_edit_on_a_closed_statement(web, db):
    _admin(web)
    sid = _two(web, db)
    _park(web, sid, ("2026-11-01", "10,000"))
    assert _locked(sid) == 1
    inv = _inv("112107")[0]
    r = web.post(f"/order-statements/{sid}/", {"action": "save_invoice", "invoice_id": inv, "stamp": _stamp_of(web, sid, inv), "doc": "112107", "dr": "",
                                                "date": "2026-09-08", "gross": "3,500", "less": "", "reason": "wrong amount on the SI"}, follow=True)
    page = r.content.decode()
    assert "parked check lowered" in page
    assert [c[2] for c in _checks(sid) if c[6] == "Active"] == [9500.0] and _locked(sid) == 1      # lowered by itself, closed again
    assert fetchone(_db(), "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CHECK_FITTED_TO_INVOICES'")[0] == 1


def test_a_numbered_check_is_replaced_under_the_number_typed(web, db):
    _admin(web)
    sid = _two(web, db)
    _park(web, sid, ("2026-11-01", "6,000"), ("2026-12-01", "4,000"))
    a, b = [c[0] for c in _checks(sid)]
    web.post(f"/order-statements/{sid}/", {"action": "chk_hand", f"num_{a}": "7001", f"num_{b}": "7002"})
    inv = _inv("112107")[0]
    r = web.post(f"/order-statements/{sid}/", {"action": "save_invoice", "invoice_id": inv, "stamp": _stamp_of(web, sid, inv), "doc": "112107", "dr": "",
                                                "date": "2026-09-08", "gross": "3,000", "less": "", "reason": "price corrected"})
    assert r.status_code == 302 and "fix=1" in r["Location"]                                       # it asks for the new number first
    page = web.get(f"/order-statements/{sid}/?fix=1").content.decode()
    assert "type the new check" in page and "7002" in page and 'name="num_' in page
    assert [c[2] for c in _checks(sid)] == [6000.0, 4000.0]                                           # nothing changed yet
    r = web.post(f"/order-statements/{sid}/", {"action": "confirm_fix", f"num_{b}": "7001"}, follow=True)
    assert "already used" in r.content.decode()                                                     # a used number stops everything
    r = web.post(f"/order-statements/{sid}/", {"action": "confirm_fix", f"num_{b}": "7010"}, follow=True)
    assert "replaced by check 7010" in r.content.decode()
    rows = {c[1]: (c[2], c[6]) for c in _checks(sid)}
    assert rows["7002"] == (4000.0, "Replaced") and rows["7010"] == (3000.0, "Active") and rows["7001"] == (6000.0, "Active")
    assert _inv("112107")[2] == 3000.0 and _locked(sid) == 1
    with sqlite3.connect(_db()) as conn:                                                             # which check pays which invoice, again
        assert conn.execute("SELECT ROUND(SUM(amount), 2) FROM invoice_check_allocations").fetchone()[0] in (None, 9000.0)   # rebuilt when tracked


def test_take_out_voids_a_numbered_check_left_with_nothing_and_a_cleared_check_never_changes(web, db):
    _admin(web)
    sid = _two(web, db)
    _park(web, sid, ("2026-11-01", "6,000"), ("2026-12-01", "4,000"))
    a, b = [c[0] for c in _checks(sid)]
    web.post(f"/order-statements/{sid}/", {"action": "chk_hand", f"num_{a}": "7001", f"num_{b}": "7002"})
    inv = _inv("112107")[0]
    r = web.post(f"/order-statements/{sid}/", {"action": "take_out", "invoice_id": inv, "stamp": _stamp_of(web, sid, inv), "reason": "belongs to the next payment"}, follow=True)
    assert "check 7002 voided" in r.content.decode()
    assert {c[1]: c[6] for c in _checks(sid)}["7002"] == "Cancelled"
    assert fetchone(_db(), "SELECT statement_id FROM invoices WHERE invoice_id = ?", (inv,))[0] is None
    with sqlite3.connect(_db()) as conn:                                                             # the other check cleared at the bank
        conn.execute("UPDATE checks_issued SET bank_cleared_date = '2026-11-02' WHERE check_number = '7001'")
    inv2 = _inv("110744")[0]
    r = web.post(f"/order-statements/{sid}/", {"action": "save_invoice", "invoice_id": inv2, "stamp": _stamp_of(web, sid, inv2), "doc": "110744", "dr": "",
                                                "date": "2026-09-08", "gross": "5,000", "less": "", "reason": "x"}, follow=True)
    assert "credit memo" in r.content.decode() and _inv("110744")[2] == 6000.0                        # refused: nothing changed


def test_which_check_pays_which_invoice_is_rebuilt_when_the_statement_tracks_it(db):
    from engines.statement_fix import _rebuild_allocations
    cid = make_creditor(db, "X Supplier", terms="30")
    with sqlite3.connect(db) as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO order_statements (creditor_id, statement_ref, total_amount, status) VALUES (?, 'DRAFT-X', 0, 'Draft')", (cid,))
        sid = cur.lastrowid
        ids = []
        for doc, amt, due in (("A", 6000, "2026-11-01"), ("B", 3000, "2026-12-01")):
            cur.execute("INSERT INTO invoices (creditor_id, statement_id, doc_ref, invoice_date, due_date, gross_amount, amount, status) VALUES (?, ?, ?, '2026-10-01', ?, ?, ?, 'Unpaid')",
                        (cid, sid, doc, due, amt, amt))
            ids.append(cur.lastrowid)
        for d, amt in (("2026-11-01", 5000), ("2026-12-01", 4000)):
            cur.execute("INSERT INTO checks_issued (creditor_id, bank_account_id, statement_id, check_date, amount, status) VALUES (?, 1, ?, ?, ?, 'Active')", (cid, sid, d, amt))
        _rebuild_allocations(cur, sid, True)
        got = cur.execute("SELECT invoice_id, ROUND(SUM(amount), 2) FROM invoice_check_allocations GROUP BY invoice_id ORDER BY invoice_id").fetchall()
    assert got == [(ids[0], 6000.0), (ids[1], 3000.0)]


def test_a_statement_whose_checks_all_cleared_reads_paid_and_a_numbered_check_counts_as_printed(web, db):
    """Owner, 2026-10-18: "if all checks are cleared make it paid than issued"; "why not balance?" -- a numbered check is printed even when its
    Chrysanth run was never marked printed, so the bulk summary and the printout count it."""
    from engines.batch_statement_service import bulk_summary_rows
    _admin(web)
    sid = _two(web, db)
    _park(web, sid, ("2026-11-01", "6,000"), ("2026-12-01", "4,000"))
    a, b = [c[0] for c in _checks(sid)]
    web.post(f"/order-statements/{sid}/", {"action": "chk_hand", f"num_{a}": "7001", f"num_{b}": "7002"})
    row = bulk_summary_rows([sid], include_unprinted_checks=False)[0]
    assert row["checks"] == 2 and row["checks_total"] == 10000.0                                       # numbered = printed
    assert row["stage"] != "Paid"
    with sqlite3.connect(_db()) as conn:
        conn.execute("UPDATE checks_issued SET bank_cleared_date = '2026-12-02' WHERE statement_id = ?", (sid,))
    assert bulk_summary_rows([sid])[0]["stage"] == "Paid"
    assert ">Paid</span>" in web.get("/order-statements/").content.decode()
    assert ">Paid</span>" in web.get(f"/order-statements/{sid}/").content.decode()
