"""Bank reconciliation v2 (2026-09-28, owner-approved): bank-loan / credit-card matching, suggested matches (accept / reject), linking by
hand, tagging and reopening, Open -> Reconciled per statement, lines from before the system set aside, the reconciliation statement
(bank vs issued checks) and the alerts (stale deposits default 7 days). Made-up statements only -- real ones never go into the project."""
import sqlite3
from datetime import date, timedelta

import app
from conftest import make_creditor, make_statement
from engines import bank_alerts
from engines.bank_recon_report import generate_recon_pdf, recon_statement
from engines.bank_statement_service import (
    _one_digit_apart, exceptions, import_statement, imports_status, link_to_check, match_checks, reject_suggestion, reopen, resolve,
    resolved_lines, series_hints, suggestions, tag_series,
)
from engines.cash_insights_service import set_buffer

HEADER = "Transaction Date,Posting Date,Time,Check Number,Transaction Description,Debit Amount,Credit Amount,Balance,Branch/Channel,Subscriber Number/TIN,Sequence Number"


def _csv(rows, opening=1_000_000.0):
    """rows: (mm/dd/yyyy, check number, description, debit, credit) -- running balances worked out here so they always add up."""
    bal, out = opening, []
    for i, (d, chk, desc, debit, credit) in enumerate(rows, 1):
        bal = round(bal - debit + credit, 2)
        out.append(f'{d},{d},23:59:59,{chk},{desc},"{debit:,.2f}","{credit:,.2f}","{bal:,.2f}",,,{i}')
    return (HEADER + "\n" + "\n".join(out) + "\n").encode("utf-8")


STATEMENT = [
    ("09/20/2026", "3991111111", "IM IN- CLRNG- BATCH", 250.0, 0.0),        # before the system's first check -> set aside
    ("10/05/2026", "3992000001", "IM IN- CLRNG- BATCH", 1000.0, 0.0),       # A: matched
    ("10/05/2026", "0000000000", "MO CSH DEP", 0.0, 20000.0),               # money in
    ("10/06/2026", "3992000011", "IM IN- CLRNG- BATCH", 1500.0, 0.0),       # T is 3992000010: one digit off -> suggestion
    ("10/06/2026", "3992000012", "IM IN- CLRNG- BATCH", 2100.0, 0.0),       # W is 3992000021: two digits swapped -> suggestion
    ("10/07/2026", "3992000999", "IM IN- CLRNG- BATCH", 700.0, 0.0),        # U has no number yet, same amount, 3 days -> suggestion
    ("10/06/2026", "0000000000", "MO DM", 25000.0, 0.0),                    # bank-loan installment due Oct 3 -> matched_loan
    ("10/06/2026", "0000000000", "MO DM", 12345.0, 0.0),                    # nothing matches -> unmatched_debit
    ("10/07/2026", "0000000000", "MO DM", 500000.0, 0.0),                   # unexplained and 20x the usual debit memo -> large-debit alert
    ("10/07/2026", "0000000000", "DE DM RET CHK", 300.0, 0.0),              # a customer's check bounced
    ("10/08/2026", "0000000000", "MO DM PAY UTLTY", 8000.0, 0.0),           # credit-card statement due Oct 1 -> matched_card
]


