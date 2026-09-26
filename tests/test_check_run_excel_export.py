"""Excel export of the pending check run (2026-09-17) -- checks now get printed outside this
system via Chrysanth's own batch-print feature, so this is the handoff point: park checks here,
export, import into Chrysanth, print, then come back and assign check numbers."""
import io
from datetime import date

import pandas as pd

import app
from conftest import make_creditor, make_statement
from engines.check_run_service import generate_pending_check_run_excel


def test_excel_export_contains_expected_columns_and_values(db):
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 5000.0, [], statement_id)
    app.issue_check(creditor_id, 5000.0, date(2026, 3, 1), statement_id=statement_id)

    excel_bytes = generate_pending_check_run_excel()
    df = pd.read_excel(io.BytesIO(excel_bytes))

    assert list(df.columns) == ["Payee Name", "Amount", "Check Date", "Reference"]
    assert len(df) == 1
    assert df["Payee Name"].iloc[0] == "ACME Tires"
    assert df["Amount"].iloc[0] == 5000.0
    assert df["Check Date"].iloc[0] == pd.Timestamp(2026, 3, 1)


def test_excel_export_filters_to_given_check_ids(db):
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 9000.0, [], statement_id)
    _, _, check_id_1 = app.issue_check(creditor_id, 5000.0, date(2026, 3, 1), statement_id=statement_id)
    _, _, check_id_2 = app.issue_check(creditor_id, 4000.0, date(2026, 3, 2), statement_id=statement_id)

    excel_bytes = generate_pending_check_run_excel([check_id_1])
    df = pd.read_excel(io.BytesIO(excel_bytes))

    assert len(df) == 1
    assert df["Amount"].iloc[0] == 5000.0


def test_excel_export_excludes_already_printed_checks(db):
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 5000.0, [], statement_id)
    _, _, check_id = app.issue_check(creditor_id, 5000.0, date(2026, 3, 1), statement_id=statement_id)
    app.mark_checks_printed([check_id])

    excel_bytes = generate_pending_check_run_excel()
    df = pd.read_excel(io.BytesIO(excel_bytes))

    assert df.empty


def test_csv_export_matches_the_chrysanth_sample_layout(db):
    from engines.check_run_service import generate_pending_check_run_csv
    creditor_id = make_creditor(db, name="Roy Ang")
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 5000.5, [], statement_id)
    app.issue_check(creditor_id, 5000.5, date(2026, 7, 10), statement_id=statement_id)

    raw = generate_pending_check_run_csv()
    df = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False)

    assert raw.startswith(b"\xef\xbb\xbf")                                          # Excel-friendly BOM
    assert list(df.columns) == ["Date", "Account No", "Payee", "Amount", "Cheque No", "Remark", "A/C Payee Only"]
    row = df.iloc[0]
    assert (row["Date"], row["Payee"], row["Amount"]) == ("7/10/2026", "Roy Ang", "5000.50")
    assert row["Account No"] == "" and row["Cheque No"] == ""                      # no account number; not numbered yet
    assert row["A/C Payee Only"] == "Y" and row["Remark"] != ""


def test_csv_export_filters_to_given_check_ids(db):
    from engines.check_run_service import generate_pending_check_run_csv
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 9000.0, [], statement_id)
    _, _, check_id_1 = app.issue_check(creditor_id, 5000.0, date(2026, 3, 1), statement_id=statement_id)
    app.issue_check(creditor_id, 4000.0, date(2026, 3, 2), statement_id=statement_id)

    df = pd.read_csv(io.BytesIO(generate_pending_check_run_csv([check_id_1])), dtype=str)

    assert len(df) == 1 and df["Amount"].iloc[0] == "5000.00"


def test_a_logged_batch_can_be_exported_again(db):
    from engines.check_run_service import create_check_print_batch, generate_batch_csv, generate_pending_check_run_csv
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 9000.0, [], statement_id)
    _, _, id1 = app.issue_check(creditor_id, 5000.0, date(2026, 3, 1), statement_id=statement_id)
    _, _, id2 = app.issue_check(creditor_id, 4000.0, date(2026, 3, 2), statement_id=statement_id)
    batch_id = create_check_print_batch([id1, id2])           # marks them printed, so the pending export is now empty

    assert len(pd.read_csv(io.BytesIO(generate_pending_check_run_csv()), dtype=str)) == 0
    again = pd.read_csv(io.BytesIO(generate_batch_csv(batch_id)), dtype=str)

    assert list(again["Amount"]) == ["5000.00", "4000.00"]


def _numbered_check(db, name, amount, when, number, shared=None):
    creditor_id, statement_id = shared if shared else (None, None)
    if creditor_id is None:
        creditor_id = make_creditor(db, name=name)
        statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-" + (number or f"X{when.day}"), date(2026, 1, 1), date(2026, 1, 31), amount, [], statement_id)
    _, _, check_id = app.issue_check(creditor_id, amount, when, statement_id=statement_id)
    if number:
        app.assign_check_number(check_id, None, None, number)
    return check_id


