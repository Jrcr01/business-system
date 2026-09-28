"""engines/duplicate_service.py -- soft 'looks like a double recording' warnings, one per track."""
import sqlite3
from datetime import date, timedelta

from conftest import make_creditor
from engines import duplicate_service as d
from engines.bank_loan_service import create_bank_loan, add_bank_loan_installment


def _exec(db, sql, params=()):
    conn = sqlite3.connect(db); conn.execute(sql, params); conn.commit(); conn.close()


def test_invoice_same_amount_and_date_under_new_number_is_flagged(db):
    cid = make_creditor(db, creditor_type="SUPPLIER")
    _exec(db, "INSERT INTO invoices (creditor_id, doc_ref, invoice_date, due_date, gross_amount, amount, status) VALUES (?, 'A-1', '2026-09-01', '2026-10-01', 500, 500, 'Unpaid')", (cid,))

    assert d.invoice_warnings(cid, [("A-2", "2026-09-01", 500)])
    assert not d.invoice_warnings(cid, [("A-2", "2026-09-02", 500)])
    assert d.invoice_warnings(cid, [("B-1", "2026-09-05", 90), ("B-1", "2026-09-05", 90)])  # repeated in the same upload


def test_check_same_payee_amount_date_is_flagged(db):
    cid = make_creditor(db, creditor_type="SUPPLIER")
    _exec(db, "INSERT INTO checks_issued (bank_account_id, statement_id, creditor_id, check_number, amount, check_date, status) VALUES (1, 1, ?, '3990000001', 250, '2026-10-05', 'Active')", (cid,))

    assert d.check_warnings(cid, [(250, "2026-10-05")])
    assert not d.check_warnings(cid, [(250, "2026-10-06")])


def test_bank_schedule_copied_onto_second_loan_is_flagged(db):
    c1 = make_creditor(db, name="PNB", creditor_type="BANK_LOAN")
    a = create_bank_loan(c1, "L-A", "", date(2026, 1, 1), date(2027, 1, 1), None, 100000.0, "ADA")
    b = create_bank_loan(c1, "L-B", "", date(2026, 2, 1), date(2027, 2, 1), None, 90000.0, "ADA")
    for m in range(3):
        add_bank_loan_installment(a, date.today() + timedelta(days=30 * (m + 1)), 5000.0)

    rows = [(date.today() + timedelta(days=30 * (m + 1)), 5000.0) for m in range(3)]
    assert d.bank_schedule_warnings(b, rows)
    assert not d.bank_schedule_warnings(b, [(date.today() + timedelta(days=5), 1.0)])


def test_credit_card_statement_same_card_due_date_and_amount_is_flagged(db):
    cid = make_creditor(db, creditor_type="CREDIT_CARD")
    _exec(db, "INSERT INTO credit_cards (card_id, creditor_id, card_name, card_number_last4, credit_limit) VALUES (7, ?, 'Test Card', '1234', 50000)", (cid,))
    _exec(db, "INSERT INTO cc_statements (card_id, due_date, total_amount_due, previous_balance, new_charges, interest_charged, amount_paid, unpaid_balance_next, status) VALUES (7, '2026-10-10', 1500, 0, 1500, 0, 0, 1500, 'Unpaid')")

    assert d.cc_statement_warnings(7, "2026-10-10", 1500)
    assert not d.cc_statement_warnings(7, "2026-10-10", 1600)   # same date, different amount
    assert not d.cc_statement_warnings(7, "2026-11-10", 1500)   # same amount, different date
    assert not d.cc_statement_warnings(8, "2026-10-10", 1500)   # different card


def test_bank_loan_same_principal_and_start_is_flagged(db):
    c1 = make_creditor(db, name="PNB", creditor_type="BANK_LOAN")
    create_bank_loan(c1, "L-A", "ACC-1", date(2026, 1, 1), date(2027, 1, 1), None, 100000.0, "ADA")

    assert d.bank_loan_warnings(c1, "ACC-1", 1.0, date(2026, 5, 5))
    assert d.bank_loan_warnings(c1, "", 100000.0, date(2026, 1, 1))
    assert not d.bank_loan_warnings(c1, "OTHER", 5.0, date(2026, 5, 5))