def _setup(db, monkeypatch, tmp_path, match_other=True):
    _match_cards_and_loans("1" if match_other else "0")      # these tests check the card / loan matching; the default is checks only (2026-10-17)
    monkeypatch.setenv("AP_DRIVE_DIR", str(tmp_path / "drive"))
    creditor_id = make_creditor(db, name="S&P Tires")
    sid = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 9, 1), date(2026, 10, 1), 10000.0, [], sid)
    ids = {}
    for key, num, amt, d in (("S", "3992000030", 400.0, "2026-09-25"), ("A", "3992000001", 1000.0, "2026-10-01"), ("E", "3992000005", 800.0, "2026-10-02"),
                             ("T", "3992000010", 1500.0, "2026-10-03"), ("W", "3992000021", 2100.0, "2026-10-03"), ("U", None, 700.0, "2026-10-04")):
        _, _, cid = app.issue_check(creditor_id, amt, date.fromisoformat(d), statement_id=sid)
        ids[key] = cid
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE checks_issued SET check_number = ? WHERE check_id = ?", (num, cid))
    with sqlite3.connect(db) as conn:
        bank = conn.execute("INSERT INTO creditors (creditor_name, creditor_type, terms) VALUES ('Metrobank', 'BANK_LOAN', '0')").lastrowid
        loan = conn.execute("INSERT INTO bank_loans (creditor_id, loan_ref) VALUES (?, 'BL-1')", (bank,)).lastrowid
        ids["inst"] = conn.execute("INSERT INTO bank_loan_installments (bank_loan_id, installment_no, due_date, amount) VALUES (?, 1, '2026-10-03', 25000)",
                                   (loan,)).lastrowid
        card = conn.execute("INSERT INTO credit_cards (creditor_id, card_name, card_number_last4) VALUES (?, 'Visa', '4242')", (bank,)).lastrowid
        ids["cc"] = conn.execute("INSERT INTO cc_statements (card_id, due_date, total_amount_due) VALUES (?, '2026-10-01', 8000)", (card,)).lastrowid
    res = import_statement(_csv(STATEMENT), "s.csv")
    assert res["new_lines"] == len(STATEMENT) and res["balance_ok"]
    return ids


def _line(db, **where):
    k, v = next(iter(where.items()))
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute(f"SELECT * FROM bank_transactions WHERE {k} = ?", (v,)).fetchone())


def test_digit_rule():
    assert _one_digit_apart("3992000010", "3992000011") and _one_digit_apart("3992000021", "3992000012")
    assert not _one_digit_apart("3992000010", "3992000010") and not _one_digit_apart("3992000010", "3992000099")
    assert not _one_digit_apart("123", "1234") and not _one_digit_apart("1234", "3214")          # swapped but not neighbours


def _match_cards_and_loans(value="1"):
    """Settings -> Bank -> bank_match_other_debits (owner, 2026-10-17: default 0 = checks only)."""
    from engines import settings_registry as sr
    from engines.acting_user import acting_as
    with acting_as("admin@slicks.test", "admin"):
        sr.save("bank_match_other_debits", value)


def test_loan_and_card_debits_are_matched_and_the_rest_left_to_tag(db, monkeypatch, tmp_path):
    ids = _setup(db, monkeypatch, tmp_path)
    loan = _line(db, debit=25000.0)
    card = _line(db, debit=8000.0)
    assert loan["match_status"] == "matched_loan" and loan["matched_installment_id"] == ids["inst"]
    assert card["match_status"] == "matched_card" and card["matched_cc_statement_id"] == ids["cc"]
    open_now = sorted((e["match_status"], e["debit"]) for e in exceptions() if not e["reviewed"])
    assert open_now == [("not_in_system", 700.0), ("not_in_system", 1500.0), ("not_in_system", 2100.0), ("returned_check", 300.0),
                        ("unmatched_debit", 12345.0), ("unmatched_debit", 500000.0)]
    before = _line(db, check_number="3991111111")          # cleared Sep 20, before the first check in the system (Sep 25)
    assert before["reviewed"] == 1 and before["resolution"] == "Before the system"
    match_checks()                                           # running it again changes nothing
    assert _line(db, debit=25000.0)["match_status"] == "matched_loan" and len([e for e in exceptions() if not e["reviewed"]]) == 6


def test_bank_debits_and_bounced_checks_before_the_system_are_tagged_automatically(db, monkeypatch, tmp_path):
    """Owner, 2026-09-28 ("resolve this all"): old lines are tagged the way the owner tags them by hand, and listed after the owner's own."""
    _setup(db, monkeypatch, tmp_path)
    resolve(_line(db, debit=12345.0)["txn_id"], "Other", "mine")
    import_statement(_csv([("09/21/2026", "0000000000", "MO DM PAY UTLTY", 111.0, 0.0),
                           ("09/22/2026", "0000000000", "DE DM RET CHK", 222.0, 0.0)], opening=5000.0), "older.csv")
    debit, bounced = _line(db, debit=111.0), _line(db, debit=222.0)
    assert (debit["reviewed"], debit["resolution"]) == (1, "Expected bank debit") and "before the system started" in debit["note"]
    assert (bounced["reviewed"], bounced["resolution"]) == (1, "Customer bounced check")
    assert _line(db, debit=300.0)["reviewed"] == 0                  # Oct 7, after the system started: still the owner's to look at
    assert resolved_lines()[0]["note"] == "mine"


