"""Stage D1 of the Django rebuild (Masters, docs/mockups/d1_masters.html): Creditors (list + one edit form each, safe when two people edit at once), Settings
(numbers and the Home notice), Users, Who can do what; every creditor change audited with who made it."""
from tests.conftest import fetchone
from tests.test_web_d0 import PW, _admin, _sign_in_new


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _add(web, name, ctype="SUPPLIER", **extra):
    data = {"name": name, "type": ctype, "terms": "60", "lead_days": "", "tin": "", "contact_person": "", "contact_number": "", "email": ""}
    data.update(extra)
    return web.post("/creditors/new/", data)


def _cid(name):
    return fetchone(_db(), "SELECT creditor_id FROM creditors WHERE creditor_name = ?", (name,))[0]


def _stamp(cid):
    from engines.creditor_rules import get_creditor
    return get_creditor(cid)["stamp"]


def test_add_list_and_filter(web):
    _admin(web)
    r = _add(web, "Goodyear Philippines", lead_days="5", contact_person="Ana Cruz")
    cid = _cid("Goodyear Philippines")
    assert r["Location"] == f"/creditors/{cid}/"
    _add(web, "Juan Dela Cruz", "LENDER")
    page = web.get("/creditors/").content.decode()
    assert "Goodyear Philippines" in page and "Juan Dela Cruz" in page and "60 days" in page
    assert fetchone(_db(), "SELECT lead_days, terms FROM creditors WHERE creditor_id = ?", (cid,)) == (5, "60")
    only_lenders = web.get("/creditors/?type=LENDER").content.decode()
    assert "Juan Dela Cruz" in only_lenders and "Goodyear" not in only_lenders
    assert "Juan" not in web.get("/creditors/?q=ana").content.decode()        # search: contact person


def test_a_name_on_file_asks_first(web):
    _admin(web)
    _add(web, "Bridgestone Tires")
    r = _add(web, "bridgestone tires")
    assert r.status_code == 200 and b"Did you mean the one already on file?" in r.content and b"Yes, add it anyway" in r.content
    assert fetchone(_db(), "SELECT COUNT(*) FROM creditors WHERE LOWER(creditor_name) = 'bridgestone tires'")[0] == 1
    r = _add(web, "Bridgestone Tires", confirmed="1")
    assert r.status_code == 302
    assert fetchone(_db(), "SELECT COUNT(*) FROM creditors WHERE LOWER(creditor_name) = 'bridgestone tires'")[0] == 2


def test_edit_saves_and_is_audited_with_who(web):
    _admin(web)
    _add(web, "Michelin")
    cid = _cid("Michelin")
    r = web.post(f"/creditors/{cid}/", {"action": "save", "stamp": _stamp(cid), "name": "Michelin Tires", "type": "SUPPLIER", "terms": "90", "lead_days": "3",
                                        "tin": "", "contact_person": "", "contact_number": "0917", "email": ""})
    assert r["Location"] == f"/creditors/{cid}/"
    assert fetchone(_db(), "SELECT creditor_name, terms, lead_days, contact_number FROM creditors WHERE creditor_id = ?", (cid,)) == ("Michelin Tires", "90", 3, "0917")
    rows = fetchone(_db(), "SELECT GROUP_CONCAT(user_action), GROUP_CONCAT(DISTINCT user_email) FROM audit_logs WHERE entity_name = 'creditors' AND entity_id = ?", (cid,))
    assert "CREDITOR_ADDED" in rows[0] and "CREDITOR_EDITED" in rows[0] and rows[1] == "admin@slicks.test"


