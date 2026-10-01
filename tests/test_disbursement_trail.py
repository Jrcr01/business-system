"""Disbursement trail (B6): follow one purchase order all the way to the bank. Ordered -> delivered -> invoiced -> in a batch -> checks -> released -> cleared,
with the planned dates beside the actual ones."""
import sqlite3
from datetime import date

import app
from conftest import make_creditor
from engines.disbursement_trail_service import STAGES, pipeline, trail_for_po
from engines.pdc_release_service import mark_released
from engines.po_rules import order_po, receive_into_batch, save_po
from engines.price_list_service import add_price

TODAY = date(2026, 10, 19)


def _po(db, qty=10):
    cid = make_creditor(db, name="Acme Tires", terms="30")
    add_price(cid, "Tire", "pc", 100, date(2026, 9, 1))
    ok, _, po = save_po(None, cid, TODAY, date(2026, 10, 25), [{"item_name": "Tire", "unit": "pc", "qty": qty}], "")
    assert ok and order_po(po)[0]
    return cid, po


def _line(po):
    from engines.po_rules import get_po
    return int(get_po(po)["lines"].iloc[0]["line_id"])


def _stage(tr, key):
    return next(s for s in tr["stages"] if s["key"] == key)


def test_a_fresh_order_has_only_its_first_stage_done(db):
    _, po = _po(db)
    tr = trail_for_po(po)
    assert [s["key"] for s in tr["stages"]] == [k for k, _ in STAGES]
    assert _stage(tr, "ordered")["state"] == "done" and _stage(tr, "delivered")["state"] == "waiting"
    assert all(_stage(tr, k)["state"] == "waiting" for k in ("invoiced", "batch", "checks", "released", "cleared"))
    assert tr["current"] == "delivered"                                    # the next thing to happen


def test_an_order_past_its_delivery_date_is_flagged_late(db):
    _, po = _po(db)
    from engines.clock import set_fake_now
    from datetime import datetime
    set_fake_now(datetime(2026, 11, 2, 9, 0))
    try:
        assert _stage(trail_for_po(po), "delivered")["state"] == "late"
    finally:
        set_fake_now(None)


def test_the_whole_journey_from_order_to_the_bank(db):
    cid, po = _po(db)
    sid = app.create_order_statement(cid)
    (ok, msg, inv) = receive_into_batch(cid, sid, "INV-1", date(2026, 10, 26), [{"line_id": _line(po), "qty": 10, "actual_unit_price": 110.0}], received_date=date(2026, 10, 27))
    assert ok, msg
    tr = trail_for_po(po)
    assert _stage(tr, "delivered")["state"] == "done" and _stage(tr, "invoiced")["state"] == "done" and _stage(tr, "batch")["state"] == "done"
    assert _stage(tr, "checks")["state"] == "waiting" and tr["current"] == "checks"
    assert tr["invoices"].iloc[0]["doc_ref"] == "INV-1" and tr["invoices"].iloc[0]["actual_amount"] == 1100.0 and tr["invoices"].iloc[0]["est_amount"] == 1000.0

    ok, _, check_id = app.issue_check(cid, 1100.0, date(2026, 12, 1), statement_id=sid)
    assert ok and app.allocate_check_to_invoices(check_id, [(inv, 1100.0)])[0]
    tr = trail_for_po(po)
    assert _stage(tr, "checks")["state"] == "done" and tr["checks"]["check_id"].tolist() == [check_id] and tr["current"] == "released"

    sqlite3.connect(db).execute("UPDATE checks_issued SET printed_at = '2026-10-28 10:00:00', check_number = '3992000001' WHERE check_id = ?", (check_id,)).connection.commit()
    assert _stage(trail_for_po(po), "released")["state"] == "waiting"
    assert mark_released([check_id], date(2026, 10, 30), "Juan")[0]
    tr = trail_for_po(po)
    assert _stage(tr, "released")["state"] == "done" and "Juan" in _stage(tr, "released")["detail"] and tr["current"] == "cleared"

    sqlite3.connect(db).execute("UPDATE checks_issued SET bank_cleared_date = '2026-12-03' WHERE check_id = ?", (check_id,)).connection.commit()
    tr = trail_for_po(po)
    assert _stage(tr, "cleared")["state"] == "done" and tr["current"] is None
    dates = tr["dates"].set_index("what")["date"]
    assert dates["Delivery expected"] == date(2026, 10, 25) and dates["Delivered"] == date(2026, 10, 27)
    assert dates["Payment expected (order)"] == date(2026, 11, 25) and dates["Invoice due"] == date(2026, 11, 26)
    assert dates["Check dated"] == date(2026, 12, 1) and dates["Cleared by the bank"] == date(2026, 12, 3)


def test_a_partial_delivery_shows_how_much_has_come(db):
    cid, po = _po(db)
    sid = app.create_order_statement(cid)
    receive_into_batch(cid, sid, "INV-1", date(2026, 10, 26), [{"line_id": _line(po), "qty": 4, "actual_unit_price": 100.0}])
    d = _stage(trail_for_po(po), "delivered")
    assert d["state"] == "partly" and "4 of 10" in d["detail"]


def test_the_pipeline_lists_every_order_with_where_it_stands(db):
    cid, po1 = _po(db)
    ok, _, po2 = save_po(None, cid, TODAY, date(2026, 10, 25), [{"item_name": "Tire", "unit": "pc", "qty": 5}], "")
    order_po(po2)
    sid = app.create_order_statement(cid)
    receive_into_batch(cid, sid, "INV-1", date(2026, 10, 26), [{"line_id": _line(po1), "qty": 10, "actual_unit_price": 100.0}])
    df = pipeline().set_index("po_id")
    assert df.loc[po1, "stage"] == "Invoiced" and df.loc[po2, "stage"] == "Ordered"
    assert df.loc[po1, "invoiced"] == 1000.0 and df.loc[po1, "estimated"] == 1000.0 and df.loc[po2, "invoiced"] == 0
    assert pipeline(only_open=True)["po_id"].tolist().count(po2) == 1


def test_a_missing_or_draft_order_has_no_trail(db):
    cid = make_creditor(db, name="X")
    assert trail_for_po(999) is None
