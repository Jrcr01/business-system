"""Slicks ERP core step C4 (2026-09-28): carve-out tags. Owner's choices: Business / Rental / Family-personal + own sub-tags; records never
tagged = Business · General; one main tag + peso parts moved to other tags (the parts always add up); sub-tags never deleted; owner only.
AP records are pointed at, never changed; card numbers never beyond the last 4 digits."""
import sqlite3
from decimal import Decimal

import pytest

pytest.importorskip("django", reason="Django is installed only in the ERP environment (.venv)")

PASSWORD = "sandbox-Test-pass-42"
_SINCE = {"log_id": 0}
MONTH = "2031-04"


@pytest.fixture
def c4(erp):
    from core.models import Role, User

    for role in (Role.OWNER, Role.SALES, Role.VIEWER):
        User.objects.filter(email=f"{role}@c4.example.com").delete()
    users = {role: User.objects.create_user(f"{role}@c4.example.com", f"{role.title()} C4", role, PASSWORD, must_change_password=False)
             for role in (Role.OWNER, Role.SALES, Role.VIEWER)}
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(log_id), 0) FROM audit_logs")
        _SINCE["log_id"] = cur.fetchone()[0]
    return users, erp


def _audit(action):
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute("SELECT user_email, entity_id, old_values, new_values FROM audit_logs WHERE user_action = %s AND log_id > %s ORDER BY log_id",
                    [action, _SINCE["log_id"]])
        return cur.fetchall()


def _client(user):
    from django.test import Client

    client = Client()
    client.force_login(user)
    return client


def _card_statement(db, name, amount, month=MONTH):
    conn = sqlite3.connect(db)
    cr = conn.execute("INSERT INTO creditors (creditor_name, creditor_type) VALUES (?, 'CREDIT_CARD')", (f"{name} Bank",)).lastrowid
    card = conn.execute("INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, card_number_full) VALUES (?, ?, '4412', "
                        "'4111111111114412')", (cr, name)).lastrowid
    sid = conn.execute("INSERT INTO cc_statements (card_id, statement_month, due_date, total_amount_due) VALUES (?, ?, ?, ?)",
                       (card, month, f"{month}-25", amount)).lastrowid
    conn.commit()
    conn.close()
    return sid


def _batch(db, ref, amount, day=f"{MONTH}-10 02:00:00"):
    conn = sqlite3.connect(db)
    cr = conn.execute("INSERT INTO creditors (creditor_name, creditor_type) VALUES (?, 'SUPPLIER')", (f"Supplier {ref}",)).lastrowid
    sid = conn.execute("INSERT INTO order_statements (creditor_id, statement_ref, total_amount, status, created_at) VALUES (?, ?, ?, 'Issued', ?)",
                       (cr, ref, amount, day)).lastrowid
    conn.commit()
    conn.close()
    return sid


def _tag(group, name):
    from core.models import Tag
    from core.parties import name_key

    return Tag.objects.get_or_create(group=group, name_key=name_key(name), defaults={"name": name})[0]


def _row(record_type, record_id, month=MONTH):
    from core import carveout

    return next(r for r in carveout.records(record_type, month) if r["id"] == record_id)


# ---- defaults and tagging -------------------------------------------------------------------------------------------------------

def test_three_groups_each_have_general_and_business_general_is_the_default(c4):
    from core.models import Tag

    general = {t.group: t for t in Tag.objects.filter(name_key="general")}
    assert set(general) == {"business", "rental", "family"}
    assert Tag.objects.get(is_default=True) == general["business"]


def test_an_untagged_record_is_business_general(c4):
    users, db = c4
    sid = _card_statement(db, "Untagged Card C4", 1234.50)
    r = _row("cc_statement", sid)
    assert [(t.name, t.group, a) for t, a in r["parts"]] == [("General", "business", Decimal("1234.50"))]
    assert "••4412" in r["label"] and "4111111111114412" not in r["label"]