def test_creditor_name_already_on_file_is_flagged(db):
    make_creditor(db, name="Acom Trading", creditor_type="SUPPLIER")
    assert d.creditor_warnings(" acom trading ", "SUPPLIER")
    assert not d.creditor_warnings("Someone Else", "SUPPLIER")


def test_loan_note_checks_must_match_the_schedule(db):
    cid = make_creditor(db, creditor_type="LENDER")
    conn = sqlite3.connect(db)
    ln = conn.execute("INSERT INTO loan_notes (creditor_id, note_ref, issue_date, principal_amount, monthly_interest_rate, total_interest, total_payable, status) VALUES (?, 'LN-T', '2026-01-01', 100000, 9, 0, 100000, 'Active')", (cid,)).lastrowid
    ids = []
    for n, (due, interest, amount) in enumerate([("2026-10-01", 9000, 109000), ("2026-11-01", 6000, 106000)], start=1):
        ck = conn.execute("INSERT INTO checks_issued (bank_account_id, creditor_id, loan_note_id, check_number, amount, check_date, status) VALUES (1, ?, ?, ?, ?, ?, 'Active')", (cid, ln, f"39900000{n}", amount, due)).lastrowid
        conn.execute("INSERT INTO loan_installments (loan_note_id, installment_no, due_date, principal, interest, outstanding_balance, check_id, status) VALUES (?, ?, ?, 100000, ?, 0, ?, 'Active')", (ln, n, due, interest, ck))
        ids.append(ck)
    conn.commit(); conn.close()

    assert d.loan_check_mismatches(ln) == []
    assert d.loan_check_mismatches(ln, {}) == []
    bad = d.loan_check_mismatches(ln, {ids[1]: (109000, "2026-11-01")})   # second check keyed as 109,000 instead of 106,000
    assert len(bad) == 1 and "106,000.00" in bad[0]
    assert d.loan_check_mismatches(ln, {ids[0]: (109000, "2026-10-05")})[0].count("due") == 1


def test_draft_refs_are_yy_mm_running_number(db):
    import app
    from datetime import datetime
    cid = make_creditor(db, creditor_type="SUPPLIER")
    a = app.create_order_statement(cid)
    b = app.create_order_statement(cid)
    refs = [r[0] for r in sqlite3.connect(db).execute("SELECT statement_ref FROM order_statements WHERE statement_id IN (?, ?) ORDER BY statement_id", (a, b))]
    stamp = datetime.now().strftime("%y-%m")
    assert refs == [f"DRAFT-{stamp}-01", f"DRAFT-{stamp}-02"]


def test_normalize_doc_ref_treats_common_variations_as_one_number():
    n = d.normalize_doc_ref
    assert n("SI-00123") == n("SI 123") == n("inv123") == n("123") == n("0123") == "123"
    assert n("A-123") == "A123" and n("A-123") != n("123")        # a letter that is part of the number is kept
    assert n("") == "" or n("") == "0"


def test_invoice_warning_catches_same_number_written_differently(db):
    cid = make_creditor(db, creditor_type="SUPPLIER")
    _exec(db, "INSERT INTO invoices (creditor_id, doc_ref, invoice_date, due_date, gross_amount, amount, status) VALUES (?, 'SI-00123', '2026-09-01', '2026-10-01', 500, 500, 'Unpaid')", (cid,))

    hits = d.invoice_warnings(cid, [("123", "2026-09-15", 999)])           # different amount and date, same number

    assert any("looks like the same number" in h and "SI-00123" in h for h in hits)
    assert d.invoice_warnings(cid, [("124", "2026-09-15", 999)]) == []
    assert any("written differently" in h for h in d.invoice_warnings(cid, [("SI-9", "2026-09-15", 1), ("9", "2026-09-15", 2)]))