def test_two_people_editing_the_same_creditor_never_overwrite_each_other(web):
    _admin(web)
    _add(web, "Pirelli")
    cid = _cid("Pirelli")
    other = _sign_in_new("staff@slicks.test", "owner")
    seen_by_both = _stamp(cid)                                                     # both opened the form
    base = {"action": "save", "type": "SUPPLIER", "terms": "60", "lead_days": "", "tin": "", "contact_person": "", "contact_number": "", "email": ""}
    assert other.post(f"/creditors/{cid}/", {**base, "stamp": seen_by_both, "name": "Pirelli Asia"}).status_code == 302
    r = web.post(f"/creditors/{cid}/", {**base, "stamp": seen_by_both, "name": "Pirelli PH"})
    assert r.status_code == 200 and b"Someone else changed this creditor after you opened it" in r.content and b"staff@slicks.test" in r.content
    assert fetchone(_db(), "SELECT creditor_name FROM creditors WHERE creditor_id = ?", (cid,))[0] == "Pirelli Asia"
    assert b'value="Pirelli Asia"' in r.content                                    # the form now shows their version


def test_type_is_locked_once_there_is_history(web, db):
    from tests.conftest import make_statement
    _admin(web)
    _add(web, "Yokohama")
    cid = _cid("Yokohama")
    make_statement(db, cid)
    page = web.get(f"/creditors/{cid}/").content.decode()
    assert "Can&#x27;t change: it has 1 order statements" in page or "Can't change" in page
    r = web.post(f"/creditors/{cid}/", {"action": "save", "stamp": _stamp(cid), "name": "Yokohama", "type": "LENDER", "terms": "", "lead_days": "",
                                        "tin": "", "contact_person": "", "contact_number": "", "email": ""})
    assert b"The type can" in r.content
    assert fetchone(_db(), "SELECT creditor_type FROM creditors WHERE creditor_id = ?", (cid,))[0] == "SUPPLIER"


def test_switch_off_on_and_delete(web, db):
    from tests.conftest import make_statement
    _admin(web)
    _add(web, "Dunlop")
    cid = _cid("Dunlop")
    web.post(f"/creditors/{cid}/", {"action": "off", "stamp": _stamp(cid)})
    assert fetchone(_db(), "SELECT is_active FROM creditors WHERE creditor_id = ?", (cid,))[0] == 0
    link = f'href="/creditors/{cid}/"'
    assert link not in web.get("/creditors/").content.decode()
    assert link in web.get("/creditors/?off=1").content.decode()
    web.post(f"/creditors/{cid}/", {"action": "on", "stamp": _stamp(cid)})
    assert fetchone(_db(), "SELECT is_active FROM creditors WHERE creditor_id = ?", (cid,))[0] == 1
    make_statement(db, cid)
    assert b"Delete</button>" not in web.get(f"/creditors/{cid}/").content           # history: switch off only
    assert b"be deleted. Switch it off instead." in web.post(f"/creditors/{cid}/", {"action": "delete", "stamp": _stamp(cid)}).content
    _add(web, "Typo Supplier")
    tid = _cid("Typo Supplier")
    assert web.post(f"/creditors/{tid}/", {"action": "delete", "stamp": _stamp(tid)})["Location"] == "/creditors/"
    assert fetchone(_db(), "SELECT COUNT(*) FROM creditors WHERE creditor_id = ?", (tid,))[0] == 0
    assert fetchone(_db(), "SELECT user_action FROM audit_logs WHERE entity_name = 'creditors' AND entity_id = ? ORDER BY log_id DESC LIMIT 1", (tid,))[0] == "CREDITOR_DELETED"


def test_a_new_supplier_by_an_owner_goes_on_the_review_list(web):
    _admin(web)
    family = _sign_in_new("brother@slicks.test", "owner")
    _add(family, "New Payee Co")
    assert fetchone(_db(), "SELECT COUNT(*) FROM review_flags WHERE kind = 'NEW_PAYEE'")[0] == 1


def test_creditors_refused_to_a_role_without_it(web):
    _admin(web)
    mother = _sign_in_new("mother@slicks.test", "releaser")
    assert mother.get("/creditors/").status_code == 403
    assert mother.post("/creditors/new/", {"name": "X", "type": "SUPPLIER"}).status_code == 403
    assert fetchone(_db(), "SELECT COUNT(*) FROM creditors WHERE creditor_name = 'X'")[0] == 0


