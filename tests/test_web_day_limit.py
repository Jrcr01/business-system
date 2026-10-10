"""The daily payment rule (owner, 2026-10-09: "no AP above 350,000 to 400,000 per day"; over it: confirm only, flagged for review): a target
(₱350,000, amber) and a ceiling (₱400,000, red; confirm anyway). Everything that goes out counts (owner, 2026-10-10), and a card's month with no SOA
yet carries a reserve (owner, 2026-10-09: the SOA "is given only 1 month before")."""
import sqlite3
from datetime import date, timedelta

from conftest import fetchone, make_creditor
from tests.test_web_d0 import _admin
from tests.test_web_d3a import _add_inv, _new_batch


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _bank_day(days_ahead: int) -> date:
    from engines.clock import manila_today
    d = manila_today() + timedelta(days=days_ahead)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _check_on(db, cid, day: date, amount: float):
    import app
    sid = app.create_order_statement(cid)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO checks_issued (bank_account_id, creditor_id, statement_id, amount, check_date, status) VALUES (1, ?, ?, ?, ?, 'Active')",
                     (cid, sid, amount, day.isoformat()))


def test_levels_target_and_ceiling(db):
    from engines.day_cap import level
    assert (level(300_000), level(380_000), level(420_000)) == ("ok", "near", "over")


def test_parking_onto_a_full_day_asks_first_and_confirming_flags_it(web, db):
    _admin(web)
    cid = make_creditor(db, "Battery Depot", terms="30")
    other = make_creditor(db, "Goodyear", terms="30")
    day = _bank_day(40)
    _check_on(db, other, day, 300_000)
    sid = _new_batch(web, cid)
    _add_inv(web, sid, doc="SI-1", gross="120,000", date=day.isoformat())
    r = web.post(f"/order-statements/{sid}/", {"action": "chk_park", "d1": day.isoformat(), "a1": "120,000"})
    assert r.status_code == 302
    assert fetchone(_db(), "SELECT COUNT(*) FROM checks_issued WHERE statement_id = ?", (sid,))[0] == 0      # held, not parked
    page = web.get(r["Location"]).content.decode()
    assert "over the daily limit" in page and "Confirm anyway" in page and "420,000.00" in page
    web.post(f"/order-statements/{sid}/", {"action": "chk_park", "d1": day.isoformat(), "a1": "120,000", "confirm_over": "1"})
    assert fetchone(_db(), "SELECT COUNT(*) FROM checks_issued WHERE statement_id = ?", (sid,))[0] == 1
    assert fetchone(_db(), "SELECT COUNT(*) FROM review_flags WHERE kind = 'DAY_OVER_CAP'")[0] == 1


def test_parking_on_a_day_with_room_just_parks(web, db):
    _admin(web)
    cid = make_creditor(db, "Battery Depot", terms="30")
    sid = _new_batch(web, cid)
    day = _bank_day(40)
    _add_inv(web, sid, doc="SI-2", gross="50,000", date=day.isoformat())
    web.post(f"/order-statements/{sid}/", {"action": "chk_park", "d1": day.isoformat(), "a1": "50,000"})
    assert fetchone(_db(), "SELECT COUNT(*) FROM checks_issued WHERE statement_id = ?", (sid,))[0] == 1
    assert fetchone(_db(), "SELECT COUNT(*) FROM review_flags WHERE kind = 'DAY_OVER_CAP'")[0] == 0


def test_moving_a_check_counts_the_new_day_without_its_old_one(db):
    from engines.day_cap import over_days
    cid = make_creditor(db, "Goodyear", terms="30")
    a, b = _bank_day(40), _bank_day(50)
    _check_on(db, cid, a, 350_000)
    _check_on(db, cid, b, 300_000)
    assert over_days([(a, 350_000)], minus=[(a, 350_000)]) == []                  # staying on its own day: nothing new
    over = over_days([(b, 350_000)], minus=[(a, 350_000)])
    assert len(over) == 1 and over[0]["total"] == 650_000


