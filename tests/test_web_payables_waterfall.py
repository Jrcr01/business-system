"""The AP waterfall (owner, 2026-10-09: "all invoices must be recorded right away so we can have live visibility on the payables"): an SI is owed the
moment it is recorded, on its due date, until checks cover it -- never counted twice; goods delivered against a PO are owed at the PO price until their
SI is recorded; the day check counts SIs with no check and every open PO, whatever the forecast setting."""
import sqlite3
from datetime import date, timedelta

from conftest import fetchone, make_creditor
from tests.test_web_d0 import _admin
from tests.test_web_d3a import _add_inv, _new_batch


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _sched():
    from engines.reports_service import get_payment_schedule_all_tracks
    return get_payment_schedule_all_tracks()


def _future(days):
    from engines.clock import manila_today
    return (manila_today() + timedelta(days=days)).isoformat()


def test_a_batch_without_checks_is_owed_on_its_invoices_due_dates_and_a_check_never_doubles_it(web, db):
    _admin(web)
    cid = make_creditor(db, "Battery Depot", terms="30")
    sid = _new_batch(web, cid)
    for doc, gross, day in (("246190", "22,840", _future(5)), ("246189", "51,262.40", _future(10)), ("246287", "12,360", _future(20))):
        _add_inv(web, sid, doc=doc, gross=gross, date=day)
    with sqlite3.connect(_db()) as conn:                           # due dates set exactly, whatever the terms
        for doc, day in (("246190", _future(35)), ("246189", _future(40)), ("246287", _future(50))):
            conn.execute("UPDATE invoices SET due_date = ? WHERE doc_ref = ?", (day, doc))
    s = _sched()
    inv = s[s["stage"] == "invoice"]
    assert sorted(inv["reference"]) == ["SI 246189 · no check yet", "SI 246190 · no check yet", "SI 246287 · no check yet"]
    assert round(inv["amount"].sum(), 2) == 86462.40
    assert set(inv["due_date"].astype(str).str[:10]) == {_future(35), _future(40), _future(50)}
    before = round(s["amount"].sum(), 2)

    web.post(f"/order-statements/{sid}/", {"action": "chk_park", "d1": _future(36), "a1": "22,840"})    # a check covering the oldest SI
    s = _sched()
    inv = s[s["stage"] == "invoice"]
    assert "SI 246190 · no check yet" not in set(inv["reference"])                  # replaced by the check ...
    assert round(s["amount"].sum(), 2) == before                                    # ... nothing counted twice
    assert round(s[s["stage"] == "check"]["amount"].sum(), 2) == 22840.0


def test_an_unbatched_si_shows_and_paid_or_cancelled_ones_do_not(web, db):
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines", terms="30")
    with sqlite3.connect(_db()) as conn:
        for doc, status in (("U-1", "Unpaid"), ("U-2", "Paid"), ("U-3", "Cancelled")):
            conn.execute("""INSERT INTO invoices (creditor_id, doc_ref, invoice_date, due_date, gross_amount, amount, status)
                            VALUES (?, ?, ?, ?, 5000, 5000, ?)""", (cid, doc, _future(0), _future(30), status))
    inv = _sched()
    inv = inv[inv["stage"] == "invoice"]
    assert list(inv["reference"]) == ["SI U-1 · no check yet"] and list(inv["batch"]) == ["Not in a batch"]


def test_goods_received_without_an_si_are_owed_at_the_po_price_until_the_si_comes(db):
    from engines.po_rules import get_po, order_po, receive_goods, save_po
    from engines.price_list_service import add_price
    cid = make_creditor(db, "Acme Tires", terms="30")
    add_price(cid, "Tire", "pc", 100, date(2026, 9, 1))
    ok, msg, po = save_po(None, cid, date.today(), date.today() + timedelta(days=5), [{"item_name": "Tire", "unit": "pc", "qty": 10}], "")
    assert ok, msg
    assert order_po(po)[0]
    s = _sched()
    assert s.empty or s[s["stage"] == "received"].empty                          # ordered only: committed, not owed
    ln = get_po(po)["lines"].iloc[0]
    ok, msg, _ = receive_goods(cid, [{"line_id": int(ln["line_id"]), "qty": 4}])
    assert ok, msg
    rec = _sched()
    rec = rec[rec["stage"] == "received"]
    assert len(rec) == 1 and round(float(rec["amount"].iloc[0]), 2) == 400.0 and "received, no SI yet" in rec["reference"].iloc[0]


def test_the_day_check_counts_sis_without_a_check_and_open_pos_even_with_the_forecast_setting_off(db):
    from engines import settings_store
    from engines.cash_insights_service import scheduled_outflows
    from engines.clock import manila_today
    from engines.po_rules import order_po, save_po
    from engines.price_list_service import add_price
    cid = make_creditor(db, "Acme Tires", terms="30")
    add_price(cid, "Tire", "pc", 100, date(2026, 9, 1))
    day = manila_today() + timedelta(days=40)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    with sqlite3.connect(db) as conn:
        conn.execute("""INSERT INTO invoices (creditor_id, doc_ref, invoice_date, due_date, gross_amount, amount, status)
                        VALUES (?, 'SI-9', ?, ?, 7000, 7000, 'Unpaid')""", (cid, manila_today().isoformat(), day.isoformat()))
    ok, msg, po = save_po(None, cid, manila_today(), manila_today() + timedelta(days=3), [{"item_name": "Tire", "unit": "pc", "qty": 10}], "")
    assert ok, msg
    assert order_po(po)[0]
    settings_store.set_setting("po_in_forecast", "0")
    end = manila_today() + timedelta(days=90)
    plan = scheduled_outflows(manila_today(), end, include_pos=True)
    assert "SI SI-9 · no check yet" in set(plan["reference"]) and (plan["track"] == "Purchase order").any()
    assert not (scheduled_outflows(manila_today(), end)["track"] == "Purchase order").any()        # the bank forecast still follows the setting
    assert "SI SI-9 · no check yet" not in set(scheduled_outflows(manila_today(), end, include_invoices=False)["reference"])
    from engines.po_rules import _committed_rows
    rows = _committed_rows(end)
    assert float(rows[rows["day"] == day]["amount"].sum()) >= 7000.0                 # the order's day check sees the SI on its day