def test_settings_numbers_and_the_notice_on_home(web):
    _admin(web)
    page = web.get("/settings/").content.decode()
    assert "A check is stale after" in page and "Default 180" in page
    r = web.post("/settings/", {"action": "save", "key": "stale_check_days", "value": "200"})
    assert r.status_code == 302
    from engines.settings_registry import value
    assert value("stale_check_days") == 200
    web.post("/settings/", {"action": "save", "key": "po_daily_payables_limit", "value": "500,000"})     # a thousands comma is fine
    assert value("po_daily_payables_limit") == 500000
    web.post("/settings/", {"action": "save", "key": "stale_check_days", "value": "5"})                 # below the minimum: refused
    assert value("stale_check_days") == 200
    web.post("/settings/", {"action": "reset", "key": "stale_check_days"})
    assert value("stale_check_days") == 180
    web.post("/settings/", {"action": "notice", "notice": "No check release on Friday"})
    assert b"No check release on Friday" in web.get("/").content
    assert fetchone(_db(), "SELECT user_email FROM audit_logs WHERE entity_name = 'settings' ORDER BY log_id DESC LIMIT 1")[0] == "admin@slicks.test"


def test_settings_closed_to_owners(web):
    _admin(web)
    family = _sign_in_new("brother@slicks.test", "owner")
    assert family.get("/settings/").status_code == 403
    assert family.post("/settings/", {"action": "save", "key": "stale_check_days", "value": "200"}).status_code == 403
    from engines.settings_registry import value
    assert value("stale_check_days") == 180


def test_users_add_shows_the_temporary_password_once(web):
    _admin(web)
    r = web.post("/settings/users/", {"email": "mother@slicks.test", "username": "mama", "name": "Mother", "role": "releaser"})
    page = r.content.decode()
    assert r.status_code == 200 and "Temporary password for <b>mother@slicks.test</b>" in page    # the email they sign in with (2026-10-09)
    assert "Temporary password for" not in web.get("/settings/users/").content.decode()     # shown only once
    uid = fetchone(_db(), "SELECT user_id, role, must_change_password FROM app_users WHERE email = 'mother@slicks.test'")
    assert uid[1:] == ("releaser", 1)


def test_users_change_one_person(web):
    _admin(web)
    _sign_in_new("staff@slicks.test", "payables")
    uid = fetchone(_db(), "SELECT user_id FROM app_users WHERE email = 'staff@slicks.test'")[0]
    web.post(f"/settings/users/{uid}/", {"action": "role", "role": "viewer"})
    assert fetchone(_db(), "SELECT role FROM app_users WHERE user_id = ?", (uid,))[0] == "viewer"
    web.post(f"/settings/users/{uid}/", {"action": "off"})
    assert fetchone(_db(), "SELECT is_active FROM app_users WHERE user_id = ?", (uid,))[0] == 0
    r = web.post(f"/settings/users/{uid}/", {"action": "reset"})
    assert b"New temporary password for" in r.content
    me = fetchone(_db(), "SELECT user_id FROM app_users WHERE email = 'admin@slicks.test'")[0]
    web.post(f"/settings/users/{me}/", {"action": "off"})
    assert fetchone(_db(), "SELECT is_active FROM app_users WHERE user_id = ?", (me,))[0] == 1         # never yourself


def test_who_can_do_what_is_gone(web):
    # owner, 2026-10-09: "i do not need this" -- each person's page says what they may do
    _admin(web)
    tabs = web.get("/settings/").content.decode()
    assert 'href="/settings/users/"' in tabs and 'href="/settings/access/"' not in tabs
    assert web.get("/settings/access/").status_code == 404


def test_home_tiles_no_longer_say_later_for_d1_pages(web):
    _admin(web)
    page = web.get("/").content.decode()
    creditors_tile = page.split('href="/creditors/"')[1].split("</a>")[0]
    assert "later" not in creditors_tile
