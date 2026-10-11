"""Expenses / petty cash and the month's Income statement (owner, 2026-10-11: "just focus on income statement" -- no GL, nothing to maintain):
an expense is one line with its category, taken back only with a reason; the owner edits the category list; the Deposits page subtracts the
drawer's expenses; the statement computes the month's profit from the documents; both pages hide behind the books switch."""
from datetime import timedelta

from conftest import fetchone

from tests.test_web_collect import _charged, _collect
from tests.test_web_d0 import _admin, _sign_in_new
from tests.test_web_sell import _ready, _sale


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _n(sql, params=()):
    return fetchone(_db(), sql, params)[0]


def _spend(web, amount="2,350", cat_name="Electricity", detail="Meralco Sept", source="Cash (drawer)", day=None):
    from engines.clock import manila_today
    cat = _n("SELECT category_id FROM expense_categories WHERE name = ?", (cat_name,))
    return web.post("/expenses/", {"action": "add", "expense_date": day or manila_today().isoformat(), "category_id": cat,
                                   "paid_from": source, "detail": detail, "amount": amount})


def test_an_expense_is_one_line_and_taken_back_only_with_a_reason(web, db):
    _admin(web)
    r = _spend(web)
    assert r.status_code == 302
    page = web.get("/expenses/").content.decode()
    assert "EXP-0001" in page and "Electricity" in page and "2,350.00" in page and "Meralco Sept" in page
    eid = _n("SELECT expense_id FROM expenses")
    web.post("/expenses/", {"action": "void", "expense_id": eid, "reason": ""})
    assert _n("SELECT voided_at FROM expenses") is None                          # no reason, no take-back
    web.post("/expenses/", {"action": "void", "expense_id": eid, "reason": "typed twice"})
    assert _n("SELECT voided_at FROM expenses") is not None
    assert _n("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'EXPENSE_VOIDED'") == 1


def test_the_category_list_is_the_owners(web, db):
    _admin(web)
    seller = _sign_in_new("sales@slicks.test", "sales")
    assert _spend(seller, amount="180", cat_name="Meals", detail="merienda").status_code == 302      # typing an expense: anyone with the switch
    r = seller.post("/expenses/", {"action": "category", "name": "Sari-sari"})
    assert _n("SELECT COUNT(*) FROM expense_categories WHERE name = 'Sari-sari'") == 0               # the list is the admin's
    web.post("/expenses/", {"action": "category", "name": "Sari-sari"})
    assert _n("SELECT COUNT(*) FROM expense_categories WHERE name = 'Sari-sari'") == 1


def test_the_deposits_page_subtracts_the_drawer(web, db):
    _admin(web)
    a, _ = _ready(web)
    _sale(web, a, si="004501", qty="1", price="2500", pay_method_0="CASH", pay_amount_0="2500")
    _spend(web, amount="600", cat_name="Meals", detail="lunch")
    page = web.get("/collect/?view=deposits").content.decode()
    assert "2,500.00" in page and "(600.00)" in page                              # taken, and spent from the drawer
    from engines.cash_in import deposit_days
    row = deposit_days()[0]
    assert row["cash"] == 2500.0 and row["spent"] == 600.0 and row["diff"] == -1900.0      # nothing on the statement yet


def test_the_income_statement_adds_the_month_up(web, db):
    from engines.clock import manila_today
    from engines.income_statement import month_report
    _admin(web)
    cust, sid = _charged(web, db)                                                 # 2 pcs x 5,000 on charge; the test item's cost is 2,840 each
    _spend(web, amount="2,350", cat_name="Electricity")
    _spend(web, amount="600", cat_name="Meals")
    m = manila_today().strftime("%Y-%m")
    r = month_report(m)
    assert r["sales"] == 10000.0 and r["cogs"] == 5680.0 and r["gross"] == 4320.0
    assert dict(r["expenses"]) == {"Electricity": 2350.0, "Meals": 600.0} and r["expenses_total"] == 2950.0
    assert r["profit"] == 1370.0
    page = web.get("/income-statement/").content.decode()
    assert "Income statement" in page and "10,000.00" in page and "(5,680.00)" in page and "(2,950.00)" in page and "1,370.00" in page
    assert web.get("/income-statement/?dl=xlsx")["Content-Disposition"].startswith("attachment")