def test_suggestions_accept_and_reject(db, monkeypatch, tmp_path):
    ids = _setup(db, monkeypatch, tmp_path)
    sugg = {(s["bank_check_number"], s["check_id"]): s["reason"] for s in suggestions()}
    assert sugg == {("3992000011", ids["T"]): "same amount, check number one digit off",
                    ("3992000012", ids["W"]): "same amount, check number one digit off",
                    ("3992000999", ids["U"]): "same amount, our check has no number yet, dated within 7 days"}
    t_line = _line(db, check_number="3992000011")["txn_id"]
    reject_suggestion(t_line, ids["T"])
    assert ("3992000011", ids["T"]) not in {(s["bank_check_number"], s["check_id"]) for s in suggestions()}
    w_line = _line(db, check_number="3992000012")["txn_id"]
    ok, _ = link_to_check(w_line, ids["W"], how="Suggested match accepted")
    assert ok
    line = _line(db, txn_id=w_line)
    assert line["match_status"] == "matched_manual" and line["reviewed"] == 1 and line["resolution"] == "Suggested match accepted"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT bank_cleared_date, bank_txn_id FROM checks_issued WHERE check_id = ?", (ids["W"],)).fetchone() == ("2026-10-06", w_line)
        assert conn.execute("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'BANK_LINE_LINKED'").fetchone()[0] == 1
    match_checks()                                           # a hand link is never undone by matching again
    assert _line(db, txn_id=w_line)["match_status"] == "matched_manual"


def test_a_check_already_cleared_cannot_be_linked_again(db, monkeypatch, tmp_path):
    ids = _setup(db, monkeypatch, tmp_path)
    other = _line(db, check_number="3992000999")["txn_id"]
    ok, msg = link_to_check(other, ids["A"])                 # A was cleared by its own bank line
    assert not ok and "already cleared" in msg
    assert _line(db, txn_id=other)["match_status"] == "not_in_system"


def test_resolving_everything_reconciles_the_statement_and_reopen_undoes(db, monkeypatch, tmp_path):
    ids = _setup(db, monkeypatch, tmp_path)
    assert [(s["status"], s["open_items"]) for s in imports_status()] == [("Open", 6)]
    u_line = _line(db, check_number="3992000999")["txn_id"]
    assert link_to_check(u_line, ids["U"])[0]
    for e in exceptions():
        if not e["reviewed"]:
            resolve(e["txn_id"], "Bank error" if e["kind"] == "CHECK" else "Expected bank debit", "checked with Metrobank")
    st_ = imports_status()[0]
    assert st_["status"] == "Reconciled" and st_["open_items"] == 0 and st_["reconciled_at"]
    assert {r["txn_id"] for r in resolved_lines()} >= {u_line}
    bounced = _line(db, debit=300.0)["txn_id"]
    reopen(bounced)
    assert _line(db, txn_id=bounced)["reviewed"] == 0 and imports_status()[0]["status"] == "Open"
    reopen(u_line)                                           # the hand link is taken back: U is not cleared any more
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT bank_cleared_date FROM checks_issued WHERE check_id = ?", (ids["U"],)).fetchone()[0] is None
        assert conn.execute("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'BANK_LINE_REOPENED'").fetchone()[0] == 2
    assert _line(db, txn_id=u_line)["match_status"] == "not_in_system"


