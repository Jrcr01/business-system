"""Multi-sheet invoice workbook: one sheet per supplier (tab) -> one new batch each."""
import io

import pandas as pd

from engines.invoice_import_service import match_supplier, parse_invoice_workbook

SUPPLIERS = {1: "Acom Trading", 2: "Advance Marketing", 3: "CSS Autoparts Inc."}


def _workbook(sheets: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, rows in sheets.items():
            pd.DataFrame(rows).to_excel(writer, sheet_name=name, index=False)
    return buf.getvalue()


def test_every_sheet_is_read_with_its_own_rows():
    data = _workbook({
        "Acom Trading": [{"Doc Ref": "A-1", "Date": "2026-09-01", "Amount": "1,000.50", "Less %": "0"}],
        "Advance Marketing": [{"Doc Ref": "B-1", "Date": "2026-09-02", "Amount": "250"}, {"Doc Ref": "B-2", "Date": "2026-09-03", "Amount": "300"}],
    })

    sheets, problems = parse_invoice_workbook(data)

    assert problems == []
    assert [s.sheet_name for s in sheets] == ["Acom Trading", "Advance Marketing"]
    assert [len(s.rows) for s in sheets] == [1, 2]
    assert sheets[0].rows[0].amount == 1000.50


def test_bad_workbook_reports_a_problem_instead_of_raising():
    sheets, problems = parse_invoice_workbook(b"not an excel file")
    assert sheets == [] and problems


def test_tab_names_are_matched_to_suppliers():
    assert match_supplier("Acom Trading", SUPPLIERS) == 1
    assert match_supplier("acom trading", SUPPLIERS) == 1
    assert match_supplier("Acom - Sept", SUPPLIERS) == 1          # supplier name inside the tab name
    assert match_supplier("CSS Autoparts", SUPPLIERS) == 3        # tab name inside the supplier name
    assert match_supplier("Advance Marketng", SUPPLIERS) == 2     # small typo
    assert match_supplier("Sheet1", SUPPLIERS) is None


def test_each_sheet_becomes_its_own_batch_with_its_invoices(db):
    """Mirrors what the Order Statement tab does on 'Create batches and import invoices'."""
    import sqlite3
    import app
    from conftest import make_creditor
    from engines.db_engine import promote_draft_statement

    acom = make_creditor(db, name="Acom Trading", creditor_type="SUPPLIER")
    data = _workbook({
        "Acom Trading": [{"Doc Ref": "A-1", "Date": "2026-09-01", "Amount": "1000"}],
        "Acom Trading (2)": [{"Doc Ref": "A-2", "Date": "2026-09-02", "Amount": "500"}],   # same supplier, second batch
    })
    sheets, _ = parse_invoice_workbook(data)
    for sh in sheets:
        assert match_supplier(sh.sheet_name, {acom: "Acom Trading"}) == acom
        sid = app.create_order_statement(acom)
        for r in sh.rows:
            app.add_invoice(acom, r.doc_ref, r.invoice_date, app.resolve_due_date(r.invoice_date, "120"), r.amount, [], sid)
        promote_draft_statement(sid, "Issued")

    conn = sqlite3.connect(db)
    batches = conn.execute("SELECT statement_ref, status, total_amount FROM order_statements ORDER BY statement_id").fetchall()
    assert len(batches) == 2 and all(b[1] == "Issued" for b in batches)
    assert sorted(b[2] for b in batches) == [500.0, 1000.0]


def test_helper_tabs_starting_with_underscore_are_ignored():
    data = _workbook({"_Start": [{"Notes": "read me"}], "_Suppliers": [{"Supplier": "Acom Trading"}],
                      "Acom Trading": [{"Doc Ref": "A-1", "Date": "2026-09-01", "Amount": "10"}]})
    sheets, _ = parse_invoice_workbook(data)
    assert [s.sheet_name for s in sheets] == ["Acom Trading"]


def test_downloadable_entry_workbook_refreshes_suppliers_and_keeps_the_macros(db):
    import io as _io
    import zipfile
    from openpyxl import load_workbook
    from conftest import make_creditor
    from engines.invoice_template_service import MASTER_PATH, build_invoice_entry_workbook

    make_creditor(db, name="Zeta Supply", creditor_type="SUPPLIER")
    data = build_invoice_entry_workbook()

    wb = load_workbook(_io.BytesIO(data), keep_vba=True)
    assert wb.sheetnames == ["_Start", "_Suppliers"]              # test batch tabs stripped
    assert [r[0].value for r in wb["_Suppliers"].iter_rows(min_row=2, max_col=1)] == ["Zeta Supply"]
    with zipfile.ZipFile(_io.BytesIO(data)) as z, zipfile.ZipFile(MASTER_PATH) as m:
        assert z.read("xl/vbaProject.bin") == m.read("xl/vbaProject.bin")   # macros + form carried through unchanged


def test_bank_abbreviations_expand_to_full_names_when_created(db):
    import app
    from engines.bank_names import expand_bank_name

    assert expand_bank_name("bdo") == "BDO Unibank, Inc."
    assert expand_bank_name("Some Other Bank") == "Some Other Bank"
    issuer_id = app.get_or_create_card_issuer("MBTC")
    assert app.get_or_create_card_issuer("Metropolitan Bank & Trust Company") == issuer_id   # no second record
    loan_creditor = app.add_creditor("PNB", "BANK_LOAN", "", "", "", "")
    import sqlite3
    assert sqlite3.connect(db).execute("SELECT creditor_name FROM creditors WHERE creditor_id = ?", (loan_creditor,)).fetchone()[0] == "Philippine National Bank"


def test_rename_script_only_touches_bank_records(db):
    import sqlite3
    import rename_banks_full_names as rn
    from conftest import make_creditor
    bank = make_creditor(db, name="BDO", creditor_type="BANK_LOAN")
    supplier = make_creditor(db, name="BDO", creditor_type="SUPPLIER")   # a supplier that happens to share the letters
    assert rn.main.__module__ == "rename_banks_full_names"
    import sys
    sys.argv = ["x", "--db", db]
    assert rn.main() == 0
    names = dict(sqlite3.connect(db).execute("SELECT creditor_id, creditor_name FROM creditors WHERE creditor_id IN (?, ?)", (bank, supplier)).fetchall())
    assert names[bank] == "BDO Unibank, Inc." and names[supplier] == "BDO"


def test_proper_case_only_converts_all_caps_or_all_lowercase_names():
    from engines.name_case import smart_proper_case as f
    assert f("BDO UNIBANK, INC.") == "BDO Unibank, Inc."
    assert f("S&P ENTERPRISES INC.") == "S&P Enterprises Inc."
    assert f("tire asia group inc") == "Tire Asia Group Inc"
    assert f("CTGlobal Enterprises Corp.") == "CTGlobal Enterprises Corp."   # mixed case is trusted
    assert f("BANK OF THE PHILIPPINE ISLANDS") == "Bank of the Philippine Islands"
