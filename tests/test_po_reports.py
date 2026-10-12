"""Reports -> PO follow-through and With and without a PO (owner, 2026-10-12: "the reports first" -> "go")."""
import sqlite3

from tests.test_web_d0 import _admin
from tests.test_web_d2b import _order_it, _start, _supplier_with_price


def _run(db, sql, params=()):
    with sqlite3.connect(db) as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def _ordered(web, db):
    _admin(web)
    cid = _supplier_with_price(web, db)
    po_id = _start(web, cid)                                                    # 10 at 1,000 = 10,000 ordered
    _order_it(web, po_id)
    return cid, po_id


def _row(po_id):
    from engines.po_reports import follow_through
    return next(r for r in follow_through("all")["rows"] if r["po_id"] == po_id)


def test_an_order_is_followed_from_ordered_to_paid(web, db):
    cid, po_id = _ordered(web, db)
    r = _row(po_id)
    assert (r["ordered"], r["billed"], r["to_come"]) == (10000.0, 0.0, 10000.0) and r["stage"].startswith("not received")
    inv = _run(db, "INSERT INTO invoices (creditor_id, doc_ref, invoice_date, due_date, gross_amount, amount) VALUES (?, 'SI 1', '2026-10-08', '2026-11-07', 10200, 10200)", (cid,))
    _run(db, "INSERT INTO po_receipts (po_id, received_date, actual_amount, est_amount, invoice_id) VALUES (?, '2026-10-08', 10200, 10000, ?)", (po_id, inv))
    _run(db, "UPDATE purchase_orders SET status = 'RECEIVED' WHERE po_id = ?", (po_id,))
    r = _row(po_id)
    assert (r["billed"], r["diff"], r["written"], r["owed"], r["to_come"], r["stage"]) == (10200.0, 200.0, 0.0, 10200.0, 0.0, "received, no check")
    sid = _run(db, "INSERT INTO order_statements (creditor_id, statement_ref, total_amount) VALUES (?, 'B-1', 10200)", (cid,))
    chk = _run(db, "INSERT INTO checks_issued (creditor_id, bank_account_id, statement_id, check_number, check_date, amount, status) VALUES (?, 1, ?, '900001', '2026-11-07', 10200, 'Active')", (cid, sid))
    _run(db, "INSERT INTO invoice_check_allocations (invoice_id, check_id, amount) VALUES (?, ?, 10200)", (inv, chk))
    r = _row(po_id)
    assert (r["written"], r["paid"], r["stage"]) == (10200.0, 0.0, "checks written")
    from engines.po_reports import follow_through
    data = follow_through("open")
    assert data["waiting_bank"] == 10200.0 and data["no_check"] == 0.0 and data["counts"]["differs"] == 1
    _run(db, "UPDATE checks_issued SET bank_cleared_date = '2026-11-09' WHERE check_id = ?", (chk,))
    r = _row(po_id)
    assert (r["paid"], r["owed"], r["stage"], r["done"]) == (10200.0, 0.0, "paid", True)
    assert po_id not in [x["po_id"] for x in follow_through("open")["rows"]]     # a paid order is no longer open
    page = web.get("/reports/?r=po&show=all").content.decode()
    assert "PO follow-through" in page and "10,200.00" in page and ">paid<" in page
    assert web.get("/reports/?r=po&download=xlsx")["Content-Type"].startswith("application/vnd.openxmlformats")


def test_purchases_with_and_without_a_po(web, db):
    cid, po_id = _ordered(web, db)
    a = _run(db, "INSERT INTO invoices (creditor_id, doc_ref, invoice_date, due_date, gross_amount, amount) VALUES (?, 'SI 1', '2026-10-08', '2026-11-07', 7500, 7500)", (cid,))
    _run(db, "INSERT INTO po_receipts (po_id, received_date, actual_amount, est_amount, invoice_id) VALUES (?, '2026-10-08', 7500, 7500, ?)", (po_id, a))
    _run(db, "INSERT INTO invoices (creditor_id, doc_ref, invoice_date, due_date, gross_amount, amount) VALUES (?, 'SI 2', '2026-10-09', '2026-11-08', 2500, 2500)", (cid,))
    _run(db, "INSERT INTO invoices (creditor_id, doc_ref, invoice_date, due_date, gross_amount, amount, is_opening_balance) VALUES (?, 'BEG', '2026-10-01', '2026-10-01', 99999, 99999, 1)", (cid,))
    _run(db, "INSERT INTO invoices (creditor_id, doc_ref, invoice_date, due_date, gross_amount, amount, status) VALUES (?, 'SI 3', '2026-10-09', '2026-11-08', 400, 400, 'Cancelled')", (cid,))
    from engines.po_reports import with_and_without
    data = with_and_without("month", "2026-10")
    g = next(x for x in data["groups"] if x["key"] == "2026-10")
    assert (g["n"], g["amount"], g["n_po"], g["amt_po"], g["n_no"], g["amt_no"], g["pct"]) == (2, 10000.0, 1, 7500.0, 1, 2500.0, 75)
    assert [r["doc_ref"] for r in data["no_po"]] == ["SI 2"]
    assert with_and_without("supplier", "2026-10")["groups"][0]["amt_no"] == 2500.0
    page = web.get("/reports/?r=nopo&m=2026-10").content.decode()
    assert "With and without a PO" in page and "75%" in page and "SI 2" in page and "BEG" not in page
    assert web.get("/reports/?r=nopo&download=xlsx")["Content-Type"].startswith("application/vnd.openxmlformats")
    assert "PO follow-through" in web.get("/reports/").content.decode()         # the cards are on the Reports page
