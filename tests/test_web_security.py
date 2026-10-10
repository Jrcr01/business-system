import sqlite3
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
    from tests.test_web_see_and_do import levels_post
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines")
    family = _sign_in_new("family@slicks.test", "owner")
    assert family.get("/calendar/").status_code == 200 and family.get("/order-statements/").status_code == 200
    uid = _uid("family@slicks.test")
    page = web.get(f"/settings/users/{uid}/").content.decode()
    assert "What Family may see and do" in page and "Pay suppliers" in page
    web.post(f"/settings/users/{uid}/", levels_post({"order-statements": "see"}))         # Order statements: see only
    assert {"invoice.enter", "check.park", "check.issue", "check.release", "check.void"} <= {
        r[0] for r in sqlite3.connect(_db()).execute("SELECT action FROM user_restrictions WHERE user_id = ?", (uid,))}
    page = family.get("/order-statements/")                                       # at the very next click: view only
    assert page.status_code == 200 and "View only" in page.content.decode()
    assert family.post("/order-statements/new/", {"s": cid}).status_code in (302, 403)     # the save is refused
    assert fetchone(_db(), "SELECT COUNT(*) FROM order_statements")[0] == 0
    assert family.get("/calendar/").status_code == 200                           # what was not switched off still works
    assert fetchone(_db(), "SELECT user_action FROM audit_logs WHERE entity_name = 'app_users' ORDER BY log_id DESC LIMIT 1")[0] == "USER_ACCESS_CHANGED"


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



def test_the_new_switches_presets_and_copying(web, db):
    # owner, 2026-10-09: "Mark payables paid" apart from Bank; cards and bank loans apart from lenders (one-time carry-over); presets; copy from another person
    import engines.auth_service as au
    from engines.permissions import ACCESS_PRESETS
    from tests.test_web_see_and_do import levels_post
    _admin(web)
    family = _sign_in_new("family@slicks.test", "owner")
    _sign_in_new("cousin@slicks.test", "owner")
    uid, cousin = _uid("family@slicks.test"), _uid("cousin@slicks.test")
    with sqlite3.connect(_db()) as conn:                                             # the one-time carry-over: the new switches start as Bank / Loans
        conn.execute("INSERT INTO user_restrictions (user_id, action) VALUES (?, 'bank.use'), (?, 'loans.use')", (cousin, cousin))
    assert au.split_new_switches() == 4 and au.split_new_switches() == 0             # + Check booklets, which starts as Bank and cash (2026-10-11)
    assert {"payables.mark_paid", "cards.use", "bankloans.use", "booklets.use"} <= au.get_restrictions(cousin)
    page = web.get(f"/settings/users/{uid}/").content.decode()
    assert "Daily operations" in page and "Copy from another person" in page and "Daily payables (mark paid)" in page and "Credit cards" in page
    daily = ACCESS_PRESETS["daily"][1]
    web.post(f"/settings/users/{uid}/", levels_post(daily["levels"], daily["extras"]))
    off = au.get_restrictions(uid)
    assert "bank.use" in off and "cards.use" in off and "payables.mark_paid" not in off and "loans.use" not in off
    assert family.get("/checks/").status_code == 200 and "View only" not in family.get("/checks/").content.decode()
    for url in ("/bank-cash/", "/credit-cards/"):                                     # see only: view only
        assert "View only" in family.get(url).content.decode(), url
    assert "Showing <b>Family</b>" in web.get(f"/settings/users/{cousin}/?copy={uid}").content.decode()
