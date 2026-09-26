"""Checks typed into the Issue-a-check grid but not parked are remembered per batch (2026-09-27)."""
from conftest import make_creditor, make_statement
from engines.park_draft_service import save_park_draft, load_park_draft, clear_park_draft, list_park_drafts


def test_draft_round_trips_and_is_replaced_by_the_latest_typing(db):
    sid = make_statement(db, make_creditor(db, name="ACME Tires"))
    save_park_draft(sid, "By number of checks", 2, 10000.0, [5, 6], [("2026-10-01", 4000.0), ("2026-11-01", 6000.0)])

    d = load_park_draft(sid)
    assert d["mode"] == "By number of checks" and d["count"] == 2 and d["balance"] == 10000.0
    assert d["invoice_ids"] == (5, 6) and d["rows"] == [("2026-10-01", 4000.0), ("2026-11-01", 6000.0)]

    save_park_draft(sid, "One check per invoice", 1, 10000.0, [5, 6], [("2026-10-05", 10000.0)])
    assert load_park_draft(sid)["rows"] == [("2026-10-05", 10000.0)]
    assert len(list_park_drafts()) == 1


def test_list_shows_label_count_and_total_and_clear_removes_it(db):
    sid = make_statement(db, make_creditor(db, name="ACME Tires"))
    save_park_draft(sid, "By number of checks", 2, 500.0, [1], [("2026-10-01", 200.0), ("2026-10-02", 300.0)])

    (d,) = list_park_drafts()
    assert d["statement_id"] == sid and "ACME Tires" in d["label"] and d["n"] == 2 and d["total"] == 500.0

    clear_park_draft(sid)
    assert load_park_draft(sid) is None and list_park_drafts() == []
