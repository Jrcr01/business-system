"""The clearing rule (owner, 2026-10-08): a check is paid only when it is cleared -- by the bank (an imported statement), settled outside the bank, or ticked
cleared by a person -- never by its date alone. A tick is confirmed by the bank statement; a statement covering the tick's date that does not show the check
puts a line on the Review list. Once, at go-live, the checks from before the first bank statement are marked "cleared before go-live"."""
import sqlite3
from datetime import date, timedelta

import app
from conftest import fetchone, make_creditor, make_statement
from engines import clearing
from engines.clock import manila_today

TODAY = manila_today()


def _issued(db, number="0004471", day=None, amount=1000.0):
    sup = make_creditor(db, name="Goodyear Philippines")
    sid = make_statement(db, sup)
    app.add_invoice(sup, f"INV-{number}", TODAY - timedelta(days=40), TODAY - timedelta(days=10), amount, [], sid)
    _, _, cid = app.issue_check(sup, amount, day or TODAY - timedelta(days=5), statement_id=sid)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE checks_issued SET check_number = ? WHERE check_id = ?", (number, cid))
    return sid, cid


def _bank_lines(db, *days):
    """An imported bank statement with one deposit line on each day (covering those dates)."""
    with sqlite3.connect(db) as conn:
        imp = conn.execute("INSERT INTO bank_statement_imports (bank_account_id, file_name, file_hash) VALUES (1, 's.csv', 'h')").lastrowid
        for n, d in enumerate(days):
            conn.execute("INSERT INTO bank_transactions (import_id, txn_date, posting_date, description, debit, credit, kind, line_key) VALUES (?, ?, ?, 'x', 0, 10, 'DEPOSIT', ?)",
                         (imp, str(d), str(d), f"k{n}"))


def _status(db, sid):
    return fetchone(db, "SELECT status FROM invoices WHERE statement_id = ?", (sid,))[0]


def test_a_past_dated_check_stays_owed_until_it_is_ticked_and_the_tick_can_be_undone(db):
    sid, cid = _issued(db)
    assert _status(db, sid) == "Unpaid"
    assert list(clearing.waiting_for_bank()["check_id"]) == [cid]
    ok, msg = clearing.mark_cleared(cid, TODAY - timedelta(days=1), "seen in online banking")
    assert ok, msg
    assert _status(db, sid) == "Paid" and bool(clearing.waiting_for_bank()["ticked"].iloc[0])
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CHECK_MARKED_CLEARED'")[0] == 1
    assert clearing.unmark_cleared(cid)[0] and _status(db, sid) == "Unpaid"


def test_a_tick_is_refused_for_a_future_date_a_check_without_number_or_one_the_bank_cleared(db):
    sid, cid = _issued(db)
    assert not clearing.mark_cleared(cid, TODAY + timedelta(days=1))[0]
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE checks_issued SET check_number = NULL WHERE check_id = ?", (cid,))
    assert "no number" in clearing.mark_cleared(cid)[1]
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE checks_issued SET check_number = '0004471', bank_cleared_date = ? WHERE check_id = ?", (str(TODAY), cid))
    assert "already cleared" in clearing.mark_cleared(cid)[1]


def test_a_post_dated_check_cannot_be_ticked_cleared_before_its_date(db):
    # owner, 2026-10-09: "future checks cannot be mark cleared"
    sid, cid = _issued(db, day=TODAY + timedelta(days=3))
    ok, msg = clearing.mark_cleared(cid)
    assert not ok and "can't clear before its date" in msg and _status(db, sid) == "Unpaid"
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE checks_issued SET check_date = ? WHERE check_id = ?", (str(TODAY - timedelta(days=3)), cid))
    assert not clearing.mark_cleared(cid, TODAY - timedelta(days=4))[0]        # cleared on a day before the check's own date
    assert clearing.mark_cleared(cid, TODAY - timedelta(days=3))[0]


