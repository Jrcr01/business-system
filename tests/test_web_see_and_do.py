"""What a person sees and what they do (owner, 2026-10-09: "i want to control what they can see, what they can do"): per page Hidden / See / See and do,
with the page's extra actions; Hidden = gone everywhere (menu, Home, search, the page's address)."""
from tests.conftest import fetchone, make_creditor
from tests.test_web_d0 import _admin, _sign_in_new, fetchone_uid


def levels_post(levels: dict, extras=(), default: str = "do") -> dict:
    """The person's page as posted: every row at `default` unless named; the extras ticked."""
    from engines.permissions import ACCESS_ROWS
    out = {"action": "access", "x": list(extras)}
    for _, rows in ACCESS_ROWS:
        for page, _, acts, _ in rows:
            lv = levels.get(page, default)
            out[f"lv_{page}"] = "see" if (lv == "do" and not acts) else lv
    return out


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def test_hidden_see_and_do(web, db):
    _admin(web)
    make_creditor(db, "Goodyear Philippines")
    family = _sign_in_new("family@slicks.test", "owner")
    uid = fetchone_uid("family@slicks.test")
    r = web.post(f"/settings/users/{uid}/", levels_post({"bank-cash": "hidden", "lenders": "see", "search": "hidden"}), follow=True)
    assert b"saved" in r.content
    # Hidden: gone everywhere
    assert family.get("/bank-cash/").status_code == 403
    home = family.get("/").content.decode()
    assert 'href="/bank-cash/' not in home                                      # no menu entry, no Home card
    assert 'class="tb-search"' not in home and family.get("/search/?q=Good").status_code == 403
    # See: view only, saves refused
    page = family.get("/lenders/").content.decode()
    assert "View only" in page
    family.post("/lenders/", {"action": "new_lender", "name": "Someone"})
    assert fetchone(_db(), "SELECT COUNT(*) FROM creditors WHERE creditor_name = 'Someone'")[0] == 0
    # See and do: works
    assert "View only" not in family.get("/bank-loans/").content.decode()
    assert fetchone(_db(), "SELECT user_action FROM audit_logs WHERE entity_name = 'app_users' ORDER BY log_id DESC LIMIT 1")[0] == "USER_ACCESS_CHANGED"
    # the person's page shows the choices back
    page = web.get(f"/settings/users/{uid}/").content.decode()
    assert 'name="lv_bank-cash" value="hidden" checked' in page and 'name="lv_lenders" value="see" checked' in page


def test_lenders_do_while_bank_loans_only_see(web):
    _admin(web)
    family = _sign_in_new("family@slicks.test", "owner")
    uid = fetchone_uid("family@slicks.test")
    web.post(f"/settings/users/{uid}/", levels_post({"bank-loans": "see"}))
    assert "View only" not in family.get("/lenders/").content.decode()
    assert "View only" in family.get("/bank-loans/").content.decode()
    from engines.auth_service import get_restrictions
    off = get_restrictions(uid)
    assert "bankloans.use" in off and "loans.use" not in off


def test_extras_count_only_under_see_and_do(web):
    from engines.auth_service import get_restrictions
    _admin(web)
    _sign_in_new("family@slicks.test", "owner")
    uid = fetchone_uid("family@slicks.test")
    web.post(f"/settings/users/{uid}/", levels_post({}, extras=["receive"]))         # PO on do: only Receive goods ticked
    off = get_restrictions(uid)
    assert "po.receive_goods" not in off and {"po.receive", "po.cancel", "po.ack_over_plan"} <= off
    web.post(f"/settings/users/{uid}/", levels_post({"purchase-orders": "see"}, extras=["receive", "cancel_po"]))
    off = get_restrictions(uid)
    assert {"po.create", "po.order", "po.receive_goods", "po.cancel"} <= off                  # see only: the ticks don't count


def test_a_hidden_page_drops_out_of_search_results(web, db):
    _admin(web)
    make_creditor(db, "Goodyear Philippines")
    family = _sign_in_new("family@slicks.test", "owner")
    uid = fetchone_uid("family@slicks.test")
    page = family.get("/search/?q=Goodyear").content.decode()
    assert "/creditors/" in page
    web.post(f"/settings/users/{uid}/", levels_post({"creditors": "hidden"}))
    page = family.get("/search/?q=Goodyear").content.decode()
    assert "/creditors/" not in page


def test_presets_and_copy_set_both(web):
    from engines.auth_service import get_hidden_pages
    from engines.permissions import ACCESS_PRESETS
    _admin(web)
    _sign_in_new("family@slicks.test", "owner")
    _sign_in_new("cousin@slicks.test", "owner")
    uid, cousin = fetchone_uid("family@slicks.test"), fetchone_uid("cousin@slicks.test")
    p = ACCESS_PRESETS["purchasing"][1]
    web.post(f"/settings/users/{uid}/", levels_post(p["levels"], p["extras"]))
    assert "bank-cash" in get_hidden_pages(uid) and "purchase-orders" not in get_hidden_pages(uid)
    page = web.get(f"/settings/users/{cousin}/?copy={uid}").content.decode()
    assert "Showing <b>Family</b>" in page and 'name="lv_bank-cash" value="hidden" checked' in page
