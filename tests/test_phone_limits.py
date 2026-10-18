"""What a phone or an iPad may do with the laptop pages (owner, 2026-10-17: "1 limit everyone 2 yes, go", "for laptop only then unshow to mobile",
"hidden, go"; mock-ups ai_phone_limits, aj_phone_home): laptop-only pages left out of the menu and Home and saying so when opened; look-only pages
without their forms and refusing changes; everything else as on the laptop; a laptop (any width) untouched. The System admin has everything on a phone
(owner, 2026-10-18: "make available for system admin all functions as for computer or browser")."""
from tests.test_phone_approval import IPAD_UA, LAPTOP_UA, PHONE_UA
from tests.test_web_d0 import _admin, _sign_in_new


def _owner(web):
    _admin(web)
    return _sign_in_new("owner@slicks.test", "owner")


def test_laptop_only_pages_are_hidden_on_a_phone_and_say_so_when_opened(web, db):
    web = _owner(web)
    home = web.get("/", HTTP_USER_AGENT=PHONE_UA).content.decode()
    assert 'href="/settings/' not in home and 'href="/creditors/' not in home and 'href="/booklets/' not in home      # menu and Home: left out
    assert "look</span>" in home                                                                                          # look-only pages marked
    assert "Creditors is done on the laptop" in web.get("/creditors/", HTTP_USER_AGENT=PHONE_UA).content.decode()
    laptop = web.get("/", HTTP_USER_AGENT=LAPTOP_UA).content.decode()
    assert 'href="/creditors/' in laptop and "look</span>" not in laptop                                                 # a laptop: as before
    assert "is done on the laptop" not in web.get("/creditors/", HTTP_USER_AGENT=LAPTOP_UA).content.decode()


def test_look_only_pages_show_but_refuse_changes_on_a_phone(web, db):
    web = _owner(web)
    page = web.get("/purchase-orders/", HTTP_USER_AGENT=PHONE_UA).content.decode()
    assert "Looking only on the phone." in page and 'href="/phone/in/"' in page and 'class="view-only"' in page
    r = web.post("/purchase-orders/", {"action": "anything"}, HTTP_USER_AGENT=PHONE_UA, follow=True)
    assert "is for looking. Do this on the laptop." in r.content.decode()
    assert "Looking only on the phone." not in web.get("/purchase-orders/", HTTP_USER_AGENT=LAPTOP_UA).content.decode()
    assert "Looking only on the phone." not in web.get("/expenses/", HTTP_USER_AGENT=PHONE_UA).content.decode()        # as now on the phone


def test_an_ipad_is_limited_like_a_phone_a_mac_is_not(web, db):
    web = _owner(web)
    assert "is done on the laptop" not in web.get("/creditors/", HTTP_USER_AGENT=IPAD_UA).content.decode()          # a Mac
    web.cookies["slk_ipad"] = "1"
    assert "Creditors is done on the laptop" in web.get("/creditors/", HTTP_USER_AGENT=IPAD_UA).content.decode()     # the iPad


def test_invoices_can_be_added_and_edited_on_a_phone_but_not_paid_or_deleted(web, db):
    """Owner, 2026-10-17: "can also add editing?" -- Invoices on a phone: add and edit; Make checks and Delete on the laptop."""
    from conftest import make_creditor
    web = _owner(web)
    cid = make_creditor(db, "Sunny Tire Supply", terms="30")
    page = web.get("/order-statements/invoices/", HTTP_USER_AGENT=PHONE_UA).content.decode()
    assert "On the phone: add and edit invoices" in page and "Looking only on the phone." not in page and 'name="action" value="add"' in page
    r = web.post("/order-statements/invoices/", {"action": "add", "creditor": cid, "si": "77001", "dr": "", "date": "2026-10-17", "gross": "1,000", "less": ""},
                 HTTP_USER_AGENT=PHONE_UA, follow=True)
    assert "On a phone this is done on the laptop." not in r.content.decode()
    r = web.post("/order-statements/invoices/", {"action": "pay", "pick": "1"}, HTTP_USER_AGENT=PHONE_UA, follow=True)
    assert "On a phone this is done on the laptop." in r.content.decode()
    r = web.post("/order-statements/invoices/", {"action": "delete", "invoice_id": "1"}, HTTP_USER_AGENT=PHONE_UA, follow=True)
    assert "On a phone this is done on the laptop." in r.content.decode()
    assert "Record an invoice" in web.get("/", HTTP_USER_AGENT=PHONE_UA).content.decode()                                # its Home card stays
    assert "Looking only on the phone." in web.get("/order-statements/", HTTP_USER_AGENT=PHONE_UA).content.decode()   # Payment statements: look


def test_the_system_admin_has_every_function_on_a_phone(web, db):
    _admin(web)
    home = web.get("/", HTTP_USER_AGENT=PHONE_UA).content.decode()
    assert 'href="/settings/' in home and 'href="/creditors/' in home and "look</span>" not in home
    assert "is done on the laptop" not in web.get("/settings/", HTTP_USER_AGENT=PHONE_UA).content.decode()
    assert "Looking only on the phone." not in web.get("/purchase-orders/", HTTP_USER_AGENT=PHONE_UA).content.decode()
    r = web.post("/order-statements/invoices/", {"action": "delete", "invoice_id": "1"}, HTTP_USER_AGENT=PHONE_UA, follow=True)
    assert "On a phone this is done on the laptop." not in r.content.decode()
