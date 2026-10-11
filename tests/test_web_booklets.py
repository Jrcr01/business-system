"""Check booklet management (owner, 2026-10-09: "add, delete, update, run audit gap"; "it must generate checkbooklet code to account proper sequencing";
"assigned checkbooklet for example me or to someone"; started 2026-10-11): the code is generated (BK-0001 ...), a booklet that does not follow the last
one asks first and is flagged, a booklet has a holder and a history of hands, the range changes only around the leaves in use, a booklet with a leaf
used is archived and never deleted, and the gap audit names skipped numbers and duplicates."""
import sqlite3
from datetime import date

from conftest import fetchone, make_creditor, make_statement
from tests.test_web_d0 import _admin, _sign_in_new, fetchone_uid


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _n(sql, params=()):
    return fetchone(_db(), sql, params)[0]


def _add(web, start, end, **extra):
    return web.post("/booklets/", {"action": "add", "start": str(start), "end": str(end), "received": "2026-10-01", "holder": "", **extra})


def _check(db, number, status="Active", booklet_id=None):
    cid = _n("SELECT creditor_id FROM creditors LIMIT 1") if _n("SELECT COUNT(*) FROM creditors") else make_creditor(db, "Acme Tires")
    sid = _n("SELECT statement_id FROM order_statements LIMIT 1") if _n("SELECT COUNT(*) FROM order_statements") else make_statement(db, cid)
    with sqlite3.connect(db) as conn:
        conn.execute("""INSERT INTO checks_issued (creditor_id, bank_account_id, statement_id, booklet_id, check_number, check_date, amount, status)
                        VALUES (?, 1, ?, ?, ?, '2026-11-01', 1000, ?)""", (cid, sid, booklet_id, str(number), status))


def test_codes_are_generated_in_order_and_never_reused(web, db):
    from engines.booklet_rules import delete_booklet, next_code
    _admin(web)
    page = web.get("/booklets/").content.decode()
    assert "will be BK-0001" in page and "No booklet registered yet" in page
    _add(web, 4401, 4450)
    _add(web, 4451, 4500)
    assert [r[0] for r in sqlite3.connect(_db()).execute("SELECT booklet_code FROM check_booklets ORDER BY booklet_id")] == ["BK-0001", "BK-0002"]
    page = web.get("/booklets/").content.decode()
    assert "BK-0001" in page and "BK-0002" in page and "will be BK-0003" in page
    assert 'name="start" value="4501"' in page and 'name="end" value="4550"' in page       # the next one usually follows, with as many leaves
    second = _n("SELECT booklet_id FROM check_booklets WHERE booklet_code = 'BK-0002'")
    assert delete_booklet(second)[0] and next_code() == "BK-0003"                           # a deleted booklet's code is not used again
    assert _n("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'BOOKLET_ADDED'") == 2 and _n("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'BOOKLET_DELETED'") == 1


def test_a_booklet_that_does_not_follow_asks_first_and_is_flagged(web, db):
    _admin(web)
    _add(web, 4401, 4500)
    r = _add(web, 4601, 4650)
    page = r.content.decode()
    assert r.status_code == 200 and "Numbers in no booklet" in page and "4501–4600" in page and "Add anyway" in page
    assert _n("SELECT COUNT(*) FROM check_booklets") == 1                                    # nothing saved yet
    _add(web, 4601, 4650, confirm_gap="1")
    assert _n("SELECT COUNT(*) FROM check_booklets") == 2 and _n("SELECT COUNT(*) FROM review_flags WHERE kind = 'BOOKLET_SEQUENCE_GAP'") == 1
    assert "4501–4600 are in no booklet" in web.get("/booklets/").content.decode()        # the list shows the hole between the two
    r = web.post("/booklets/", {"action": "add", "start": "4640", "end": "4700", "received": "2026-10-01", "holder": ""}, follow=True)
    assert "Overlaps BK-0002" in r.content.decode() and _n("SELECT COUNT(*) FROM check_booklets") == 2      # an overlap is refused, never asked


def test_a_booklet_has_a_holder_and_a_history_of_hands(web, db):
    _admin(web)
    brother = _sign_in_new("julian@slicks.test", "owner")
    uid = fetchone_uid("julian@slicks.test")
    _add(web, 4401, 4450, holder="other", holder_name="Mama")
    _add(web, 4451, 4500, holder=str(uid))
    bk1 = _n("SELECT booklet_id FROM check_booklets WHERE booklet_code = 'BK-0001'")
    assert fetchone(_db(), "SELECT holder_user_id, holder_name FROM check_booklets WHERE booklet_id = ?", (bk1,)) == (None, "Mama")
    page = web.get("/booklets/").content.decode()
    assert "Mama" in page and "Julian" in page
    mine = brother.get("/booklets/?mine=1").content.decode()
    assert "BK-0002" in mine and "<b>BK-0001</b>" not in mine                               # Mine = the booklets I hold
    web.post("/booklets/", {"action": "hand_over", "booklet_id": bk1, "holder": str(uid), "holder_name": ""})
    assert fetchone(_db(), "SELECT holder_user_id, holder_name FROM check_booklets WHERE booklet_id = ?", (bk1,)) == (uid, None)
    web.post("/booklets/", {"action": "hand_over", "booklet_id": bk1, "holder": "other", "holder_name": "Mama"})
    notes = [r[0] for r in sqlite3.connect(_db()).execute("SELECT new_values FROM audit_logs WHERE user_action = 'BOOKLET_HANDED_OVER' ORDER BY rowid")]
    assert len(notes) == 2 and "held by Julian" in notes[0] and "held by Mama" in notes[1]