def test_reconciliation_statement_math_and_pdf(db, monkeypatch, tmp_path):
    ids = _setup(db, monkeypatch, tmp_path)
    r = recon_statement(imports_status()[0]["import_id"])
    closing = 1_000_000.0 - sum(x[3] for x in STATEMENT) + sum(x[4] for x in STATEMENT)
    assert r["as_of"] == date(2026, 10, 8) and abs(r["bank_balance"] - closing) < 0.005
    # outstanding: numbered or printed, active, dated on or before Oct 8, not cleared: S, E, T, W (A cleared; U has no number, not printed)
    assert sorted(r["outstanding"]["amount"]) == [400.0, 800.0, 1500.0, 2100.0] and r["outstanding_total"] == 4800.0
    assert abs(r["available"] - (closing - 4800.0)) < 0.005
    how = dict(zip(r["bank_items"]["debit"], r["bank_items"]["how"]))
    assert how[25000.0].startswith("Bank-loan") and how[8000.0].startswith("Credit-card") and how[12345.0] == "Not resolved yet"
    assert r["money_in"] == 20000.0 and r["checks_cleared"] == 1 and r["open_items"] == 6
    pdf = generate_recon_pdf(r)                              # payee "S&P Tires": the & must not break the PDF
    assert pdf[:4] == b"%PDF" and len(pdf) > 2000


def test_alerts_default_seven_days_and_adjustable(db, monkeypatch, tmp_path):
    from engines import settings_registry as _sr
    _sr.save("show_cash_forecast", 1)                # the forecast is hidden by default (Settings); the low-cash alert is part of it
    ids = _setup(db, monkeypatch, tmp_path)
    assert bank_alerts.stale_days() == 7 and bank_alerts.debit_factor() == 5.0
    # waiting to Oct 8 (last bank date): S 13 days, E 6, T and W 5 -> only S at the 7-day default
    assert [r["check_id"] for r in bank_alerts.stale_deposits()] == [ids["S"]]
    bank_alerts.set_alert_settings(5, 3.0)
    assert bank_alerts.stale_days() == 5 and {r["check_id"] for r in bank_alerts.stale_deposits()} == {ids["S"], ids["E"], ids["T"], ids["W"]}
    bank_alerts.set_alert_settings(7, 5.0)
    big = bank_alerts.large_debits()
    assert list(big["debit"]) == [500000.0]                  # 12,345 is ordinary; 25,000 is matched to the loan
    resolve(int(big["txn_id"].iloc[0]), "Expected bank debit", "loan pre-payment")
    assert bank_alerts.large_debits().empty

    today = date(2026, 10, 9)
    assert bank_alerts.low_cash_ahead(today) is None         # no buffer set
    set_buffer(10_000_000)
    low = bank_alerts.low_cash_ahead(today)
    assert low and low["day"] == today
    assert bank_alerts.low_cash_ahead(date(2026, 11, 30)) is None            # bank balance too old to forecast from
    keys = [a["key"] for a in bank_alerts.all_alerts(today)]
    assert keys[:2] == ["low_cash", "stale_deposit"]
    assert bank_alerts.statement_age(date.today()) is None
    assert bank_alerts.statement_age(date.today() + timedelta(days=8))["age"] == 8


def test_no_alerts_before_anything_is_imported(db):
    assert bank_alerts.all_alerts() == [] and bank_alerts.large_debits().empty