def test_the_bank_statement_confirms_or_flags_a_tick(db):
    sid, cid = _issued(db)
    clearing.mark_cleared(cid, TODAY - timedelta(days=2))
    assert clearing.check_ticks_against_bank() == 0                         # no statement yet: nothing to compare
    _bank_lines(db, TODAY - timedelta(days=4), TODAY)                       # a statement covering the tick date, without this check
    assert clearing.check_ticks_against_bank() == 1
    assert fetchone(db, "SELECT kind FROM review_flags WHERE ref_id = ?", (cid,))[0] == "CLEARED_NOT_ON_BANK"


def test_the_cutoff_marks_checks_before_the_first_statement_once(db):
    sid_old, old = _issued(db, "0001000", TODAY - timedelta(days=90))
    sup = fetchone(db, "SELECT creditor_id FROM order_statements WHERE statement_id = ?", (sid_old,))[0]
    with sqlite3.connect(db) as conn:                       # a second check, dated inside the statement period
        new = conn.execute("INSERT INTO checks_issued (bank_account_id, creditor_id, statement_id, check_number, check_date, amount, status) VALUES (1, ?, ?, '0002000', ?, 500, 'Active')",
                           (sup, sid_old, str(TODAY - timedelta(days=5)))).lastrowid
    _bank_lines(db, TODAY - timedelta(days=30))
    prev = clearing.cutoff_preview()
    assert prev["first_day"] == TODAY - timedelta(days=30) and list(prev["before"]["check_id"]) == [old] and list(prev["inside"]["check_id"]) == [new]
    ok, msg = clearing.apply_cutoff()
    assert ok, msg
    assert fetchone(db, "SELECT manual_cleared_note, manual_cleared_at FROM checks_issued WHERE check_id = ?", (old,)) == (clearing.CUTOFF_NOTE, str(TODAY - timedelta(days=90)))
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CLEARING_CUTOFF'")[0] == 1
    assert fetchone(db, "SELECT manual_cleared_at FROM checks_issued WHERE check_id = ?", (new,))[0] is None      # inside the statement period: left for the owner
    assert clearing.check_ticks_against_bank() == 0                                                                 # a cut-off mark is not a tick to confirm


def _loan_due(db, day, amount=20000.0):
    from engines.bank_loan_service import add_bank_loan_installment, create_bank_loan
    from conftest import make_creditor as mk
    bank = mk(db, name="BDO", creditor_type="BANK_LOAN")
    blid = create_bank_loan(bank, "L-9", "A", date(2026, 1, 1), date(2028, 1, 1), None, 500000.0, "ADA")
    add_bank_loan_installment(blid, day, amount)
    return fetchone(db, "SELECT installment_id FROM bank_loan_installments WHERE bank_loan_id = ?", (blid,))[0]


def _owed_tracks():
    from engines.reports_service import get_combined_ap_summary
    df = get_combined_ap_summary()
    return {str(r["track"]): float(r["outstanding"]) for _, r in df.iterrows()}


def test_a_bank_loan_payment_stays_owed_past_its_date_until_ticked_or_on_the_bank(db):
    # daily payables, owner 2026-10-09: "until the check is not cleared it will still be considered payable"
    iid = _loan_due(db, TODAY - timedelta(days=4))
    assert _owed_tracks()["Bank Loan"] == 20000.0
    assert not clearing.mark_loan_cleared(iid, TODAY - timedelta(days=5))[0]           # before its due date
    ok, msg = clearing.mark_loan_cleared(iid, TODAY, "auto-debit seen")
    assert ok, msg
    assert _owed_tracks()["Bank Loan"] == 0.0
    assert clearing.unmark_loan_cleared(iid)[0] and _owed_tracks()["Bank Loan"] == 20000.0
    _bank_lines(db, TODAY)
    with sqlite3.connect(db) as conn:                                                  # the bank statement shows the debit
        conn.execute("UPDATE bank_transactions SET matched_installment_id = ? WHERE line_key = 'k0'", (iid,))
    assert _owed_tracks()["Bank Loan"] == 0.0