def test_the_number_picker_names_the_holder_and_puts_mine_first(web, db):
    from django.test import RequestFactory

    from payables.batch_checks import booklet_plan
    _admin(web)
    uid = fetchone_uid("admin@slicks.test")
    _add(web, 4401, 4450, holder="other", holder_name="Mama")
    _add(web, 4451, 4500, holder=str(uid))
    req = RequestFactory().get("/")
    req.slicks_user = {"email": "admin@slicks.test", "role": "admin"}
    books = booklet_plan(req, [])["booklets"]
    assert [b["code"] for b in books] == ["BK-0002 · Admin", "BK-0001 · Mama"] and books[0]["mine"]


def test_the_range_changes_only_around_the_leaves_in_use(web, db):
    _admin(web)
    _add(web, 4401, 4450)
    bid = _n("SELECT booklet_id FROM check_booklets")
    _check(db, 4405)
    edit = {"action": "edit", "booklet_id": bid, "received": "2026-10-02", "status": "Active"}
    web.post("/booklets/", {**edit, "start": "4401", "end": "4460"})                          # extending the last number works
    assert fetchone(_db(), "SELECT start_number, end_number, date_received FROM check_booklets") == (4401, 4460, "2026-10-02")
    r = web.post("/booklets/", {**edit, "start": "4401", "end": "4404"}, follow=True)         # shrinking past a used leaf is refused
    assert "Leaves already used would fall outside" in r.content.decode() and _n("SELECT end_number FROM check_booklets") == 4460
    r = web.post("/booklets/", {**edit, "start": "4410", "end": "4460"}, follow=True)         # and so is moving the first number past it
    assert "Leaves already used would fall outside" in r.content.decode() and _n("SELECT start_number FROM check_booklets") == 4401
    assert _n("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'BOOKLET_CHANGED'") == 1 and _n("SELECT booklet_code FROM check_booklets") == "BK-0001"


def test_delete_only_an_unused_booklet_otherwise_archive(web, db):
    _admin(web)
    _add(web, 4401, 4450)
    _add(web, 4451, 4500)
    used = _n("SELECT booklet_id FROM check_booklets WHERE booklet_code = 'BK-0001'")
    unused = _n("SELECT booklet_id FROM check_booklets WHERE booklet_code = 'BK-0002'")
    _check(db, 4402)
    r = web.post("/booklets/", {"action": "delete", "booklet_id": used}, follow=True)
    assert "Archive it instead" in r.content.decode() and _n("SELECT COUNT(*) FROM check_booklets") == 2
    web.post("/booklets/", {"action": "delete", "booklet_id": unused})
    assert _n("SELECT COUNT(*) FROM check_booklets") == 1
    web.post("/booklets/", {"action": "archive", "booklet_id": used})
    assert _n("SELECT status FROM check_booklets") == "Archived" and _n("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'BOOKLET_ARCHIVED'") == 1
    assert "<b>BK-0001</b>" not in web.get("/booklets/").content.decode()                   # out of the list
    assert "<b>BK-0001</b>" in web.get("/booklets/?show=all").content.decode()              # unless archived ones are shown


