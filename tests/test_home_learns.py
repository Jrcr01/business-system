"""My home learns each person's most-used pages, with pins (owner, 2026-10-19: "my home tiles ... should be the basic tile should have or frequent click
by the user"; "auto learn with pinning, admin too")."""
import sqlite3
from datetime import timedelta

from tests.test_web_d0 import _admin


def _clicks(db, email, card, n, days_ago=1):
    from engines.clock import manila_today
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO home_clicks (user_email, card, day, clicks) VALUES (?, ?, ?, ?)", (email, card, str(manila_today() - timedelta(days=days_ago)), n))


def _my_home(page):
    import re
    head = page.split('id="g-mine"')[1]
    head = head[:head.find('class="home-sec"', 10)] if 'class="home-sec"' in head[10:] else head
    return re.findall(r'class="tile[^"]*" href="[^"]*" data-card="([^"]+)"', head)


def test_my_home_starts_with_the_basics_then_follows_the_clicks(web, db):
    _admin(web)
    start = _my_home(web.get("/").content.decode())
    assert "purchase-orders" in start or "New order" in start                                     # the admin's basics
    _clicks(db, "admin@slicks.test", "credit-cards", 12)
    _clicks(db, "admin@slicks.test", "bank-loans", 8)
    learned = _my_home(web.get("/").content.decode())
    assert learned[:2] == ["credit-cards", "bank-loans"]
    _clicks(db, "admin@slicks.test", "lenders", 30, days_ago=0)                                     # today's clicks wait for tomorrow
    assert _my_home(web.get("/").content.decode())[:2] == ["credit-cards", "bank-loans"]


def test_a_pinned_card_stays_and_clicks_are_counted(web, db):
    import json
    from conftest import fetchone
    _admin(web)
    assert json.loads(web.post("/home/pin/", {"card": "calendar"}).content)["pinned"] is True
    assert _my_home(web.get("/").content.decode())[0] == "calendar"
    assert web.post("/home/click/", {"card": "checks"}).status_code == 204
    assert fetchone(db, "SELECT clicks FROM home_clicks WHERE card = 'checks'")[0] == 1
    assert json.loads(web.post("/home/pin/", {"card": "calendar"}).content)["pinned"] is False
