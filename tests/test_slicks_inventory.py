"""Slicks Inventory and the quick quotation (owner, 2026-10-17; mock-ups an_slicks_inventory and ao_quick_quotation, answers all A): tires by rim
like the notebook (size, brand, how many), its own app icon; a quote for one size lists what is on hand at cost, the markup is added and rounded up on
the phone; the customer's view and what is shared never carry a cost; the staff phone gets the stock but no quote."""
from tests.test_phone import _on
from tests.test_web_d0 import _admin, _sign_in_new
from tests.test_web_stock import _item, _stock


def test_the_rim_of_every_kind_of_size_and_the_notebooks_order():
    from engines.rim_stock import rim_of, round_up, size_order
    assert [rim_of(s) for s in ("205/55R16", "7.50-16", "9.5R17.5", "31x10.5R15", "12x24", "8-18", "195R14", "11R22.5", "205/45ZR17")] == \
        ["16", "16", "17.5", "15", "24", "18", "14", "22.5", "17"]
    sizes = ["7.50R16", "215/65R16", "205/55R16", "7.50-16", "195R16", "205/60R16"]
    assert sorted(sizes, key=size_order) == ["205/55R16", "205/60R16", "215/65R16", "195R16", "7.50-16", "7.50R16"]
    assert (round_up(2627, 50), round_up(2650, 50), round_up(2627, 0)) == (2650, 2650, 2627)


def test_slicks_inventory_shows_each_rim_size_brand_and_how_many(web, db):
    _admin(web)
    _on()
    a, _ = _item("Westlake", "205/55R16", "SA37")
    b, _ = _item("Sunny", "205/55R16", "NP226")
    c, _ = _item("Westlake", "185/65R15", "RP28")
    _stock(a, 6, 3100.0)
    _stock(c, 4, 2400.0)
    page = web.get("/phone/inventory/?rim=16").content.decode()
    assert "205/55R16" in page and "Westlake SA37" in page and "Sunny NP226" in page and "185/65R15" not in page
    assert "3,100" not in page and "3100" not in page                                    # no cost on the stock by rim
    assert 'href="/phone/inventory/quote/?size=205/55R16"' in page or "quote/?size=205%2F55R16" in page
    assert "185/65R15" in web.get("/phone/inventory/?q=1856515").content.decode()         # find works across rims
    assert web.cookies["slk_rim"].value == "16"
    m = web.get("/phone/manifest-inv.webmanifest")
    assert m.status_code == 200 and b'"Slicks Inventory"' in m.content and b'"/phone/inventory/"' in m.content


def test_the_quote_lists_what_is_on_hand_at_cost_cheapest_first(web, db):
    _admin(web)
    _on()
    a, _ = _item("Westlake", "205/55R16", "SA37")
    b, _ = _item("Sunny", "205/55R16", "NP226")
    z, _ = _item("Michelin", "205/55R16", "Primacy 4")                                  # none on hand: left out
    _stock(a, 6, 3100.0)
    _stock(b, 8, 2550.0)
    from engines.rim_stock import quote_lines
    assert [(ln["name"], ln["qty"], ln["cost"]) for ln in quote_lines("205/55R16")] == [("Sunny NP226", 8.0, 2550.0), ("Westlake SA37", 6.0, 3100.0)]
    page = web.get("/phone/inventory/quote/?size=205%2F55R16").content.decode()
    assert "Show the customer" in page and '"cost": 2550.0' in page and "Mounting and balancing included? Ask us." in page
    r = web.post("/phone/inventory/quote/?size=205%2F55R16", {"footer": "Free mounting today."}, follow=True)
    assert "Free mounting today." in r.content.decode()                                  # the bottom line is the admin's to change


def test_the_staff_phone_sees_the_stock_by_rim_but_never_a_quote(web, db):
    _admin(web)
    _on()
    a, _ = _item("Westlake", "205/55R16", "SA37")
    _stock(a, 6, 3100.0)
    rack = _sign_in_new("rack@slicks.test", "scanner")
    page = rack.get("/phone/inventory/?rim=16").content.decode()
    assert "Westlake SA37" in page and "Quote" not in page
    assert rack.get("/phone/inventory/quote/?size=205%2F55R16").status_code == 403


def test_a_sale_started_from_a_quote_opens_with_the_tire_and_the_total(web, db):
    _admin(web)
    _on()
    a, _ = _item("Sunny", "205/55R16", "NP226")
    _stock(a, 8, 2550.0)
    from engines.sales_rules import register_booklet
    register_booklet(4501, 4550, "Julian")
    page = web.get(f"/phone/sale/?item={a}&qty=4&receipt=10600.00").content.decode()
    assert 'name="receipt" value="10600.00"' in page and f'"id": {a}, "qty": 4.0' in page.replace("&quot;", '"')