def test_owner_sets_the_main_tag_and_it_is_audited(c4):
    users, db = c4
    sid = _card_statement(db, "School Card C4", 3200)
    school = _tag("family", "School C4")
    resp = _client(users["owner"]).post("/tag-records/main/", {"type": "cc_statement", "month": MONTH, "record_id": sid, "tag": school.pk})
    assert resp.status_code == 302
    assert _row("cc_statement", sid)["parts"] == [(school, Decimal("3200.00"))]
    who, rid, old, new = _audit("TAG_SET")[0]
    assert who == "owner@c4.example.com" and rid == sid and "never tagged" in old and "School C4" in new


def test_a_split_always_adds_up_and_follows_later_changes_in_ap(c4):
    from core import carveout
    from core.models import Tag

    users, db = c4
    sid = _card_statement(db, "Split Card C4", 10000)
    household = _tag("family", "Household C4")
    owner = _client(users["owner"])
    general = Tag.objects.get(is_default=True)
    owner.post(f"/tag-records/cc_statement/{sid}/", {"month": MONTH, "main": general.pk, "tag_0": household.pk, "amount_0": "2,000.00"})
    r = _row("cc_statement", sid)
    assert [(t.name, a) for t, a in r["parts"]] == [("General", Decimal("8000.00")), ("Household C4", Decimal("2000.00"))]
    assert _audit("TAG_SPLIT")

    conn = sqlite3.connect(db)                                          # AP corrects the statement to a higher total ...
    conn.execute("UPDATE cc_statements SET total_amount_due = 12000 WHERE cc_statement_id = ?", (sid,))
    conn.commit()
    r = _row("cc_statement", sid)
    assert r["parts"][0][1] == Decimal("10000.00") and not r["needs_look"]     # ... the main tag keeps the rest

    conn.execute("UPDATE cc_statements SET total_amount_due = 1500 WHERE cc_statement_id = ?", (sid,))
    conn.commit()
    conn.close()
    r = _row("cc_statement", sid)                                       # ... lower than the part moved: flagged
    assert r["needs_look"] and carveout.summary(MONTH)["flagged"] >= 1


@pytest.mark.parametrize("parts, message", [
    ({"tag_0": "HOUSE", "amount_0": "-5"}, "more than zero"),
    ({"tag_0": "HOUSE", "amount_0": "abc"}, "must be numbers"),
    ({"tag_0": "HOUSE", "amount_0": "100", "tag_1": "HOUSE", "amount_1": "100"}, "only once"),
    ({"tag_0": "MAIN", "amount_0": "100"}, "only once"),
    ({"tag_0": "HOUSE", "amount_0": "5000"}, "less than the total"),
    ({"tag_0": "HOUSE", "amount_0": ""}, "both a tag and an amount"),
])
def test_split_mistakes_are_refused(c4, parts, message):
    from core.models import Tag

    users, db = c4
    sid = _card_statement(db, f"Refused Card C4 {message[:6]}", 5000)
    house, main = _tag("family", "Household C4"), Tag.objects.get(is_default=True)
    data = {"month": MONTH, "main": main.pk}
    data.update({k: (house.pk if v == "HOUSE" else main.pk if v == "MAIN" else v) for k, v in parts.items()})
    resp = _client(users["owner"]).post(f"/tag-records/cc_statement/{sid}/", data, follow=True)
    text = resp.content.decode()
    assert message in text.replace("&#x27;", "'")
    assert _row("cc_statement", sid)["parts"] == [(main, Decimal("5000.00"))]


def test_a_zero_amount_counts_as_no_part(c4):
    from core.models import Tag

    users, db = c4
    sid = _batch(db, "ZERO-C4", 700)
    general = Tag.objects.get(is_default=True)
    _client(users["owner"]).post(f"/tag-records/batch/{sid}/", {"month": MONTH, "main": general.pk,
                                                                  "tag_0": _tag("family", "Household C4").pk, "amount_0": "0"})
    assert _row("batch", sid)["parts"] == [(general, Decimal("700.00"))]