def test_an_si_with_no_check_and_an_open_po_count_on_their_day(db):
    from engines.day_cap import day_totals
    cid = make_creditor(db, "Acme", terms="30")
    day = _bank_day(40)
    with sqlite3.connect(db) as conn:
        conn.execute("""INSERT INTO invoices (creditor_id, doc_ref, invoice_date, due_date, gross_amount, amount, status)
                        VALUES (?, 'SI-9', ?, ?, 7000, 7000, 'Unpaid')""", (cid, day.isoformat(), day.isoformat()))
    assert day_totals(day, day).get(day, 0) >= 7000


def test_suggest_dates_keeps_days_under_the_target_and_never_before_a_due_date(web, db):
    from engines.day_cap import suggest_batch_dates
    _admin(web)
    cid = make_creditor(db, "Battery Depot", terms="30")
    sid = _new_batch(web, cid)
    due = _bank_day(40)
    from engines.clock import manila_today
    for k in range(3):
        _add_inv(web, sid, doc=f"SI-{k}", gross="200,000", date=manila_today().isoformat(), confirmed="1")   # same amount and day: confirm the "possible duplicate"
        assert fetchone(_db(), "SELECT COUNT(*) FROM invoices WHERE doc_ref = ?", (f"SI-{k}",))[0] == 1
    with sqlite3.connect(_db()) as conn:
        conn.execute("UPDATE invoices SET due_date = ? WHERE statement_id = ?", (due.isoformat(), sid))
    plan = suggest_batch_dates(sid)
    assert round(sum(p["amount"] for p in plan), 2) == 600_000.0
    assert all(p["date"] >= due for p in plan) and len({p["date"] for p in plan}) == len(plan)
    assert all(p["day_total"] <= 350_000.005 for p in plan if p["status"] != "Doesn't fit")
    assert len(plan) >= 2                                                           # ₱600,000 cannot all sit on one ₱350,000 day
    page = web.get(f"/order-statements/{sid}/?tab=checks").content.decode()
    assert "Within the daily limit" in page


def test_card_reserve_until_the_soa_comes(db):
    from engines.day_cap import card_reserve_rows
    from engines.clock import manila_today
    bank = make_creditor(db, "RCBC", creditor_type="CREDIT_CARD")
    today = manila_today()
    with sqlite3.connect(db) as conn:
        card = conn.execute("INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit) VALUES (?, 'Platinum', '5003', 1)", (bank,)).lastrowid
        for back, amt in ((3, 80_000), (2, 100_000), (1, 120_000)):
            m = (today.replace(day=1) - timedelta(days=28 * back)).replace(day=15)
            conn.execute("INSERT INTO cc_statements (card_id, due_date, total_amount_due, status) VALUES (?, ?, ?, 'Unpaid')", (card, m.isoformat(), amt))
    rows = card_reserve_rows(today, today + timedelta(days=95))
    assert len(rows) >= 2 and set(rows["amount"]) == {100_000.0} and rows["reference"].str.startswith("Card reserve (estimate)").all()
    from engines.settings_store import set_setting
    set_setting("card_reserve_on", "0")
    assert card_reserve_rows(today, today + timedelta(days=95)).empty


def test_the_limit_can_count_suppliers_only(db):
    # owner's real figures (2026-10-10): loans and cards alone pass 400,000 on most days, so what the limit counts is a Settings choice
    import app
    from engines.day_cap import day_totals
    from engines.settings_store import set_setting
    sup, lender = make_creditor(db, "Goodyear", terms="30"), make_creditor(db, "Richie", creditor_type="LENDER")
    day = _bank_day(40)
    _check_on(db, sup, day, 100_000)
    with sqlite3.connect(db) as conn:
        ln = conn.execute("INSERT INTO loan_notes (creditor_id, note_ref, issue_date, status) VALUES (?, 'LN-1', ?, 'Active')", (lender, day.isoformat())).lastrowid
        conn.execute("INSERT INTO checks_issued (bank_account_id, creditor_id, loan_note_id, amount, check_date, status) VALUES (1, ?, ?, 50000, ?, 'Active')",
                     (lender, ln, day.isoformat()))
    assert day_totals(day, day)[day] == 150_000
    set_setting("po_daily_limit_scope", "0")
    assert day_totals(day, day)[day] == 100_000
