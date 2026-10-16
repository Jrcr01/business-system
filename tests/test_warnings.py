"""Analytics -> Warnings (owner, 2026-10-15: "Next warning tab"): one list, most urgent first, each with a why and where to act; a warning is shown only
to a person who may open the page it points to; the limits are settings."""
import sqlite3
from datetime import timedelta

from conftest import make_creditor
from tests.test_web_d0 import _admin


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _titles(email=None):
    from engines.warn_list import all_for
    return [w["title"] for w in all_for(email)]


def test_each_warning_fires_on_its_case_and_the_tab_lists_them(web, db):
    from engines.acting_user import acting_as
    from engines.check_rules import issue_check
    from engines.clock import manila_today
    from engines.loan_schedule_service import create_note_with_payments
    _admin(web)
    today = manila_today()
    assert not any("check" in t for t in _titles())
    lender = make_creditor(db, "Luz", creditor_type="LENDER")                    # a check dated 45 days ago, never cleared
    old = today - timedelta(days=45)
    with acting_as("admin@slicks.test", "admin"):
        ok, msg, lnid = create_note_with_payments(lender, old - timedelta(days=10), 50000.0, 0.0, [(old, 50000.0)])
        assert ok, msg
        inst = sqlite3.connect(_db()).execute("SELECT installment_id FROM loan_installments WHERE loan_note_id = ?", (lnid,)).fetchone()[0]
        ok, msg, _ = issue_check(lender, 50000.0, old, loan_note_id=lnid, installment_id=inst)
        assert ok, msg
    assert any(t.startswith("1 check dated over 30 days ago, not cleared (₱50,000)") for t in _titles())
    sup = make_creditor(db, "Sunny Tire Supply")                                   # a price 10% up within the last 30 days
    bank = make_creditor(db, "BPI Cards", creditor_type="CREDIT_CARD")
    with sqlite3.connect(_db()) as conn:
        conn.execute("INSERT INTO supplier_price_list (creditor_id, item_name, unit_price, effective_date) VALUES (?, '185/65R15 XYZ', 2000, ?)", (sup, str(today - timedelta(days=200))))
        conn.execute("INSERT INTO supplier_price_list (creditor_id, item_name, unit_price, effective_date) VALUES (?, '185/65R15 XYZ', 2200, ?)", (sup, str(today - timedelta(days=3))))
        conn.execute("INSERT INTO supplier_price_list (creditor_id, item_name, unit_price, effective_date) VALUES (?, '195/65R15 XYZ', 2000, ?)", (sup, str(today - timedelta(days=200))))
        conn.execute("INSERT INTO supplier_price_list (creditor_id, item_name, unit_price, effective_date) VALUES (?, '195/65R15 XYZ', 2040, ?)", (sup, str(today - timedelta(days=3))))   # 2%: under the limit
        card = conn.execute("INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit, statement_day) VALUES (?, 'Amore', '4411', 300000, ?)",
                            (bank, today.day)).lastrowid                       # this month's statement, with a finance charge
        conn.execute("INSERT INTO cc_statements (card_id, due_date, total_amount_due, interest_charged) VALUES (?, ?, 50000, 1234.5)", (card, str(today + timedelta(days=20))))
    titles = _titles()
    assert "Sunny Tire Supply: prices up 10.0% on 1 item" in titles
    assert any(t == "₱1,234 in card finance charges this month" for t in titles)
    from engines.warn_list import all_for
    levels = [w["level"] for w in all_for()]
    assert levels == sorted(levels, key=lambda x: {"red": 0, "amber": 1, "blue": 2}[x])        # most urgent first
    assert "tab=warnings" not in web.get("/").content.decode()                   # released hidden (owner, 2026-10-16): the switch shows it
    from engines import settings_registry as sr
    with acting_as("admin@slicks.test", "admin"):
        sr.save("warn_tab_on", "1")
    page = web.get("/analytics/?tab=warnings").content.decode()
    assert "could not be drawn" not in page and "prices up 10.0%" in page and 'href="/checks/?tab=waiting"' in page
    assert "tab=warnings" in web.get("/").content.decode()                       # its Home card and menu entry
    from engines import settings_registry as sr
    with acting_as("admin@slicks.test", "admin"):
        sr.save("warn_price_rise_pct", "15")                                    # the limit is a setting
    assert not any("prices up" in t for t in _titles())


def test_a_warning_is_shown_only_to_a_person_who_may_open_its_page(web, db):
    from engines.acting_user import acting_as
    from engines.clock import manila_today
    from tests.test_web_d0 import _sign_in_new, fetchone_uid
    _admin(web)
    today = manila_today()
    sup = make_creditor(db, "Sunny Tire Supply")
    with sqlite3.connect(_db()) as conn:
        conn.execute("INSERT INTO supplier_price_list (creditor_id, item_name, unit_price, effective_date) VALUES (?, 'A', 1000, ?)", (sup, str(today - timedelta(days=200))))
        conn.execute("INSERT INTO supplier_price_list (creditor_id, item_name, unit_price, effective_date) VALUES (?, 'A', 1200, ?)", (sup, str(today - timedelta(days=2))))
    _sign_in_new("family@slicks.test", "owner")
    assert any("prices up" in t for t in _titles("family@slicks.test"))
    with sqlite3.connect(_db()) as conn:
        conn.execute("INSERT INTO user_hidden_pages (user_id, page_id) VALUES (?, 'price-lists')", (fetchone_uid("family@slicks.test"),))
    assert not any("prices up" in t for t in _titles("family@slicks.test"))
    assert any("prices up" in t for t in _titles("admin@slicks.test"))           # the System admin sees everything
