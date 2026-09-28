"""Slicks ERP core step C3 (2026-09-28): the shared party list. Owner's choices: one list, several roles per party; Sales adds customers
and edits contact / address, only the owner sets credit / terms / affiliate / active; one Walk-in customer; every AP creditor linked
read-only (same name = one party); Sales sees customers and affiliates only; Viewer none."""
import sqlite3

import pytest

pytest.importorskip("django", reason="Django is installed only in the ERP environment (.venv)")

PASSWORD = "sandbox-Test-pass-42"
_SINCE = {"log_id": 0}


@pytest.fixture
def c3(erp):
    """Fresh users; parties and links from earlier tests are left (they can't be deleted through the rules, like real data), so each
    test uses its own names. Returns the users and the database path."""
    from django.contrib.sessions.models import Session

    from core.models import Role, User

    Session.objects.all().delete()
    for role in (Role.OWNER, Role.SALES, Role.VIEWER):
        User.objects.filter(email=f"{role}@c3.example.com").delete()
    users = {role: User.objects.create_user(f"{role}@c3.example.com", f"{role.title()} C3", role, PASSWORD, must_change_password=False)
             for role in (Role.OWNER, Role.SALES, Role.VIEWER)}
    _mark()
    return users, erp


def _mark():
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(log_id), 0) FROM audit_logs")
        _SINCE["log_id"] = cur.fetchone()[0]


def _audit(action):
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute("SELECT user_email, entity_id, old_values, new_values FROM audit_logs WHERE user_action = %s AND log_id > %s "
                    "ORDER BY log_id", [action, _SINCE["log_id"]])
        return cur.fetchall()


def _client(user):
    from django.test import Client

    client = Client()
    client.force_login(user)
    return client


def _creditor(db, name, kind="SUPPLIER"):
    conn = sqlite3.connect(db)
    cur = conn.execute("INSERT INTO creditors (creditor_name, creditor_type, terms) VALUES (?, ?, '30')", (name, kind))
    conn.commit()
    conn.close()
    return cur.lastrowid


def _party(name, **kw):
    from core.models import Party
    from core.parties import name_key

    return Party.objects.create(name=name, name_key=name_key(name), **kw)


# ---- AP link ------------------------------------------------------------------------------------------------------------------

def test_every_ap_creditor_is_linked_once_and_same_names_share_a_party(c3):
    from core.models import PartyAPLink
    from core.parties import roles, sync_ap_links

    users, db = c3
    loan = _creditor(db, "Sample Bank C3", "BANK_LOAN")
    card = _creditor(db, "sample bank c3 ", "CREDIT_CARD")         # same name, other case / spaces
    sup = _creditor(db, "Rubber Co C3")
    sync_ap_links()
    assert sync_ap_links() == 0                                     # nothing left to link
    a, b, c = (PartyAPLink.objects.get(creditor_id=i).party for i in (loan, card, sup))
    assert a == b and a != c
    assert roles(a) == ["Bank loan", "Card"] and roles(c) == ["Supplier"]
    assert len(_audit("PARTY_AP_LINKED")) >= 3


def test_a_new_ap_creditor_appears_when_the_owner_opens_parties(c3):
    from core.models import PartyAPLink

    users, db = c3
    _client(users["owner"]).get("/parties/")
    new = _creditor(db, "Fresh Supplier C3")
    assert not PartyAPLink.objects.filter(creditor_id=new).exists()
    html = _client(users["owner"]).get("/parties/", {"q": "Fresh Supplier C3"}).content.decode()
    assert "Fresh Supplier C3" in html and PartyAPLink.objects.filter(creditor_id=new).exists()


def test_owner_can_separate_a_wrong_same_name_match(c3):
    from core.models import PartyAPLink
    from core.parties import sync_ap_links

    users, db = c3
    cust = _party("Twin Name C3", is_customer=True)
    cr = _creditor(db, "Twin Name C3")
    sync_ap_links()
    assert PartyAPLink.objects.get(creditor_id=cr).party == cust
    resp = _client(users["owner"]).post(f"/parties/{cust.pk}/unlink/{cr}/")
    moved = PartyAPLink.objects.get(creditor_id=cr).party
    assert moved != cust and resp["Location"] == f"/parties/{moved.pk}/" and _audit("PARTY_AP_MOVED")


# ---- walk-in ------------------------------------------------------------------------------------------------------------------

def test_there_is_one_walk_in_and_it_cannot_be_changed(c3):
    from django.db import IntegrityError, transaction

    from core.models import Party

    users, _ = c3
    walk_in = Party.objects.get(is_walk_in=True)
    owner = _client(users["owner"])
    owner.post(f"/parties/{walk_in.pk}/details/", {"name": "Renamed", "contact_number": "", "address": ""})
    owner.post(f"/parties/{walk_in.pk}/credit/", {"credit_allowed": "on", "terms_days": "30"})
    owner.post(f"/parties/{walk_in.pk}/active/", {"active": "0"})
    walk_in.refresh_from_db()
    assert walk_in.name == "Walk-in customer" and not walk_in.credit_allowed and walk_in.is_active
    with pytest.raises(IntegrityError), transaction.atomic():
        _party("Second walk-in C3", is_walk_in=True)


# ---- who sees and does what ---------------------------------------------------------------------------------------------------

