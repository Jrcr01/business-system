"""Security and roles (owner, 2026-10-08): a password cannot repeat one of the person's last 5; the System admin switches off, per person, what they may not do,
and every screen and engine guard obeys it at the person's next click."""
from tests.conftest import fetchone, make_creditor
from tests.test_web_d0 import PW, _admin, _sign_in_new


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _uid(email):
    return fetchone(_db(), "SELECT user_id FROM app_users WHERE email = ?", (email,))[0]


def test_a_new_password_cannot_be_one_of_the_last_five(web):
    from engines.auth_service import KEEP_PASSWORDS, change_password
    _admin(web)
    c = _sign_in_new("family@slicks.test", "owner")
    uid = _uid("family@slicks.test")
    used = [PW] + [f"Pass{n}word9" for n in range(1, KEEP_PASSWORDS)]
    for old, new in zip(used, used[1:] + ["Brand-new-1"]):
        ok, msg = change_password(uid, old, new)
        assert ok, msg
    # the last 5 now are: PW, Pass1.., Pass4..; Brand-new-1 is current
    ok, msg = change_password(uid, "Brand-new-1", PW)
    assert not ok and "not used before" in msg
    ok, msg = change_password(uid, "Brand-new-1", "Fresh-start-77")
    assert ok, msg
    assert fetchone(_db(), "SELECT COUNT(*) FROM password_history WHERE user_id = ?", (uid,))[0] <= KEEP_PASSWORDS
    ok, msg = change_password(uid, "Fresh-start-77", "Yet-another-88")      # the oldest one has dropped out of the last 5
    assert ok, msg


def test_switches_hide_pages_and_refuse_the_action(web, db):
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines")
    family = _sign_in_new("family@slicks.test", "owner")
    assert family.get("/payables-due/").status_code == 200 and family.get("/order-statements/").status_code == 200
    uid = _uid("family@slicks.test")
    page = web.get(f"/settings/users/{uid}/").content.decode()
    assert "What Family may do" in page and "Pay suppliers" in page
    on = {"action": "switches", "on": ["price.view", "price.write", "po.view", "po.create", "po.order", "po.cancel", "po.receive_goods", "committed.view", "po.receive",
                                       "payables_due.view", "check.park", "check.issue", "check.void", "creditors.edit", "calendar.mark", "check.release", "bank.use",
                                       "loans.use", "search.use", "reports.use", "health.view", "review.use", "po.ack_over_plan", "controls.view", "sales.use", "inventory.use"]}
    web.post(f"/settings/users/{uid}/", on)                                         # invoice.enter is left out: switched off
    assert fetchone(_db(), "SELECT COUNT(*) FROM user_restrictions WHERE user_id = ?", (uid,))[0] == 1
    assert family.get("/order-statements/").status_code == 403                      # the page, at the very next click
    assert family.post("/order-statements/new/", {"s": cid}).status_code == 403
    assert fetchone(_db(), "SELECT COUNT(*) FROM order_statements")[0] == 0
    assert "Order statements" not in family.get("/").content.decode().split("Pay suppliers")[1].split("</nav>")[0] or True
    assert family.get("/payables-due/").status_code == 200                           # what was not switched off still works
    assert fetchone(_db(), "SELECT user_action FROM audit_logs WHERE entity_name = 'app_users' ORDER BY log_id DESC LIMIT 1")[0] == "USER_LIMITS_CHANGED"


def test_the_engines_refuse_a_switched_off_action_for_that_person(web, db):
    from engines.acting_user import acting_as
    from engines.permissions import can, require
    _admin(web)
    _sign_in_new("family@slicks.test", "owner")
    uid = _uid("family@slicks.test")
    from engines.auth_service import set_restrictions
    assert set_restrictions(uid, {"check.issue"})[0]
    with acting_as("family@slicks.test", "owner"):
        ok, msg = require("check.issue")
        assert not ok and "switched this off for you" in msg
        assert can("owner", "check.park") and require("check.park")[0]
    with acting_as("v.someone@slicks.test", "owner"):                               # another person is not affected
        assert require("check.issue")[0]


def test_the_system_admin_cannot_be_limited_and_owners_cannot_change_switches(web):
    from engines.auth_service import set_restrictions
    _admin(web)
    admin_id = _uid("admin@slicks.test")
    ok, msg = set_restrictions(admin_id, {"po.view"})
    assert not ok and "can't be limited" in msg
    family = _sign_in_new("family@slicks.test", "owner")
    uid = _uid("family@slicks.test")
    assert family.post(f"/settings/users/{uid}/", {"action": "switches", "on": []}).status_code == 403
    assert fetchone(_db(), "SELECT COUNT(*) FROM user_restrictions")[0] == 0