def test_smarter_suggestions_confidence_and_series(db, monkeypatch, tmp_path):
    """Owner, 2026-09-28 ("make suggestions more intelligent"): the real case -- Battery Depot leaves ...674-...678 at PHP 62,637 each are
    ours; the bank paid leaf ...673 on Sep 21. Our ...677 (Sep 25) and ...678 (Sep 29) are dated AFTER that payment -> Low; the app says
    ...673 is probably an unrecorded Battery Depot check, and one click tags it (no check created)."""
    monkeypatch.setenv("AP_DRIVE_DIR", str(tmp_path / "drive"))
    bd = make_creditor(db, name="Battery Depot")
    sid = make_statement(db, bd)
    app.add_invoice(bd, "BD-1", date(2026, 9, 1), date(2026, 10, 1), 400000.0, [], sid)
    ids = {}
    for leaf, d in ((674, "2026-09-20"), (675, "2026-09-22"), (676, "2026-09-24"), (677, "2026-09-25"), (678, "2026-09-29")):
        _, _, cid = app.issue_check(bd, 62637.0, date.fromisoformat(d), statement_id=sid)
        ids[leaf] = cid
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE checks_issued SET check_number = ? WHERE check_id = ?", (f"3992554{leaf}", cid))
    import_statement(_csv([("09/21/2026", "3992554673", "IM IN- CLRNG- BATCH", 62637.0, 0.0),
                           ("09/22/2026", "3992554674", "IM IN- CLRNG- BATCH", 62637.0, 0.0)]), "s.csv")
    sugg = {s["our_number"]: s for s in suggestions()}
    assert sugg["3992554678"]["confidence"] == "Low" and "AFTER the bank paid it" in sugg["3992554678"]["reasons"][-1]
    assert sugg["3992554677"]["confidence"] == "Low"
    [hint] = series_hints()
    assert hint["payee"] == "Battery Depot" and hint["bank_check_number"] == "3992554673" and len(hint["neighbours"]) >= 2
    tag_series(hint["txn_id"], hint["payee"])
    line = _line(db, check_number="3992554673")
    assert (line["reviewed"], line["resolution"]) == (1, "Not recorded in the system") and "Battery Depot" in line["note"]
    assert suggestions() == [] and series_hints() == []
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM checks_issued").fetchone()[0] == 5          # nothing created


def test_a_well_fitting_suggestion_is_high(db, monkeypatch, tmp_path):
    ids = _setup(db, monkeypatch, tmp_path)
    t = next(s for s in suggestions() if s["check_id"] == ids["T"])          # our T dated Oct 3, bank paid Oct 6, only candidate
    assert t["confidence"] == "High" and "paid 3 day(s) after its date" in t["reasons"]


def test_a_card_debit_of_exactly_the_statement_total_marks_that_card_paid_on_the_bank_date(db, monkeypatch, tmp_path):
    """The bank statement is the truth (2026-10-03): the card is Paid because the bank debited it, on the day the bank posted it."""
    ids = _setup(db, monkeypatch, tmp_path)
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT status, payment_method, payment_date, amount_paid FROM cc_statements WHERE cc_statement_id = ?", (ids["cc"],)).fetchone()
        audit = conn.execute("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CC_STATEMENT_PAID_FROM_BANK'").fetchone()[0]
    assert row == ("Paid", "Direct Debit", "2026-10-08", 8000.0) and audit == 1


def test_a_loan_installment_with_no_bank_debit_is_a_data_health_finding_but_a_matched_one_is_not(db, monkeypatch, tmp_path):
    from engines import data_health_service as dh
    ids = _setup(db, monkeypatch, tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO bank_loan_installments (bank_loan_id, installment_no, due_date, amount) VALUES (1, 2, '2026-10-01', 31000)")
    flagged = [f for f in dh.findings(today=date(2026, 10, 9)) if f["check"] == "Bank loan installment with no bank debit"]
    assert len(flagged) == 1 and "31,000.00" in flagged[0]["detail"]                 # the Oct 1 installment has no debit; the Oct 3 one (25,000) does



def test_checks_only_by_default_card_and_loan_debits_are_left_to_tag(db, monkeypatch, tmp_path):
    """Owner, 2026-10-17: "checks only" -- "since it is confirmed thru check number". The bank statement clears our checks; a card statement is never
    marked paid from it, a loan installment never cleared by it: those debits are left for the owner to tag."""
    import sqlite3
    ids = _setup(db, monkeypatch, tmp_path, match_other=False)
    loan, card = _line(db, debit=25000.0), _line(db, debit=8000.0)
    assert loan["match_status"] == "unmatched_debit" and loan["matched_installment_id"] is None
    assert card["match_status"] == "unmatched_debit" and card["matched_cc_statement_id"] is None
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT status FROM cc_statements WHERE cc_statement_id = ?", (ids["cc"],)).fetchone()[0] != "Paid"
