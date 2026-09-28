"""Data health check (2026-09-28, owner-approved): every kind of suspicious record is found, correct set-ups are left alone, and "It's fine"
hides a finding (audited) until "Show again". Made-up records only."""
import sqlite3
from datetime import date

from conftest import make_creditor, make_statement
from engines.audit_seal import seal_and_verify
from engines.data_health_service import findings, mark_fine, open_count, show_again

TODAY = date(2026, 9, 28)


def _check(conn, creditor_id, number, check_date, amount, statement_id=None, printed=True, status="Active"):
    return conn.execute("""INSERT INTO checks_issued (creditor_id, bank_account_id, statement_id, check_number, check_date, amount, status, printed_at)
                           VALUES (?, 1, ?, ?, ?, ?, ?, ?)""",
                        (creditor_id, statement_id, number, check_date, amount, status, "2026-09-26 10:00:00" if printed else None)).lastrowid


def _invoice(conn, creditor_id, ref, inv, due, amount, statement_id):
    return conn.execute("INSERT INTO invoices (creditor_id, doc_ref, invoice_date, due_date, gross_amount, amount, statement_id) VALUES (?,?,?,?,?,?,?)",
                        (creditor_id, ref, inv, due, amount, amount, statement_id)).lastrowid


def _batch(db, creditor_id, ref):
    with sqlite3.connect(db) as conn:
        return conn.execute("INSERT INTO order_statements (creditor_id, statement_ref, total_amount, status) VALUES (?, ?, 0, 'Draft')",
                            (creditor_id, ref)).lastrowid


def _clean(db):
    """A supplier, a batch with one invoice and one proper check; a bank set up for loans AND cards; a lender with no terms."""
    s = make_creditor(db, name="S&P Tires")
    sid = make_statement(db, s)
    make_creditor(db, name="Metrobank", creditor_type="BANK_LOAN", terms="")
    make_creditor(db, name="Metrobank", creditor_type="CREDIT_CARD", terms="")
    make_creditor(db, name="Tita Lending", creditor_type="LENDER", terms="")
    with sqlite3.connect(db) as conn:
        _invoice(conn, s, "SI-1", "2026-09-01", "2026-10-01", 20000.0, sid)
        _check(conn, s, "3992554301", "2026-10-01", 10000.0, sid)
    seal_and_verify(db)                  # core step C2: a clean system's audit log has been checked by the daily backup
    return s, sid


def _found(db):
    return {(f["group"], f["check"]) for f in findings(today=TODAY)}


def test_clean_records_have_no_findings(db):
    _clean(db)
    assert findings(today=TODAY) == [] and open_count() == 0


def test_every_kind_of_problem_is_found(db):
    s, sid = _clean(db)
    other = make_creditor(db, name="Tire Asia")
    make_creditor(db, name="S&P Tires ")                                     # the same name again (extra space)
    make_creditor(db, name="No Terms Trading", terms="")
    gone = make_creditor(db, name="Closed Shop")
    empty = _batch(db, s, 'EMPTY-1')
    big = _batch(db, other, 'TA-BATCH')
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE creditors SET is_active = 0 WHERE creditor_id = ?", (gone,))
        _invoice(conn, gone, "CS-1", "2026-08-01", "2026-09-01", 500.0, None)                  # inactive + owed; not in any batch
        _invoice(conn, s, "SI-2", "2026-09-10", "2026-09-01", 100.0, sid)                     # due before invoice date
        _invoice(conn, s, "SI-3", "2062-09-10", "2062-10-10", 100.0, sid)                     # year typo
        _check(conn, s, None, "2027-01-22", 70.0, sid)                                        # printed, no number
        _check(conn, s, "3992554310", "2025-11-01", 80.0, sid)                                # stale
        _check(conn, s, "3992554311", "2028-01-01", 90.0, sid)                                # far ahead
        _check(conn, other, "3992554312", "2026-10-03", 60.0, sid)                            # payee differs from the batch supplier
        _invoice(conn, other, "TA-1", "2026-09-01", "2026-10-01", 1000.0, big)
        _check(conn, other, "3992554313", "2026-10-01", 1500.0, big)                          # overpaid batch
        imp = conn.execute("INSERT INTO bank_statement_imports (bank_account_id, file_name, file_hash) VALUES (1, 's.csv', 'h')").lastrowid
        chk = _check(conn, s, "3992527921", "2026-10-29", 6409.0, sid)
        conn.execute("""INSERT INTO bank_transactions (import_id, txn_date, posting_date, check_number, debit, kind, line_key, match_status, matched_check_id)
                        VALUES (?, '2026-01-29', '2026-01-29', '3992527921', 6408, 'CHECK', 'k1', 'amount_mismatch', ?)""", (imp, chk))
    got = _found(db)
    # (a check number used twice can't even be saved -- the database refuses it since migration 047; the scan still looks, as a second layer)
    expected = {("Checks", "Printed but no check number"),
                ("Checks", "Stale check (over 6 months, not cleared)"), ("Checks", "Dated more than a year ahead"),
                ("Checks", "Bank paid this check number before our check's date"),
                ("Invoices and batches", "Due date before the invoice date"), ("Invoices and batches", "Unusual year"),
                ("Invoices and batches", "Invoice not in any batch"), ("Invoices and batches", "Empty batch"),
                ("Invoices and batches", "Batch overpaid"), ("Invoices and batches", "Check payee differs from the batch supplier"),
                ("Bank vs our records", "Bank cleared a different amount"),
                ("Suppliers", "Same name twice"), ("Suppliers", "Inactive but still owed"), ("Suppliers", "Supplier with no terms")}
    assert expected <= got, expected - got
    names = {f["detail"].split(" is on file")[0] for f in findings(today=TODAY) if f["check"] == "Same name twice"}
    assert names == {"S&P Tires"}                                                                 # Metrobank loan + card is fine
    empty_f = next(f for f in findings(today=TODAY) if f["check"] == "Empty batch")
    assert empty_f["page"] == "order-statements" and empty_f["state"]["selected_statement_id"] == empty


def test_its_fine_hides_a_finding_until_show_again(db):
    s, sid = _clean(db)
    with sqlite3.connect(db) as conn:
        _check(conn, s, None, "2027-01-22", 70.0, sid)
    got = findings(today=TODAY)
    assert [x["check"] for x in got] == ["Printed but no check number"], got
    f = got[0]
    mark_fine([f["key"]], "numbers come after printing")
    assert findings(today=TODAY) == [] and open_count() == 0
    [hidden] = findings(include_fine=True, today=TODAY)
    assert hidden["fine"] and hidden["note"] == "numbers come after printing"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'DATA_HEALTH_FINE'").fetchone()[0] == 1
    show_again([f["key"]])
    assert len(findings(today=TODAY)) == 1