def test_a_return_and_a_bank_loan_interest_land_in_the_right_month(web, db):
    from engines.clock import manila_today
    from engines.income_statement import month_report
    _admin(web)
    cust, sid = _charged(web, db)
    lid = _n("SELECT line_id FROM sale_lines WHERE sale_id = ?", (sid,))
    web.post(f"/sales/{sid}/", {"action": "return", "line_id": lid, "qty": "1", "reason": "wrong size"})
    from conftest import make_creditor
    bank = make_creditor(db, "Philippine National Bank")
    from engines.common import get_conn
    with get_conn() as conn:                                                       # a bank-loan installment with its typed interest, ticked paid today
        bl = conn.execute("INSERT INTO bank_loans (creditor_id, loan_ref, principal_amount) VALUES (?, 'T-001', 100000) RETURNING bank_loan_id",
                          (bank,)).fetchone()[0]
        conn.execute("""INSERT INTO bank_loan_installments (bank_loan_id, installment_no, due_date, amount, interest_portion, cleared_at)
                        VALUES (?, 1, ?, 11500, 1500, ?)""", (bl, str(manila_today()), str(manila_today())))
    r = month_report(manila_today().strftime("%Y-%m"))
    assert r["sales"] == 5000.0                                                    # 10,000 less the 5,000 returned
    assert r["cogs"] == 2840.0                                                     # its cost went back too
    assert r["bankloan_interest"] == 1500.0
    assert r["profit"] == 5000.0 - 2840.0 - 1500.0


def test_the_books_hide_behind_their_switch(web, db, monkeypatch):
    _admin(web)
    monkeypatch.setenv("WEB_BOOKS", "0")
    assert web.get("/expenses/").status_code == 403 and web.get("/income-statement/").status_code == 403
    home = web.get("/").content.decode()
    assert 'href="/expenses/"' not in home and 'href="/income-statement/"' not in home
    monkeypatch.setenv("WEB_BOOKS", "1")
    assert web.get("/expenses/").status_code == 200 and web.get("/income-statement/").status_code == 200


def test_lender_interest_counts_when_its_check_clears_by_the_systems_own_rule(web, db):
    # found 2026-10-12: a cleared check keeps status 'Active' and carries a clearing mark (engines/clearing.py) -- there is no 'Cleared' status,
    # so the statement must read the marks, in the month the check cleared
    from conftest import make_creditor
    from engines.clock import manila_today
    from engines.common import get_conn
    from engines.income_statement import month_report
    _admin(web)
    lender = make_creditor(db, "Neth Canlas")
    today = manila_today()
    with get_conn() as conn:
        note = conn.execute("""INSERT INTO loan_notes (creditor_id, note_ref, issue_date, principal_amount, total_payable, status)
                               VALUES (?, 'LN-T1', ?, 100000, 103000, 'Active') RETURNING loan_note_id""", (lender, str(today))).fetchone()[0]
        ids = []
        for no, mark in ((1, "manual_cleared_at"), (2, None)):                    # one ticked cleared, one still waiting for the bank
            chk = conn.execute(f"""INSERT INTO checks_issued (creditor_id, bank_account_id, loan_note_id, check_number, check_date, amount, status
                                   {', ' + mark if mark else ''}) VALUES (?, 1, ?, ?, ?, 11500, 'Active' {', ?' if mark else ''}) RETURNING check_id""",
                               (lender, note, f"39925700{no:02d}", str(today), *([str(today) + " 09:00:00"] if mark else []))).fetchone()[0]
            conn.execute("""INSERT INTO loan_installments (loan_note_id, installment_no, due_date, principal, interest, outstanding_balance, status, check_id)
                            VALUES (?, ?, ?, 10000, 1500, 90000, 'Active', ?)""", (note, no, str(today), chk))
    r = month_report(today.strftime("%Y-%m"))
    assert r["lender_interest"] == 1500.0                                         # only the cleared one; the waiting one is not paid yet
