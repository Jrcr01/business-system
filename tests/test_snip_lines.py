"""The snip box and its lines (owner, 2026-10-17: "make it sizable like DataSnipper", "I can fix the rows and columns"; mock-up ap_snip_rows_columns,
answers 1-3 A): every table read comes back with where the system put the rows and columns; the person's own lines are read cell by cell; a bigger box
keeps the person's lines and gets the guess in the new part; a picture is straightened first, and can be nudged; a text PDF is never turned."""
import io
import json

from tests.test_snip import ROWS, _box, _open
from tests.test_web_d0 import _admin
from tests.test_web_d2b import _supplier_with_price


def _read(web, token, body):
    return json.loads(web.post(f"/price-lists/snip/{token}/read/", json.dumps(body), content_type="application/json").content)


def test_a_table_read_gives_its_lines_and_the_persons_lines_are_read_cell_by_cell(web, db):
    _admin(web)
    cid = _supplier_with_price(web, db, "New Century City Marketing Corp.")
    token = _open(web, cid, ruled=False)
    box = _box(token, web)
    r = _read(web, token, {"page": 1, "mode": "table", "box": box})
    assert r["ok"] and len(r["lines"]["cols"]) == 2 and len(r["lines"]["rows"]) == 3            # 3 columns, 4 rows: the lines between them
    assert all(box["x0"] < x < box["x1"] for x in r["lines"]["cols"])
    # one column line removed: Size and Pattern read as one cell
    mine = {"cols": [r["lines"]["cols"][1]], "rows": r["lines"]["rows"]}
    r2 = _read(web, token, {"page": 1, "mode": "table", "box": box, "lines": mine})
    assert r2["how"] == "lines" and r2["rows"][1] == ["315/80R22.5-20PR Lug MD777", "16,800"]
    # every row line removed: one row holding the whole column
    r3 = _read(web, token, {"page": 1, "mode": "table", "box": box, "lines": {"cols": r["lines"]["cols"], "rows": []}})
    assert len(r3["rows"]) == 1 and "16,800" in r3["rows"][0][2] and "13,200" in r3["rows"][0][2]


def test_a_bigger_box_keeps_the_persons_lines_and_guesses_the_new_part(web, db):
    from engines.snip import merge_lines
    assert merge_lines({"cols": [100.0], "rows": []}, {"cols": [103.0, 300.0], "rows": [50.0]}) == {"cols": [100.0, 300.0], "rows": [50.0]}
    _admin(web)
    cid = _supplier_with_price(web, db, "New Century City Marketing Corp.")
    token = _open(web, cid, ruled=False)
    box = _box(token, web)
    r = _read(web, token, {"page": 1, "mode": "table", "box": box})
    narrow = dict(box, x1=r["lines"]["cols"][1])                                                # the Price column left out of the box
    r1 = _read(web, token, {"page": 1, "mode": "table", "box": narrow})
    mine = {"cols": [r1["lines"]["cols"][0] - 2], "rows": r1["lines"]["rows"]}                   # the person moved the line a little
    r2 = _read(web, token, {"page": 1, "mode": "table", "box": box, "lines": mine, "merge": True})
    assert mine["cols"][0] in r2["lines"]["cols"] and len(r2["lines"]["cols"]) == 2                # theirs kept, the new column's line added
    assert r2["rows"][2][2] == "14,700"


def test_a_photo_is_straightened_and_can_be_nudged_a_text_pdf_never(web, db):
    from PIL import Image, ImageDraw
    from django.core.files.uploadedfile import SimpleUploadedFile
    from engines import snip
    _admin(web)
    cid = _supplier_with_price(web, db, "New Century City Marketing Corp.")
    im = Image.new("RGB", (1200, 900), "white")
    d = ImageDraw.Draw(im)
    for y in range(100, 820, 45):
        d.line([(80, y), (1120, y)], fill="black", width=3)
    for x in (80, 400, 650, 900, 1120):
        d.line([(x, 100), (x, 820)], fill="black", width=3)
    buf = io.BytesIO()
    im.rotate(-3, fillcolor="white").save(buf, format="JPEG")                                  # taken at a slant
    loc = web.post("/price-lists/snip/upload/", {"s": cid, "file": SimpleUploadedFile("Ultra batteries.jpg", buf.getvalue(), content_type="image/jpeg")})["Location"]
    token = loc.split("&f=")[1].split("&")[0]
    a = json.loads(web.get(f"/snip/{token}/angle/?p=1").content)
    assert a["can"] and abs(a["angle"] - 3.0) < 0.3                                             # turned back level
    assert web.get(f"/price-lists/snip/{token}/page/1.jpg")["Cache-Control"] == "private, no-cache"
    gray, _ = snip._page_gray(snip.file_info(token), 1)
    hl, vl = snip.table_lines(gray, (60, 80, 1140, 840))
    assert len(hl) >= 15 and len(vl) == 5                                                      # level again: every rule found
    b = json.loads(web.post(f"/snip/{token}/angle/", json.dumps({"page": 1, "delta": 0.5}), content_type="application/json").content)
    assert abs(b["angle"] - a["angle"] - 0.5) < 0.01 and snip.page_angle(token, 1) == b["angle"]
    pdf = _open(web, cid, ruled=False)
    assert json.loads(web.get(f"/snip/{pdf}/angle/?p=1").content) == {"ok": True, "can": False, "angle": 0.0}
    assert ROWS                                                                                  # (the text PDF of the other tests)