def test_sales_sees_customers_and_affiliates_only(c3):
    from core.parties import sync_ap_links

    users, db = c3
    cust = _party("Loyal Buyer C3", is_customer=True)
    aff = _party("Tita Branch C3", is_affiliate=True)
    _creditor(db, "Hidden Supplier C3")
    sync_ap_links()
    from core.models import PartyAPLink

    sup = PartyAPLink.objects.get(creditor__creditor_name="Hidden Supplier C3").party
    sales = _client(users["sales"])
    html = sales.get("/parties/").content.decode()
    assert "Loyal Buyer C3" in html and "Tita Branch C3" in html and "Hidden Supplier C3" not in html
    assert "show=suppliers" not in html                              # filter not offered
    assert "Hidden Supplier C3" not in sales.get("/parties/", {"show": "suppliers"}).content.decode()
    assert sales.get(f"/parties/{sup.pk}/").status_code == 404
    assert sales.get(f"/parties/{cust.pk}/").status_code == 200 and sales.get(f"/parties/{aff.pk}/").status_code == 200


def test_sales_adds_customers_and_edits_contact_but_not_credit(c3):
    from core.models import Party

    users, _ = c3
    sales = _client(users["sales"])
    resp = sales.post("/parties/add/", {"name": "  New  Buyer C3 ", "contact_number": "0917 000 0001", "address": "Tacloban"})
    p = Party.objects.get(name="New Buyer C3")
    assert resp["Location"] == f"/parties/{p.pk}/" and p.is_customer and not p.credit_allowed
    assert _audit("PARTY_ADDED")[0][0] == "sales@c3.example.com"
    sales.post(f"/parties/{p.pk}/details/", {"name": "New Buyer C3", "contact_number": "0917 000 0002", "address": "Palo"})
    p.refresh_from_db()
    assert (p.contact_number, p.address) == ("0917 000 0002", "Palo")
    who, _, old, new = _audit("PARTY_EDITED")[0]
    assert who == "sales@c3.example.com" and "0917 000 0001" in old and "Palo" in new
    for path, data in ((f"/parties/{p.pk}/credit/", {"credit_allowed": "on", "terms_days": "30"}),
                       (f"/parties/{p.pk}/active/", {"active": "0"})):
        assert sales.post(path, data).status_code == 403
    p.refresh_from_db()
    assert not p.credit_allowed and p.is_active
    assert "Credit and roles" not in sales.get(f"/parties/{p.pk}/").content.decode()


def test_viewer_cannot_open_parties(c3):
    users, _ = c3
    viewer = _client(users["viewer"])
    assert viewer.get("/parties/").status_code == 403 and viewer.get("/parties/add/").status_code == 403


def test_the_same_name_cannot_be_added_twice(c3):
    from core.models import Party

    users, _ = c3
    _party("Double Check Tires C3", is_customer=True)
    sales = _client(users["sales"])
    resp = sales.post("/parties/add/", {"name": "double  check tires c3.", "contact_number": "", "address": ""})
    assert "already on the list" in resp.content.decode()
    other = _party("Other Name C3", is_customer=True)
    sales.post(f"/parties/{other.pk}/details/", {"name": "DOUBLE CHECK TIRES C3", "contact_number": "", "address": ""})
    other.refresh_from_db()
    assert other.name == "Other Name C3"
    assert Party.objects.filter(name_key="double check tires c3").count() == 1


# ---- owner: credit, roles, active ---------------------------------------------------------------------------------------------

def test_owner_sets_credit_with_terms_and_it_is_audited(c3):
    users, _ = c3
    p = _party("Credit Buyer C3")
    owner = _client(users["owner"])
    owner.post(f"/parties/{p.pk}/credit/", {"credit_allowed": "on", "terms_days": ""})
    p.refresh_from_db()
    assert not p.credit_allowed                                        # terms required
    owner.post(f"/parties/{p.pk}/credit/", {"credit_allowed": "on", "terms_days": "30", "is_affiliate": "on"})
    p.refresh_from_db()
    assert p.credit_allowed and p.terms_days == 30 and p.is_affiliate and p.is_customer
    who, _, old, new = _audit("PARTY_CREDIT_CHANGED")[0]
    assert who == "owner@c3.example.com" and "credit_allowed: False" in old and "credit_allowed: True" in new
    owner.post(f"/parties/{p.pk}/credit/", {"terms_days": "30", "is_affiliate": "on"})       # credit off, terms kept
    p.refresh_from_db()
    assert not p.credit_allowed and p.terms_days == 30


def test_deactivated_parties_are_hidden_unless_asked(c3):
    users, _ = c3
    p = _party("Closed Shop C3", is_customer=True)
    owner = _client(users["owner"])
    owner.post(f"/parties/{p.pk}/active/", {"active": "0"})
    names = lambda resp: [r["p"].name for r in resp.context["rows"]]
    assert "Closed Shop C3" not in names(owner.get("/parties/", {"q": "Closed Shop"}))
    assert "Closed Shop C3" in names(owner.get("/parties/", {"q": "Closed Shop", "inactive": "1"}))
    assert _audit("PARTY_DEACTIVATED")
    owner.post(f"/parties/{p.pk}/active/", {"active": "1"})
    p.refresh_from_db()
    assert p.is_active and _audit("PARTY_REACTIVATED")


def test_search_and_role_filters(c3):
    users, db = c3
    _party("Filter Customer C3", is_customer=True, contact_number="0999 123 4567")
    _creditor(db, "Filter Lender C3", "LENDER")
    owner = _client(users["owner"])
    owner.get("/parties/")
    assert "Filter Customer C3" in owner.get("/parties/", {"q": "0999 123"}).content.decode()
    lenders = owner.get("/parties/", {"show": "lenders"}).content.decode()
    assert "Filter Lender C3" in lenders and "Filter Customer C3" not in lenders