def test_chrysanth_txt_is_ms_dos_text_like_the_template_that_imported(db):
    from engines.check_run_service import generate_pending_check_run_txt, save_chrysanth_settings, CHRYSANTH_TXT_COLUMNS
    assert save_chrysanth_settings("399", 0)[0]
    _numbered_check(db, "Roy Ang", 10000.0, date(2026, 10, 5), "3992495984")

    data, problems, used = generate_pending_check_run_txt()

    assert not data.startswith((b"\xff\xfe", b"\xef\xbb\xbf"))          # plain single-byte text: no UTF-16, no BOM
    assert b"\x00" not in data
    assert data.endswith(b"\r\n")
    lines = data.decode("cp437").split("\r\n")
    assert len(lines) == 3 and lines[-1] == ""                          # header + 1 row, each ended by CRLF
    assert lines[0].split("\t") == CHRYSANTH_TXT_COLUMNS == ["AccountNo", "Payee", "Amount", "Date", "ChequeNo", "CrossingOption", "Remark", "Place"]
    row = lines[1].split("\t")
    assert len(row) == 8 and problems == [] and len(used) == 1
    assert row[:6] == ["399", "Roy Ang", "10000", "2026-10-05", "3992495984", "0"]      # amount without .00
    assert row[7] == ""                                                 # Place stays empty


def test_chrysanth_txt_amount_has_no_trailing_zeros():
    from engines.check_run_service import _txt_amount
    assert _txt_amount(10000.0) == "10000"
    assert _txt_amount(12345.6) == "12345.6"
    assert _txt_amount(0.05) == "0.05"
    assert _txt_amount(106000.0) == "106000"


def test_chrysanth_txt_gives_unnumbered_checks_a_unique_placeholder_number(db):
    from conftest import fetchone
    from engines.check_run_service import generate_pending_check_run_txt, save_chrysanth_settings, placeholder_cheque_number
    save_chrysanth_settings("ACC-1", 2)
    _numbered_check(db, "Shared Co", 1000.0, date(2026, 10, 5), "3992495985")
    cid, sid = fetchone(db, "SELECT creditor_id, statement_id FROM checks_issued LIMIT 1")
    _numbered_check(db, "Shared Co", 2000.0, date(2026, 10, 6), "", shared=(cid, sid))
    _numbered_check(db, "Shared Co", 3000.0, date(2026, 10, 7), "", shared=(cid, sid))

    data, notes, used = generate_pending_check_run_txt()

    rows = [ln.split("\t") for ln in data.decode("cp437").split("\r\n")[1:] if ln]
    numbers = [r[4] for r in rows]
    assert len(used) == 3 and len(set(numbers)) == 3                         # every row has a number, all different
    assert "3992495985" in numbers
    fake = [n for n in numbers if n != "3992495985"]
    assert all(n.startswith("9") and len(n) == 12 and n.isdigit() for n in fake)
    assert len(notes) == 1 and "temporary placeholder" in notes[0] and "2 check(s)" in notes[0]
    # nothing was stored on the checks themselves: they still have no number in the system
    assert fetchone(db, "SELECT COUNT(*) FROM checks_issued WHERE check_number IS NULL")[0] == 2
    # the same file again gives the same placeholders (a re-download of a batch matches the original)
    assert [ln.split("\t")[4] for ln in generate_pending_check_run_txt()[0].decode("cp437").split("\r\n")[1:] if ln] == numbers
    assert placeholder_cheque_number(404, "2026 - OS 0012") == "900012000404"         # 9 + OS 12 + check 404
    assert placeholder_cheque_number(7, "2026 - OS 0003") < placeholder_cheque_number(2, "2026 - OS 0012")   # earlier order statement sorts first
    assert placeholder_cheque_number(5, None, loan_note_id=3) > placeholder_cheque_number(9, "2026 - OS 0999")  # lenders after every OS


def test_chrysanth_settings_are_validated(db):
    from engines.check_run_service import save_chrysanth_settings, get_chrysanth_settings
    assert not save_chrysanth_settings("", 2)[0]
    assert not save_chrysanth_settings("X" * 21, 2)[0]
    assert not save_chrysanth_settings("ACC-1", 9)[0]
    assert save_chrysanth_settings("ACC-1", 4)[0]
    assert get_chrysanth_settings() == {"account_no": "ACC-1", "crossing": 4}


