"""Owners see every module (owner, 2026-10-09: "since we are all owners, i want to give them view access to all modules"); what they may change stays per
their switches, and Set-up stays the System admin's."""
from tests.test_web_d0 import _admin, _sign_in_new, fetchone_uid


def test_an_owner_with_switches_off_still_sees_every_module_read_only(web):
    from engines.auth_service import set_restrictions
    from engines.permissions import SWITCHES
    _admin(web)
    family = _sign_in_new("family@slicks.test", "owner")
    set_restrictions(fetchone_uid("family@slicks.test"), set(SWITCHES))          # everything switched off
    home = family.get("/").content.decode()
    for url in ("/purchase-orders/", "/order-statements/", "/checks/", "/calendar/", "/bank-cash/", "/lenders/", "/bank-loans/", "/credit-cards/",
                "/reports/", "/analytics/", "/review/", "/creditors/", "/price-lists/"):
        r = family.get(url)
        assert r.status_code == 200, url
        page = r.content.decode()
        assert "View only" in page and 'class="view-only"' in page, url
        assert f'href="{url}' in home or url in ("/review/",), url                  # on the menu too
    r = family.post("/lenders/", {"action": "new_lender", "name": "Someone"})
    assert r.status_code == 302 and "view only" in family.get(r["Location"]).content.decode().lower()
    assert family.get("/search/?q=00").status_code == 200                         # search only reads
    for url in ("/settings/", "/settings/users/", "/settings/audit/"):
        assert family.get(url).status_code in (302, 403), url                    # Set-up stays the System admin's


def test_a_switched_on_page_is_not_view_only(web):
    _admin(web)
    family = _sign_in_new("family@slicks.test", "owner")
    page = family.get("/bank-cash/").content.decode()
    assert "View only" not in page and 'class="view-only"' not in page
