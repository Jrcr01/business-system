"""Pay suppliers -> Checks in Django (Phase 2, owner 2026-10-08): the written checks waiting for the bank, the cleared tick, and the one-time cut-off (System
admin only). The rule itself is tested in test_clearing_rule.py."""
from conftest import fetchone
from engines.clock import manila_today
from tests.test_clearing_rule import _issued
from tests.test_web_d0 import _admin, _sign_in_new

TODAY = manila_today()


def test_the_checks_page_ticks_and_only_the_admin_sees_the_cutoff(web, db):
    _admin(web)
    sid, cid = _issued(db)
    page = web.get("/checks/").content.decode()
    assert "Waiting for the bank" in page and "0004471" in page and "Mark cleared" in page and "Cut-off" in page
    web.post("/checks/", {"action": "clear", "check_id": cid, "on": str(TODAY), "note": "online banking"})
    assert fetchone(db, "SELECT manual_cleared_at FROM checks_issued WHERE check_id = ?", (cid,))[0] == str(TODAY)
    assert fetchone(db, "SELECT status FROM invoices WHERE statement_id = ?", (sid,))[0] == "Paid"
    web.post("/checks/", {"action": "unclear", "check_id": cid})
    assert fetchone(db, "SELECT manual_cleared_at FROM checks_issued WHERE check_id = ?", (cid,))[0] is None
    family = _sign_in_new("family@slicks.test", "owner")
    assert "Cut-off" not in family.get("/checks/").content.decode()
    family.post("/checks/", {"action": "cutoff"})
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CLEARING_CUTOFF'")[0] == 0