def test_a_card_statement_stays_owed_past_its_due_date_until_its_payment_is_recorded(db):
    from conftest import make_creditor as mk
    issuer = mk(db, name="RCBC", creditor_type="CREDIT_CARD")
    with sqlite3.connect(db) as conn:
        card = conn.execute("INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit) VALUES (?, 'Gold', '1234', 1)", (issuer,)).lastrowid
        csid = conn.execute("INSERT INTO cc_statements (card_id, due_date, total_amount_due, status) VALUES (?, ?, 3000, 'Unpaid')",
                            (card, str(TODAY - timedelta(days=3)))).lastrowid
    assert _owed_tracks()["Credit Card"] == 3000.0
    ok, msg = clearing.pay_card(csid, TODAY)
    assert ok, msg
    assert fetchone(db, "SELECT status FROM cc_statements WHERE cc_statement_id = ?", (csid,))[0] == "Paid" and _owed_tracks()["Credit Card"] == 0.0


def test_the_cutoff_also_marks_bank_loan_payments_before_the_first_statement(db):
    old = _loan_due(db, TODAY - timedelta(days=40))
    _bank_lines(db, TODAY - timedelta(days=20), TODAY - timedelta(days=1))
    assert len(clearing.cutoff_preview()["loans_before"]) == 1
    ok, msg = clearing.apply_cutoff()
    assert ok, msg
    assert fetchone(db, "SELECT cleared_note FROM bank_loan_installments WHERE installment_id = ?", (old,))[0] == clearing.CUTOFF_NOTE


def test_the_next_soa_is_the_final_word_on_card_payments_recorded_daily(db):
    # owner, 2026-10-09: payments to a card are confirmed by the SOA (the owner's way); co-owners record them day to day; a difference is flagged
    from conftest import make_creditor as mk
    from engines.card_rules import confirm_cc_payment_from_next_statement
    issuer = mk(db, name="RCBC", creditor_type="CREDIT_CARD")
    with sqlite3.connect(db) as conn:
        card = conn.execute("INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit) VALUES (?, 'Gold', '1234', 1)", (issuer,)).lastrowid
        a = conn.execute("INSERT INTO cc_statements (card_id, due_date, total_amount_due, status, paid_confirmed) VALUES (?, ?, 3000, 'Unpaid', 0)",
                         (card, str(TODAY - timedelta(days=3)))).lastrowid
        b = conn.execute("INSERT INTO cc_statements (card_id, due_date, total_amount_due, status, paid_confirmed) VALUES (?, ?, 3000, 'Unpaid', 0)",
                         (card, str(TODAY - timedelta(days=2)))).lastrowid
    assert clearing.pay_card(a, TODAY, amount=3000)[0] and clearing.pay_card(b, TODAY, amount=1000)[0]
    assert confirm_cc_payment_from_next_statement(a, 0.0) == (3000.0, "Paid")                 # the SOA agrees: nothing to look at
    assert fetchone(db, "SELECT COUNT(*) FROM review_flags WHERE kind = 'CC_PAYMENT_MISMATCH'")[0] == 0
    assert confirm_cc_payment_from_next_statement(b, 500.0) == (2500.0, "Partially Paid")      # the SOA shows 2,500 paid, 1,000 was recorded
    assert fetchone(db, "SELECT COUNT(*) FROM review_flags WHERE kind = 'CC_PAYMENT_MISMATCH' AND ref_id = ?", (b,))[0] == 1
    assert fetchone(db, "SELECT amount_paid FROM cc_statements WHERE cc_statement_id = ?", (b,))[0] == 2500.0   # the SOA figure is kept


