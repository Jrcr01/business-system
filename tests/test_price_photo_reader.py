"""Price lists > Read a photo (owner, 2026-10-20): photos read by the AI into rows, checked by a person, saved through the Excel import.
The AI is a stand-in: no internet, no cost."""
import io
import json
from types import SimpleNamespace

import pytest

from conftest import fetchone, make_creditor
from tests.test_web_d0 import _admin

KEY = "sk-ant-api03-" + "x" * 40 + "WXYZ"
READ = {"brand": "Sonix", "effective_date": "2026-09-01", "columns": ["GROSS PRICE", "NET PRICE"], "handwritten": "less 5%",
        "photos": [{"photo": 1, "same_as": 0}, {"photo": 2, "same_as": 1}],
        "rows": [{"photo": 1, "size": "225/55ZR17", "ply": "", "pattern": "XSPORT S8 EV", "prices": [5200, 4100], "unclear": False},
                 {"photo": 1, "size": "225/50ZR18", "ply": "", "pattern": "XSPORT S8 EV", "prices": [6000, 4800], "unclear": True},
                 {"photo": 1, "size": "235/50ZR18", "ply": "", "pattern": "XSPORT S8 EV", "prices": [5600, 4700], "unclear": False},
                 {"photo": 1, "size": "215/50ZR18", "ply": "", "pattern": "XSPORT S8 EV", "prices": [9900, 9800], "unclear": False},
                 {"photo": 2, "size": "225/55ZR17", "ply": "", "pattern": "XSPORT S8 EV", "prices": [5200, 4100], "unclear": False}]}


def _photo(color="white"):
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (300, 400), color).save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def ai(monkeypatch):
    from engines import ai_client, settings_registry as sr
    calls = []

    class Client:
        class messages:
            @staticmethod
            def create(**kw):
                calls.append(kw)
                return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(READ))], stop_reason="end_turn",
                                       usage=SimpleNamespace(input_tokens=3000, output_tokens=800, cache_creation_input_tokens=0, cache_read_input_tokens=0))
    monkeypatch.setattr(ai_client, "_client", lambda: Client)
    return SimpleNamespace(calls=calls, on=lambda: (ai_client.save_key(KEY), sr.save("ai_on", 1)))


def test_off_until_the_ai_is_on(web, db, ai):
    _admin(web)
    cid = make_creditor(db, "Tire Asia Test", terms="30")
    page = web.get(f"/price-lists/?s={cid}&tab=photo").content.decode()
    assert "AI help is switched off" in page
    from engines.price_photo_reader import read_photos
    ok, msg, token = read_photos(cid, [("a.png", _photo())])
    assert not ok and "switched off" in msg and ai.calls == []


def test_read_check_and_save(web, db, ai):
    from engines import price_photo_reader as ppr
    from engines.list_cost import list_costs
    _admin(web)
    ai.on()
    cid = make_creditor(db, "Tire Asia Test", terms="30")
    r = web.post("/price-lists/", {"s": cid, "tab": "photo", "action": "photo_read", "photos": [io.BytesIO(_photo()), io.BytesIO(_photo("gray"))]})
    token = r["Location"].split("r=")[1]
    sent = ai.calls[0]
    assert sum(1 for b in sent["messages"][0]["content"] if b["type"] == "image") == 2 and sent["output_config"]["format"]["type"] == "json_schema"
    rv = ppr.review(token, 1)
    assert [x["size"] for x in rv["rows"]] == ["225/55ZR17", "225/50ZR18", "235/50ZR18", "215/50ZR18"]       # photo 2 = photo 1: its rows dropped
    assert rv["handwritten"] == "less 5%" and rv["col_name"] == "NET PRICE"
    flags = {x["size"]: (x["unclear"], x["odd"]) for x in rv["rows"]}
    assert flags["225/50ZR18"][0] is True                                                     # the AI was unsure
    assert "lower than" in flags["215/50ZR18"][1] or "far from" in flags["215/50ZR18"][1] or flags["235/50ZR18"][1]
    page = web.get(f"/price-lists/?s={cid}&tab=photo&r={token}").content.decode()
    assert "NET PRICE" in page and "4100.00" in page and "new item" in page and "hard to read" in page
    assert web.get(f"/price-lists/photo/{token}/1.jpg").status_code == 200
    post = {"s": cid, "tab": "photo", "action": "photo_save", "r": token, "n": 4, "brand": "Sonix EV", "tube": "TL", "effective_date": "2026-09-01",
            "discount": "5", "discount_scope": "brand"}
    for x in rv["rows"]:
        post.update({f"size_{x['i']}": x["size"], f"ply_{x['i']}": x["ply"], f"pattern_{x['i']}": x["pattern"], f"price_{x['i']}": f"{x['price']:,.2f}"})
    post["keep_3"] = "0"                                                                         # the odd row left out by the person
    r = web.post("/price-lists/", post)
    assert r.status_code == 302 and "tab=prices" in r["Location"]
    assert fetchone(db, "SELECT COUNT(*) FROM supplier_price_list WHERE creditor_id = ? AND is_active = 1", (cid,))[0] == 3
    iid = fetchone(db, "SELECT id FROM items WHERE name = 'Sonix EV 225/55ZR17 XSPORT S8 EV Tubeless'")[0]
    assert list_costs([iid])[iid] == 3895.0                                                    # 4,100 less 5%
    assert ppr.review(token) is None                                                           # the reading is gone once saved
    assert fetchone(db, "SELECT COUNT(*) FROM ai_usage WHERE feature = 'price_photo'")[0] == 1


def test_a_known_tire_joins_its_item(web, db, ai):
    from engines import price_photo_reader as ppr
    from engines.product_catalog import get_or_create_product
    _admin(web)
    ai.on()
    iid, name, _ = get_or_create_product("Tires", "Sonix", size="225/55ZR17", pattern="XSPORT S8 EV", tube="TL")
    cid = make_creditor(db, "Tire Asia Test", terms="30")
    ok, msg, token = ppr.read_photos(cid, [("a.png", _photo())])
    assert ok, msg
    code = fetchone(db, "SELECT code FROM items WHERE id = ?", (iid,))[0]
    rows = {x["size"]: x["code"] for x in ppr.review(token)["rows"]}
    assert rows["225/55ZR17"] == code and rows["225/50ZR18"] is None