def test_each_export_batch_tags_every_remark_so_it_can_be_searched_in_chrysanth(db):
    from conftest import fetchone
    from engines.check_run_service import generate_pending_check_run_txt, generate_batch_txt, save_chrysanth_settings, print_batch_tag
    save_chrysanth_settings("399", 0)
    _numbered_check(db, "Shared Co", 1000.0, date(2026, 10, 5), "3992495985")
    cid, sid = fetchone(db, "SELECT creditor_id, statement_id FROM checks_issued LIMIT 1")

    data, notes, used = generate_pending_check_run_txt(log_batch=True)
    remarks = [ln.split("\t")[6] for ln in data.decode("cp437").split("\r\n")[1:] if ln]
    assert remarks and set(remarks) == {"RUN 001"}                       # first batch -> RUN 001

    _numbered_check(db, "Shared Co", 2000.0, date(2026, 10, 6), "3992495986", shared=(cid, sid))
    data2, _, used2 = generate_pending_check_run_txt(log_batch=True)
    assert [ln.split("\t")[6] for ln in data2.decode("cp437").split("\r\n")[1:] if ln][0] == "RUN 002"
    assert len(used2) == 1                                                                # the first batch's check is not exported again

    batch_1 = fetchone(db, "SELECT MIN(print_batch_id) FROM check_print_batches")[0]
    again, _ = generate_batch_txt(batch_1)                                                # a re-download keeps the same tag
    assert [ln.split("\t")[6] for ln in again.decode("cp437").split("\r\n")[1:] if ln][0] == "RUN 001"
    assert print_batch_tag(batch_1) == "RUN 001"


def test_placeholders_sort_by_order_statement_and_are_remembered_for_redownloads(db):
    from conftest import fetchone
    import sqlite3 as _sq
    from engines.check_run_service import generate_pending_check_run_txt, generate_batch_txt, save_chrysanth_settings
    save_chrysanth_settings("399", 0)
    _numbered_check(db, "Beta Co", 1000.0, date(2026, 10, 5), "")
    conn = _sq.connect(db)
    conn.execute("UPDATE order_statements SET statement_ref = '2026 - OS 0012'")
    conn.commit()
    conn.close()

    data, notes, used = generate_pending_check_run_txt(log_batch=True)
    rows = [ln.split("\t") for ln in data.decode("cp437").split("\r\n")[1:] if ln]
    assert rows[0][4].startswith("900012") and len(rows[0][4]) == 12                      # OS 0012 leads the number
    stored = fetchone(db, "SELECT export_placeholder FROM checks_issued")[0]
    assert stored == rows[0][4]

    batch = fetchone(db, "SELECT MAX(print_batch_id) FROM check_print_batches")[0]
    again, _ = generate_batch_txt(batch)
    assert [ln.split("\t")[4] for ln in again.decode("cp437").split("\r\n")[1:] if ln] == [rows[0][4]]   # a re-download gives the same number


def test_checks_exported_under_the_old_scheme_keep_their_old_placeholder(db):
    from conftest import fetchone
    from engines.check_run_service import generate_batch_txt, save_chrysanth_settings, create_check_print_batch
    save_chrysanth_settings("399", 0)
    check_id = _numbered_check(db, "Beta Co", 1000.0, date(2026, 10, 5), "")
    create_check_print_batch([check_id])                       # exported before the new scheme existed: no export_placeholder stored

    batch = fetchone(db, "SELECT MAX(print_batch_id) FROM check_print_batches")[0]
    data, _ = generate_batch_txt(batch)

    assert [ln.split("\t")[4] for ln in data.decode("cp437").split("\r\n")[1:] if ln] == [f"9{check_id:011d}"]


def test_file_name_says_which_order_statements_it_holds():
    from engines.check_run_service import chrysanth_file_name as f
    assert f(["2026 - OS 0006"], "RUN 004") == "ChequeTransaction_RUN004_OS0006.txt"
    assert f(["2026 - OS 0008", "2026 - OS 0006", "2026 - OS 0007"], "RUN 004") == "ChequeTransaction_RUN004_OS0006-OS0008.txt"     # consecutive -> a range
    assert f(["2026 - OS 0006", "2026 - OS 0008", "2026 - OS 0011"], "RUN 005") == "ChequeTransaction_RUN005_OS0006+OS0008+OS0011.txt"
    assert f(["2026 - OS 0001", "2026 - OS 0004", "2026 - OS 0009", "2026 - OS 0012"], "RUN 006") == "ChequeTransaction_RUN006_OS0001-OS0012.txt"
    assert f(["2026 - OS 0006", "2026 - LN 0001"], "RUN 007") == "ChequeTransaction_RUN007_OS0006_Lenders.txt"
    assert f(["DRAFT-26-09-16"], "RUN 008") == "ChequeTransaction_RUN008_Draft.txt"
    assert f([], "RUN 009") == "ChequeTransaction_RUN009.txt"


def test_file_name_for_a_logged_batch_uses_its_order_statements(db):
    from engines.check_run_service import generate_pending_check_run_txt, file_name_for_checks, file_name_for_batch, save_chrysanth_settings
    from conftest import fetchone
    import sqlite3 as _sq
    save_chrysanth_settings("399", 0)
    _numbered_check(db, "Beta Co", 1000.0, date(2026, 10, 5), "3992495985")
    conn = _sq.connect(db)
    conn.execute("UPDATE order_statements SET statement_ref = '2026 - OS 0012'")
    conn.commit()
    conn.close()

    data, notes, ids = generate_pending_check_run_txt(log_batch=True)
    batch = fetchone(db, "SELECT MAX(print_batch_id) FROM check_print_batches")[0]

    assert file_name_for_checks(ids, batch) == file_name_for_batch(batch) == "ChequeTransaction_RUN001_OS0012.txt"