def test_the_gap_audit_names_skipped_numbers_and_duplicates(web, db):
    _admin(web)
    _add(web, 4451, 4500)
    bid = _n("SELECT booklet_id FROM check_booklets")
    _check(db, 4451)
    _check(db, 4454)
    _check(db, 4455)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO spoiled_checks (bank_account_id, check_number, reason) VALUES (1, '4452', 'torn')")
    page = web.get("/booklets/").content.decode()
    assert "1 booklet needs a look" in page and "BK-0001: 1 skipped number" in page         # the strip on top
    audit = web.get(f"/booklets/?audit={bid}").content.decode()
    assert "Gap audit · BK-0001" in audit and "Skipped:</b> 4453" in audit and "find 4453" in audit and "Leaf by leaf" in audit
    x = web.get(f"/booklets/{bid}/audit.xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    web.post("/booklets/", {"action": "spoiled", "booklet_id": bid, "numbers": "4453", "reason": "lost"})
    after = web.get("/booklets/").content.decode()
    assert "All booklets accounted for" in after and _n("SELECT COUNT(*) FROM spoiled_checks") == 2


def test_up_to_number_shows_the_leaves_used_but_not_recorded(web, db):
    _admin(web)
    _add(web, 4451, 4500, holder="other", holder_name="Mama")
    bid = _n("SELECT booklet_id FROM check_booklets")
    _check(db, 4451)
    web.post("/booklets/", {"action": "up_to", "booklet_id": bid, "number": "4454"})
    page = web.get("/booklets/").content.decode()
    assert _n("SELECT reported_up_to FROM check_booklets") == 4454
    assert "3 used, not recorded" in page and "Mama's BK-0001: 3 leaves used but not recorded" in page


def test_see_only_shows_the_page_and_refuses_saves_and_the_switch_follows_bank_once(web, db):
    from engines.auth_service import set_restrictions, split_new_switches
    from engines.permissions import level
    from engines.settings_store import set_setting
    _admin(web)
    _add(web, 4401, 4450)
    family = _sign_in_new("family@slicks.test", "owner")
    uid = fetchone_uid("family@slicks.test")
    set_restrictions(uid, {"booklets.use"})
    page = family.get("/booklets/").content.decode()
    assert "BK-0001" in page and "Add a booklet" not in page and "Change</a>" not in page   # sees the list and the audit, no forms
    family.post("/booklets/", {"action": "add", "start": "4451", "end": "4500", "received": "2026-10-01", "holder": ""})
    assert _n("SELECT COUNT(*) FROM check_booklets") == 1
    set_restrictions(uid, {"bank.use"})                                                    # Bank off before the split: Check booklets starts the same
    set_setting("switches_split_booklets_2026_10_11", "0")
    assert split_new_switches() >= 1
    assert {r[0] for r in sqlite3.connect(_db()).execute("SELECT action FROM user_restrictions WHERE user_id = ?", (uid,))} >= {"bank.use", "booklets.use"}
    assert level("payables", "booklets.use") == "Y" and level("purchasing", "booklets.use") != "Y"


def test_existing_booklets_get_their_codes_once_in_check_number_order(db):
    from engines.booklet_rules import renumber_codes
    with sqlite3.connect(db) as conn:
        for code, start in (("B-4451", 4451), ("Mom's", 4401)):
            conn.execute("INSERT INTO check_booklets (bank_account_id, booklet_code, start_number, end_number, next_available_number, date_received) VALUES (1, ?, ?, ?, ?, ?)",
                         (code, start, start + 49, start, str(date(2026, 9, 1))))
    assert renumber_codes() == 2 and renumber_codes() == 0                                   # once
    assert [r[0] for r in sqlite3.connect(db).execute("SELECT booklet_code FROM check_booklets ORDER BY start_number")] == ["BK-0001", "BK-0002"]
    old = {r[0]: r[1] for r in sqlite3.connect(db).execute("SELECT new_values, old_values FROM audit_logs WHERE user_action = 'BOOKLET_CODE_SET'")}
    assert old == {"BK-0001": "Mom's", "BK-0002": "B-4451"}


def test_the_menu_and_home_have_check_booklets(web, db):
    _admin(web)
    home = web.get("/").content.decode()
    assert 'href="/booklets/"' in home and "Check booklets" in home
    assert 'href="/booklets/"' in web.get("/bank-cash/").content.decode()                   # where the Booklets tab was


def test_a_spoiled_leaf_can_be_taken_back_with_a_reason(web, db):
    # owner, 2026-10-12: "is there no way to undo spoiled check?" -- now there is, on the booklet's audit, reason required
    from conftest import fetchone
    import engines.db_engine as d
    from tests.test_web_d0 import _admin
    _admin(web)
    web.post("/booklets/", {"action": "add", "start": "3992554201", "end": "3992554300", "received": "2026-10-01"})
    bid = fetchone(d.DB_PATH, "SELECT booklet_id FROM check_booklets ORDER BY booklet_id DESC LIMIT 1")[0]
    web.post("/booklets/", {"action": "spoiled", "booklet_id": bid, "numbers": "3992554205", "reason": "torn"})
    assert fetchone(d.DB_PATH, "SELECT COUNT(*) FROM spoiled_checks")[0] == 1
    r = web.post("/booklets/", {"action": "unspoil", "booklet_id": bid, "number": "3992554205", "reason": ""}, follow=True)
    assert fetchone(d.DB_PATH, "SELECT COUNT(*) FROM spoiled_checks")[0] == 1          # no reason, no undo
    r = web.post("/booklets/", {"action": "unspoil", "booklet_id": bid, "number": "3992554205", "reason": "it was fine after all"}, follow=True)
    assert b"open again" in r.content
    assert fetchone(d.DB_PATH, "SELECT COUNT(*) FROM spoiled_checks")[0] == 0
    assert fetchone(d.DB_PATH, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CHECK_UNSPOILED'")[0] == 1
    assert b"Not spoiled" not in web.get(f"/booklets/?audit={bid}").content            # nothing spoiled, no button
