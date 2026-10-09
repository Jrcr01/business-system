"""Invoice vs delivery receipt (Phase 5, owner 2026-10-08): the sales invoice no. is the reference; a DR number is kept beside it (dr_no), never read
as the invoice number. Spreadsheets, the multi-batch workbook and the CSV template all follow it."""
import io

from openpyxl import Workbook

from engines.invoice_import_service import get_template_csv, parse_invoice_spreadsheet, parse_invoice_workbook


def test_a_dr_column_goes_beside_the_sales_invoice_never_into_it():
    csv = b"Sales Invoice No,DR No,Date,Amount,Less %\nSI-100,DR-77,2026-09-01,1000,0\nSI-101,,2026-09-02,500,5\n"
    rows, notes = parse_invoice_spreadsheet(csv, "x.csv")
    assert notes == [] and [(r.doc_ref, r.dr_no) for r in rows] == [("SI-100", "DR-77"), ("SI-101", "")]


def test_a_sheet_with_only_a_dr_column_is_not_taken_as_invoice_numbers():
    csv = b"DR No,Date,Amount\nDR-77,2026-09-01,1000\n"
    rows, notes = parse_invoice_spreadsheet(csv, "x.csv")
    assert rows[0].doc_ref == "DR 77" and rows[0].dr_no == "DR-77" and not notes          # SI or DR (2026-10-09): a DR alone is the reference
    _, notes = parse_invoice_spreadsheet(b"DR No,Date,Amount\n,2026-09-01,1000\n", "x.csv")
    assert "SI no. or DR no." in notes[0]                                                  # neither: flagged


def test_older_headers_still_work():
    rows, _ = parse_invoice_spreadsheet(b"Doc Ref,Date,Amount,Less %\nINV-1,2026-09-01,100,0\n", "x.csv")
    assert rows[0].doc_ref == "INV-1" and rows[0].dr_no == ""


def test_the_workbook_and_the_template_carry_the_dr():
    wb = Workbook()
    ws = wb.active
    ws.title = "Goodyear"
    ws.append(["Sales Invoice No", "Date", "Amount", "Less %", "DR No"])          # the Excel pop-up's columns (DR in E)
    ws.append(["SI-9", "2026-09-01", 900, 0, "DR-5"])
    buf = io.BytesIO()
    wb.save(buf)
    sheets, problems = parse_invoice_workbook(buf.getvalue())
    assert problems == [] and (sheets[0].rows[0].doc_ref, sheets[0].rows[0].dr_no) == ("SI-9", "DR-5")
    assert get_template_csv().startswith(b"Sales Invoice No,DR No,")