def test_scan_finds_duplicates_across_batches_and_can_be_dismissed(db):
    from engines import duplicate_scan_service as scan
    cid = make_creditor(db, name="Acom Trading", creditor_type="SUPPLIER")
    conn = sqlite3.connect(db)
    b1 = conn.execute("INSERT INTO order_statements (creditor_id, statement_ref, total_amount, status) VALUES (?, 'LEGACY-MS-ACCESS-1', 1000, 'Issued')", (cid,)).lastrowid
    b2 = conn.execute("INSERT INTO order_statements (creditor_id, statement_ref, total_amount, status) VALUES (?, 'DRAFT-26-09-01', 1000, 'Draft')", (cid,)).lastrowid
    for batch, ref, day in ((b1, "SI-77", "2026-09-01"), (b2, "77", "2026-09-05")):
        conn.execute("INSERT INTO invoices (creditor_id, doc_ref, invoice_date, due_date, gross_amount, amount, status, statement_id) VALUES (?, ?, ?, '2026-12-01', 400, 400, 'Unpaid', ?)", (cid, ref, day, batch))
    conn.commit(); conn.close()

    found = scan.scan_possible_duplicates()

    assert set(found["kind"]) == {"Same invoice number", "Same batch total"}
    assert "migrated" in found[found["kind"] == "Same batch total"]["detail"].iloc[0]
    key = found[found["kind"] == "Same invoice number"]["dup_key"].iloc[0]
    scan.dismiss([key])
    assert set(scan.scan_possible_duplicates()["kind"]) == {"Same batch total"}
    assert len(scan.scan_possible_duplicates(include_dismissed=True)) == 2
    scan.restore([key])
    assert len(scan.scan_possible_duplicates()) == 2


def test_credit_card_second_statement_same_month_and_odd_total_are_flagged(db):
    cid = make_creditor(db, creditor_type="CREDIT_CARD")
    _exec(db, "INSERT INTO credit_cards (card_id, creditor_id, card_name, card_number_last4, credit_limit) VALUES (7, ?, 'Test Card', '1234', 50000)", (cid,))
    _exec(db, "INSERT INTO cc_statements (card_id, statement_month, due_date, total_amount_due, previous_balance, new_charges, interest_charged, amount_paid, unpaid_balance_next, status) VALUES (7, '2026-10', '2026-10-10', 1500, 0, 1500, 0, 0, 1500, 'Unpaid')")

    assert d.cc_statement_month_warnings(7, "2026-10", 1500)          # same month again
    assert not d.cc_statement_month_warnings(7, "2026-11", 1600)      # next month, similar amount: fine
    assert d.cc_statement_month_warnings(7, "2026-11", 15000)         # ten times the last one: probably a typo


def test_credit_card_same_month_warning_uses_the_statement_date(db):
    """Owner, 2026-09-29: a statement's month is its STATEMENT DATE's month (card statement day 8: due Jul 7 = the Jun 8 statement,
    due Jul 29 = the Jul 8 statement) -- and "no two statement in a month". Old labels (the due month) must not raise false warnings."""
    cid = make_creditor(db, creditor_type="CREDIT_CARD")
    _exec(db, "INSERT INTO credit_cards (card_id, creditor_id, card_name, card_number_last4, credit_limit, statement_day) VALUES (8, ?, 'Toyota', '4047', 50000, 8)", (cid,))
    _exec(db, "INSERT INTO cc_statements (card_id, statement_month, due_date, total_amount_due, status) VALUES (8, '2026-07', '2026-07-07', 3800, 'Paid')")
    from datetime import date
    assert not d.cc_statement_month_warnings(8, "2026-07", 4000, due_date=date(2026, 7, 29))   # the July statement: no clash with the old "2026-07" label
    assert d.cc_statement_month_warnings(8, "2026-06", 3800, due_date=date(2026, 6, 29))       # also the June 8 statement: warned
