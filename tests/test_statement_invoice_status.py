"""Invoice status printed on the batch statement (2026-09-27): what has actually happened to the money, not just 'Unpaid'."""
import sqlite3
from datetime import date, timedelta

import app
from conftest import bank_clear, make_creditor, make_statement, fetchone
from engines.batch_statement_service import _fetch_batch_data, generate_batch_statement_pdf


def _batch(db, invoice_amount=10000.0):
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), invoice_amount, [], statement_id)
    invoice_id = fetchone(db, "SELECT invoice_id FROM invoices WHERE statement_id = ?", (statement_id,))[0]
    return creditor_id, statement_id, invoice_id


def _park(db, creditor_id, statement_id, invoice_id, amount, when):
    _, _, check_id = app.issue_check(creditor_id, amount, when, statement_id=statement_id)
    assert app.allocate_check_to_invoices(check_id, [(invoice_id, amount)])[0]
    return check_id


def _label(statement_id):
    return _fetch_batch_data(statement_id, True)["invoices"][0]["display_status"]


def test_unpaid_until_a_check_covers_it(db):
    creditor_id, statement_id, invoice_id = _batch(db)
    assert _label(statement_id) == "Unpaid"


def test_partly_covered_when_checks_cover_only_part(db):
    creditor_id, statement_id, invoice_id = _batch(db)
    _park(db, creditor_id, statement_id, invoice_id, 4000.0, date.today() + timedelta(days=30))
    assert _label(statement_id) == "Partly covered"


def test_check_issued_with_the_last_check_date_when_fully_covered_but_not_cleared(db):
    creditor_id, statement_id, invoice_id = _batch(db)
    _park(db, creditor_id, statement_id, invoice_id, 4000.0, date.today() + timedelta(days=30))
    later = date.today() + timedelta(days=60)
    _park(db, creditor_id, statement_id, invoice_id, 6000.0, later)

    assert _label(statement_id) == f"Check issued \u00b7 {later:%m/%d/%Y}"


def test_paid_once_every_covering_check_has_cleared(db):
    creditor_id, statement_id, invoice_id = _batch(db)
    check_id = _park(db, creditor_id, statement_id, invoice_id, 10000.0, date.today() - timedelta(days=1))
    assert _label(statement_id) != "Paid"                                    # dated yesterday, not cleared: still owed (2026-10-08)
    bank_clear(db, check_id)
    assert _label(statement_id) == "Paid"


def test_the_stored_status_is_untouched_and_the_pdf_still_builds(db):
    creditor_id, statement_id, invoice_id = _batch(db)
    _park(db, creditor_id, statement_id, invoice_id, 10000.0, date.today() + timedelta(days=30))

    assert fetchone(db, "SELECT status FROM invoices WHERE invoice_id = ?", (invoice_id,))[0] in ("Unpaid", "Partially Paid", "Paid")
    assert generate_batch_statement_pdf(statement_id).startswith(b"%PDF")


def test_the_header_status_is_draft_parked_or_issued_by_the_checks(db):
    from engines.check_run_service import create_check_print_batch
    creditor_id, statement_id, invoice_id = _batch(db)
    header = lambda: _fetch_batch_data(statement_id, True)["statement"]["status"]
    assert header() == "Draft"                                                # no checks yet
    check_id = _park(db, creditor_id, statement_id, invoice_id, 10000.0, date.today() + timedelta(days=30))
    assert header() == "Parked"                                               # parked, none printed
    create_check_print_batch([check_id])
    assert header() == "Issued"                                               # exported / printed


def test_a_section_heading_is_never_stranded_from_its_table_at_a_page_bottom(db):
    """2026-09-27: 'Checks' used to sit alone at the bottom of page 1 with its table on page 2. The number of invoices decides where the Checks
    heading lands, so try a spread of them: on every page that shows a heading, the table's header row must be there too."""
    import io
    import pdfplumber
    creditor_id = make_creditor(db, name="ACME Tires")
    stranded = []
    for n_invoices in range(6, 30):
        with sqlite3.connect(db) as conn:
            conn.execute("INSERT INTO order_statements (creditor_id, statement_ref, total_amount, status) VALUES (?, ?, 0, 'Draft')", (creditor_id, f"T-{n_invoices}"))
            statement_id = conn.execute("SELECT statement_id FROM order_statements WHERE statement_ref = ?", (f"T-{n_invoices}",)).fetchone()[0]
        for i in range(n_invoices):
            app.add_invoice(creditor_id, f"INV-{n_invoices}-{i}", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)
        for i in range(4):
            app.issue_check(creditor_id, 1000.0, date(2026, 3, 1 + i), statement_id=statement_id)
        pdf = generate_batch_statement_pdf(statement_id, True, "filing", False)
        with pdfplumber.open(io.BytesIO(pdf)) as doc:
            for page in doc.pages:
                lines = [ln.strip() for ln in (page.extract_text() or "").split(chr(10))]
                if not any(marker in " ".join(lines) for marker in ("Check Number", "Sales Invoice", "BATCH / ORDER STATEMENT", "Payment Application")):
                    stranded.append((n_invoices, "blank page (footer only)"))
                for heading, header_row in (("Checks", "Check Number"), ("Invoices", "Sales Invoice")):          # the column was "Doc Ref" until 2026-10-03
                    if heading in lines and not any(ln.startswith(header_row) for ln in lines):
                        stranded.append((n_invoices, heading))
    assert stranded == [], f"heading alone / blank page for: {stranded}"


def test_a_statement_that_only_just_overflows_is_tightened_to_fit_one_page_per_copy(db):
    """2026-09-27: 11 invoices + 8 checks overflowed by a single check row, leaving a page that held one row and white space (twice, for 2 copies).
    Rows are tightened just enough to fit, so each copy is one page."""
    import io
    import pdfplumber
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    for i in range(11):
        app.add_invoice(creditor_id, f"INV-{i}", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)
    for i in range(8):
        app.issue_check(creditor_id, 1375.0, date(2026, 3, 1 + i), statement_id=statement_id)

    pdf = generate_batch_statement_pdf(statement_id, True, "both", False)

    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        assert len(doc.pages) == 2