def test_clearing_the_parts_undoes_a_split(c4):
    from core.models import Tag

    users, db = c4
    sid = _batch(db, "UNDO-C4", 900)
    rent, general = _tag("rental", "Stall 2 C4"), Tag.objects.get(is_default=True)
    owner = _client(users["owner"])
    owner.post(f"/tag-records/batch/{sid}/", {"month": MONTH, "main": general.pk, "tag_0": rent.pk, "amount_0": "300"})
    owner.post(f"/tag-records/batch/{sid}/", {"month": MONTH, "main": general.pk})
    assert _row("batch", sid)["parts"] == [(general, Decimal("900.00"))]


# ---- tags page ------------------------------------------------------------------------------------------------------------------

def test_sub_tags_are_added_renamed_and_deactivated_never_deleted(c4):
    from core.models import Tag

    users, db = c4
    owner = _client(users["owner"])
    owner.post("/tags/add/", {"group": "rental", "name": "Apartment A C4"})
    t = Tag.objects.get(group="rental", name="Apartment A C4")
    resp = owner.post("/tags/add/", {"group": "rental", "name": "apartment  a c4"}, follow=True)
    assert "already a sub-tag" in resp.content.decode()
    owner.post("/tags/add/", {"group": "family", "name": "Apartment A C4"})         # same name in another group is fine
    assert Tag.objects.filter(name="Apartment A C4").count() == 2
    owner.post(f"/tags/{t.pk}/", {"name": "Apartment B C4"})
    t.refresh_from_db()
    assert t.name == "Apartment B C4" and _audit("TAG_RENAMED")

    sid = _batch(db, "DEACT-C4", 500)
    owner.post("/tag-records/main/", {"type": "batch", "month": MONTH, "record_id": sid, "tag": t.pk})
    owner.post(f"/tags/{t.pk}/", {"active": "0"})
    t.refresh_from_db()
    assert not t.is_active and _audit("TAG_DEACTIVATED")
    assert _row("batch", sid)["parts"][0][0] == t                                # records keep it
    page = owner.get("/tag-records/", {"type": "batch", "month": MONTH})
    assert t not in list(page.context["tags"])                                   # no longer offered
    other = _batch(db, "DEACT2-C4", 100)
    resp = owner.post("/tag-records/main/", {"type": "batch", "month": MONTH, "record_id": other, "tag": t.pk}, follow=True)
    assert "deactivated" in resp.content.decode()


def test_the_default_tag_cannot_be_deactivated(c4):
    from core.models import Tag

    users, _ = c4
    general = Tag.objects.get(is_default=True)
    _client(users["owner"]).post(f"/tags/{general.pk}/", {"active": "0"})
    general.refresh_from_db()
    assert general.is_active


# ---- summary and access ---------------------------------------------------------------------------------------------------------

def test_summary_by_group_and_sub_tag(c4):
    from core import carveout
    from core.models import Tag

    users, db = c4
    month = "2031-07"
    a = _card_statement(db, "Summary Card C4", 10000, month)
    _batch(db, "SUM-C4", 50000, f"{month}-03 01:00:00")
    household = _tag("family", "Household C4")
    carveout.set_split("cc_statement", a, Tag.objects.get(is_default=True), [(household, Decimal("2500"))])
    s = carveout.summary(month)
    assert s["by_type"]["Card statements"]["family"] == Decimal("2500.00")
    assert s["by_type"]["Card statements"]["business"] == Decimal("7500.00")
    assert s["totals"]["business"] >= Decimal("57500.00") and s["totals"]["total"] == sum(s["totals"][g] for g in ("business", "rental", "family"))
    assert dict((t.name, v) for t, v in s["by_tag"])["Household C4"] == Decimal("2500.00")
    page = _client(users["owner"]).get("/carve-out/", {"month": month})
    assert page.status_code == 200 and "2,500.00" in page.content.decode()


@pytest.mark.parametrize("role", ["sales", "viewer"])
def test_only_the_owner_sees_carve_out(c4, role):
    users, db = c4
    client = _client(users[role])
    for path in ("/tags/", "/tag-records/", "/carve-out/", "/tag-records/batch/1/"):
        assert client.get(path).status_code == 403, path
    assert client.post("/tags/add/", {"group": "family", "name": "Sneaky"}).status_code == 403
    assert "/carve-out/" not in client.get("/").content.decode()