def test_after_the_soa_taking_back_a_daily_record_keeps_the_soa_figures_and_reports_count_the_soa(db):
    # owner, 2026-10-09: "what if my brother mark the credit card paid, but i corrected it thru entering SOA" -- the SOA stays final
    from conftest import make_creditor as mk
    from engines import account_statement
    from engines.card_rules import confirm_cc_payment_from_next_statement
    from engines.overview_report_service import _cash_rows
    issuer = mk(db, name="PNB", creditor_type="CREDIT_CARD")
    due = TODAY - timedelta(days=2)
    with sqlite3.connect(db) as conn:
        card = conn.execute("INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit) VALUES (?, 'Mastercard', '6819', 1)", (issuer,)).lastrowid
        sid = conn.execute("INSERT INTO cc_statements (card_id, due_date, total_amount_due, status, paid_confirmed) VALUES (?, ?, 733600, 'Unpaid', 0)",
                           (card, str(due))).lastrowid
    assert clearing.pay_card(sid, TODAY)[0]                                                    # the brother: all 733,600 paid
    assert confirm_cc_payment_from_next_statement(sid, 233600.0) == (500000.0, "Partially Paid")   # the SOA: only 500,000
    assert fetchone(db, "SELECT soa_confirmed_at IS NOT NULL FROM cc_statements WHERE cc_statement_id = ?", (sid,))[0] == 1
    with app.get_conn() as conn:
        cash = _cash_rows(conn, due, TODAY)
    assert cash[cash["track"] == "Credit Card"]["paid"].sum() == 500000.0                      # reports: the SOA's amount, not the daily record
    soa_report = account_statement.ledger("card", card)
    assert soa_report["payments"] == 500000.0 and soa_report["closing"] == 233600.0              # the statement of account: the SOA's figure too
    pid = fetchone(db, "SELECT payment_id FROM cc_payments WHERE cc_statement_id = ?", (sid,))[0]
    ok, msg = clearing.unpay_card(pid)
    assert ok and "keeps what the SOA shows" in msg
    row = fetchone(db, "SELECT amount_paid, unpaid_balance_next, status FROM cc_statements WHERE cc_statement_id = ?", (sid,))
    assert row == (500000.0, 233600.0, "Partially Paid")                                       # untouched by the undo


def test_before_the_soa_taking_back_a_daily_record_makes_it_owed_again(db):
    from conftest import make_creditor as mk
    issuer = mk(db, name="RCBC", creditor_type="CREDIT_CARD")
    with sqlite3.connect(db) as conn:
        card = conn.execute("INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit) VALUES (?, 'Gold', '5003', 1)", (issuer,)).lastrowid
        sid = conn.execute("INSERT INTO cc_statements (card_id, due_date, total_amount_due, status, paid_confirmed) VALUES (?, ?, 3000, 'Unpaid', 0)",
                           (card, str(TODAY - timedelta(days=1)))).lastrowid
    assert clearing.pay_card(sid, TODAY, amount=1000)[0]
    pid = fetchone(db, "SELECT payment_id FROM cc_payments WHERE cc_statement_id = ?", (sid,))[0]
    ok, msg = clearing.unpay_card(pid)
    assert ok and "owed again" in msg
    assert fetchone(db, "SELECT amount_paid, status FROM cc_statements WHERE cc_statement_id = ?", (sid,)) == (0.0, "Unpaid")


def test_a_statement_with_a_recorded_payment_is_not_deleted_and_says_why(db):
    # owner, 2026-10-10: "error occurred when i delete SOA" -- a Server Error; the daily payment still pointed at the statement
    from conftest import make_creditor as mk
    from engines.card_rules import delete_cc_statement
    issuer = mk(db, name="RCBC", creditor_type="CREDIT_CARD")
    with sqlite3.connect(db) as conn:
        card = conn.execute("INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit) VALUES (?, 'World', '1240', 1)", (issuer,)).lastrowid
        sid = conn.execute("INSERT INTO cc_statements (card_id, due_date, total_amount_due, status, paid_confirmed) VALUES (?, ?, 506400, 'Unpaid', 0)",
                           (card, str(TODAY - timedelta(days=5)))).lastrowid
    assert clearing.pay_card(sid, TODAY)[0]
    ok, msg = delete_cc_statement(sid)
    assert not ok and "Take the payment back first" in msg
    assert fetchone(db, "SELECT COUNT(*) FROM cc_statements WHERE cc_statement_id = ?", (sid,))[0] == 1
    pid = fetchone(db, "SELECT payment_id FROM cc_payments WHERE cc_statement_id = ?", (sid,))[0]
    assert clearing.unpay_card(pid)[0]
    assert delete_cc_statement(sid) == (True, "Statement deleted.")
