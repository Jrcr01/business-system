"""Other income (owner, 2026-10-13 "i want to add other income"; go 2026-10-15): one line each with its kind, taken back only with a reason; the kinds
are the owner's; the Income statement adds it to the profit; the Deposits drawer is untouched; recorded by whoever records Expenses."""
import sqlite3

from conftest import fetchone

from tests.test_web_d0 import _admin, _sign_in_new, fetchone_uid


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _n(sql, params=()):
    return fetchone(_db(), sql, params)[0]


def _receive(web, amount="15,000", kind="Rent received", detail="Stall 2 rent, October", where="Cash (drawer)", day=None):
    from engines.clock import manila_today
    kid = _n("SELECT kind_id FROM income_kinds WHERE name = ?", (kind,))
    return web.post("/other-income/", {"action": "add", "income_date": day or manila_today().isoformat(), "kind_id": kid,
                                       "received_in": where, "detail": detail, "amount": amount})


def test_a_line_is_saved_numbered_and_taken_back_only_with_a_reason(web, db):
    from datetime import timedelta
    from engines.clock import manila_today
    _admin(web)
    assert _receive(web).status_code == 302
    page = web.get("/other-income/").content.decode()
    assert "OI-0001" in page and "Rent received" in page and "15,000.00" in page and "Stall 2 rent, October" in page
    _receive(web, amount="0")
    _receive(web, day=(manila_today() + timedelta(days=1)).isoformat())
    web.post("/other-income/", {"action": "add", "income_date": manila_today().isoformat(), "kind_id": "", "amount": "500"})
    assert _n("SELECT COUNT(*) FROM other_income") == 1                          # zero, a future date, no kind: refused
    iid = _n("SELECT income_id FROM other_income")
    web.post("/other-income/", {"action": "void", "income_id": iid, "reason": ""})
    assert _n("SELECT voided_at FROM other_income") is None
    web.post("/other-income/", {"action": "void", "income_id": iid, "reason": "typed twice"})
    assert _n("SELECT voided_at FROM other_income") is not None and _n("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'OTHER_INCOME_VOIDED'") == 1


def test_the_income_statement_adds_it_and_the_drawer_is_untouched(web, db):
    from engines.cash_in import deposit_days
    from engines.clock import manila_today
    from engines.income_statement import month_report
    _admin(web)
    before = month_report(manila_today().strftime("%Y-%m"))["profit"]
    _receive(web, amount="15,000")
    _receive(web, amount="4,200", kind="Supplier rebates and incentives", detail="Sunny Q3 rebate", where="GCash")
    r = month_report(manila_today().strftime("%Y-%m"))
    assert dict(r["other_income"]) == {"Rent received": 15000.0, "Supplier rebates and incentives": 4200.0} and r["other_income_total"] == 19200.0
    assert r["profit"] == round(before + 19200.0, 2)
    page = web.get("/income-statement/").content.decode()
    assert "Other income" in page and "19,200.00" in page and "Supplier rebates and incentives" in page
    assert web.get("/income-statement/?dl=xlsx")["Content-Disposition"].startswith("attachment")
    assert all(d["cash"] == 0 for d in deposit_days())                          # not counted in the drawer on Deposits


def test_the_kinds_are_the_owners_and_hidden_means_no_page(web, db):
    _admin(web)
    seller = _sign_in_new("sales@slicks.test", "sales")
    assert _receive(seller, amount="500", kind="Others", detail="scrap tires sold").status_code == 302     # anyone who records expenses
    seller.post("/other-income/", {"action": "kind", "name": "Commissions"})
    assert _n("SELECT COUNT(*) FROM income_kinds WHERE name = 'Commissions'") == 0                       # the list is the admin's
    web.post("/other-income/", {"action": "kind", "name": "Commissions"})
    assert _n("SELECT COUNT(*) FROM income_kinds WHERE name = 'Commissions'") == 1
    assert "Commissions" in web.get("/other-income/").content.decode()
    with sqlite3.connect(_db()) as conn:
        conn.execute("INSERT INTO user_hidden_pages (user_id, page_id) VALUES (?, 'other-income')", (fetchone_uid("sales@slicks.test"),))
    assert seller.get("/other-income/").status_code in (302, 403)
