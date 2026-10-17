"""Card credits and unbilled installments (owner, 2026-10-17; mock-up docs/mockups/ac_card_installments.html): a balance converted to installments is a
CREDIT, not a payment, so the chain still adds up to the bank's total; the installments still to be billed are typed as one figure and counted in what we
owe on the card everywhere; both snippable off the statement; a picture can be pasted from the clipboard on every snip page."""
import io
import sqlite3
from datetime import date

from django.core.files.uploadedfile import SimpleUploadedFile

from tests.test_card_analysis import _card, _db
from tests.test_web_d0 import _admin


def _pdf(lines):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica", 10)
    y = 700
    for text in lines:
        c.drawString(80, y, text)
        y -= 16
    c.showPage()
    c.save()
    return buf.getvalue()


def _total(sql):
    with sqlite3.connect(_db()) as conn:
        return conn.execute(sql).fetchone()[0]


def test_a_conversion_is_a_credit_not_a_payment_and_the_unbilled_balance_is_owed(db):
    from engines import card_analysis
    from engines.card_rules import add_statement_from_soa
    from engines.reports_service import get_combined_ap_summary
    card = _card(db, holder="Jeane", limit=800_000)
    ok, _, sep = add_statement_from_soa(card, "2026-09", date(2026, 9, 30), 506_370.33, None, 12_940.10)
    assert ok
    # the Metrobank statement of 10/10: 40,000 paid, 600,183.43 converted to installments, 13,085.63 finance charge, 66,511.48 due; 418,537.11 still to be billed
    ok, msg, oct_ = add_statement_from_soa(card, "2026-10", date(2026, 10, 30), 66_511.48, 40_000, 13_085.63, credits=600_183.43, unbilled=418_537.11)
    assert ok and "418,537.11" in msg
    with sqlite3.connect(_db()) as conn:
        prev, new, cr, ub = conn.execute("SELECT previous_balance, new_charges, credits, unbilled_installments FROM cc_statements WHERE cc_statement_id = ?", (oct_,)).fetchone()
        paid, status = conn.execute("SELECT amount_paid, status FROM cc_statements WHERE cc_statement_id = ?", (sep,)).fetchone()
    assert prev == 0 and round(new, 2) == 53_425.85 and cr == 600_183.43 and ub == 418_537.11     # the credit exceeded the carried balance: what is left of the total is new
    assert paid == 40_000 and status == "Paid"                 # the earlier statement: 40,000 paid, the rest converted to installments, nothing carried -- not "506,370 paid"
    df = get_combined_ap_summary()
    assert round(float(df[df["track"] == "Credit Card"]["outstanding"].iloc[0]), 2) == round(66_511.48 + 418_537.11, 2)
    r = card_analysis.analysis(date(2026, 10, 17))
    assert r["owed"] == round(66_511.48 + 418_537.11, 2) and r["owed_unbilled"] == 418_537.11 and r["owed_statements"] == 66_511.48
    me = r["cards"][0]
    assert me["unbilled"] == 418_537.11 and ("warn", "Balance converted to installments this month") in me["watch"]
    assert me["used"] == round((66_511.48 + 418_537.11) / 800_000 * 100)


def test_the_two_figures_are_corrected_on_the_latest_statement_only_and_never_negative(db):
    from engines.card_rules import add_statement_from_soa, correct_cc_statement
    card = _card(db)
    _, _, a = add_statement_from_soa(card, "2026-08", date(2026, 8, 30), 10_000, None)
    _, _, b = add_statement_from_soa(card, "2026-09", date(2026, 9, 30), 12_000, 10_000, unbilled=5_000)
    assert correct_cc_statement(a, "2026-08", date(2026, 8, 30), unbilled=1)[0] is False
    assert correct_cc_statement(b, "2026-09", date(2026, 9, 30), unbilled=-1)[0] is False
    assert add_statement_from_soa(card, "2026-10", date(2026, 10, 30), 1_000, 0, credits=-5)[0] is False
    ok, msg = correct_cc_statement(b, "2026-09", date(2026, 9, 30), credits=500, unbilled=4_000)
    assert ok
    assert _total("SELECT credits + unbilled_installments FROM cc_statements WHERE cc_statement_id = %d" % b) == 4_500
    assert _total("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CC_STATEMENT_CORRECTED' AND new_values LIKE '%unbilled installments %4,000.00%'") == 1
    assert correct_cc_statement(b, "2026-09", date(2026, 9, 30), credits=500, unbilled=4_000) == (False, "Nothing was changed.")


def test_the_card_page_has_the_boxes_the_snip_and_the_paste_button(web, db):
    from engines import snip
    _admin(web)
    card = _card(db)
    page = web.get("/credit-cards/").content.decode()
    assert 'name="credits"' in page and 'name="unbilled"' in page and 'id="sn_paste"' in page and "slicksSnip.paste" in page
    doc = _pdf(["SUMMARY OF OUTSTANDING INSTALLMENT", "003  05/08/25  41,300.18  QUICK CASH", "005  08/10/25  77,118.93  C2G", "023  10/07/26  185,576.84  BALCON"])
    r = web.post("/credit-cards/", {"action": "snip_upload", "file": SimpleUploadedFile("metrobank.pdf", doc, content_type="application/pdf")})
    assert r.status_code == 302 and "?f=" in r["Location"]
    token = r["Location"].split("?f=")[1]
    page = web.get(f"/credit-cards/?f={token}").content.decode()
    assert 'id="sn_wrap"' in page and f'name="token" value="{token}"' in page and "Click a box in the form" in page
    assert web.get(f"/credit-cards/snip/{token}/page/1.jpg")["Content-Type"] == "image/jpeg"
    ws = snip.words(token, 1)
    col = [w for w in ws if w["t"] in ("41,300.18", "77,118.93", "185,576.84")]
    import json
    box = {"x0": min(w["x0"] for w in col) - 3, "y0": min(w["y0"] for w in col) - 3, "x1": max(w["x1"] for w in col) + 3, "y1": max(w["y1"] for w in col) + 3}
    d = web.post(f"/credit-cards/snip/{token}/read/", json.dumps({"page": 1, "mode": "text", "box": box}), content_type="application/json").json()
    assert d["ok"] and d["text"].split() == ["41,300.18", "77,118.93", "185,576.84"]       # the page adds these up into the Unbilled installments box
    r = web.post("/credit-cards/", {"action": "statement", "card": card, "month": "2026-10", "due": "2026-10-30", "total": "66,511.48", "payments": "",
                                    "credits": "", "interest": "0", "unbilled": "303,995.95", "token": token})
    assert r.status_code == 302
    assert snip.file_info(token) is None                                      # the statement document is not kept
    page = web.get("/credit-cards/").content.decode()
    assert "Unbilled installments</th>" in page and "303,995.95" in page


def test_every_snip_page_offers_paste_from_the_clipboard(web, db):
    from conftest import make_creditor
    from tests.test_snip_loan import _loan
    _admin(web)
    cid = make_creditor(db, "Sunny Tire Supply", terms="30")
    assert "slicksSnip.paste" in web.get(f"/price-lists/?s={cid}&tab=snip").content.decode()
    assert "slicksSnip.paste" in web.get(f"/order-statements/soa/?s={cid}").content.decode()
    blid = _loan(web, db)
    assert "slicksSnip.paste" in web.get(f"/bank-loans/{blid}/?tab=snip").content.decode()
    # a pasted picture arrives through the same upload as a chosen file
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buf, format="PNG")
    r = web.post("/order-statements/soa/", {"s": cid, "action": "snip_upload", "file": SimpleUploadedFile("pasted-20261017.png", buf.getvalue(), content_type="image/png")})
    assert r.status_code == 302 and "&f=" in r["Location"]


def test_a_snip_document_can_be_removed_now_with_the_page_own_rights(web, db):
    """Owner, 2026-10-17: a ✕ beside each open document removes it at once (instead of after a day)."""
    from PIL import Image
    from engines import snip
    from tests.test_web_d0 import _sign_in_new
    _admin(web)
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), "white").save(buf, format="PNG")
    r = web.post("/credit-cards/", {"action": "snip_upload", "file": SimpleUploadedFile("Pasted 17 Oct 10:30 AM.png", buf.getvalue(), content_type="image/png")})
    token = r["Location"].split("?f=")[1]
    page = web.get(f"/credit-cards/?f={token}").content.decode()
    assert "Pasted 17 Oct 10:30 AM.png" in page and 'action="/snip/discard/"' in page and f'name="token" value="{token}"' in page
    viewer = _sign_in_new("view@slicks.test", "viewer")
    r = viewer.post("/snip/discard/", {"token": token, "back": "/credit-cards/"})
    assert snip.file_info(token) is not None                                  # without the card rights nothing is removed
    r = web.post("/snip/discard/", {"token": token, "back": f"/credit-cards/?f={token}"})
    assert r.status_code == 302 and r["Location"] == "/credit-cards/" and snip.file_info(token) is None
    r = web.post("/snip/discard/", {"token": token, "back": "https://evil.example/"})
    assert r["Location"] == "/"                                               # gone already; never sent off the site


def test_the_picked_card_and_the_open_statement_stay_together(web, db):
    """Owner, 2026-10-17: "when i upload the statement then pick a card the statement will close upon refresh. vice versa"."""
    from PIL import Image
    _admin(web)
    card = _card(db)
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), "white").save(buf, format="PNG")
    r = web.post("/credit-cards/", {"action": "snip_upload", "card": str(card), "file": SimpleUploadedFile("soa.png", buf.getvalue(), content_type="image/png")})
    assert r.status_code == 302 and r["Location"].startswith(f"/credit-cards/?card={card}&show=all&f=")          # the card stays picked
    token = r["Location"].split("&f=")[1]
    page = web.get(f"/credit-cards/?card={card}&show=all&f={token}").content.decode()
    assert f'href="?card={card}&amp;show=all&amp;f={token}"' in page or f'href="?card={card}&show=all&f={token}"' in page   # its link keeps the card
    assert 'go.set("f", here.get("f"))' in page and "cc_soa_draft" in page                                       # picking a card keeps the statement; typed figures kept


def test_correcting_a_statement_keeps_the_open_picture(web, db):
    """Owner, 2026-10-17: "consider also the edit statement" / "when it loads i have to reupload again": Edit, Cancel, the filter and the saved
    correction keep the open picture; pasting while correcting comes back to the correction; the Edit form's boxes take a snip too."""
    from PIL import Image
    from engines.card_rules import add_statement_from_soa
    _admin(web)
    card = _card(db)
    _, _, sid = add_statement_from_soa(card, "2026-10", date(2026, 10, 30), 66_511.48, None)
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), "white").save(buf, format="PNG")
    r = web.post("/credit-cards/", {"action": "snip_upload", "card": str(card), "edit": str(sid), "file": SimpleUploadedFile("soa.png", buf.getvalue(), content_type="image/png")})
    assert r.status_code == 302 and f"&edit={sid}#edit" in r["Location"]                                          # back to the correction
    token = r["Location"].split("&f=")[1].split("&")[0]
    page = web.get(f"/credit-cards/?card={card}&show=all&f={token}&edit={sid}").content.decode()
    assert 'id="es-form"' in page and "es_unbilled" in page and 'id="sn_wrap"' in page
    assert f"&amp;f={token}&amp;edit={sid}#edit" in page or f"&f={token}&edit={sid}#edit" in page              # the Edit links carry the picture
    assert f'<input type="hidden" name="f" value="{token}">' in page                                              # so does the filter
    r = web.post("/credit-cards/", {"action": "correct_statement", "statement_id": sid, "card": str(card), "month": "2026-10", "due": "2026-10-30",
                                    "total": "66,511.48", "interest": "0", "credits": "0", "unbilled": "1,032,501.45", "token": token})
    assert r.status_code == 302 and f"&f={token}" in r["Location"]                                                 # still open after saving
    with sqlite3.connect(_db()) as conn:
        assert conn.execute("SELECT unbilled_installments FROM cc_statements WHERE cc_statement_id = ?", (sid,)).fetchone()[0] == 1_032_501.45


def test_with_a_picture_open_the_form_sits_beside_it_with_add_and_correct_tabs(web, db):
    """Owner, 2026-10-17 ("1 as drawn 2 yes 3 include all"): picture left, form right; Correct is a tab beside the picture; zoom; the next box named."""
    from engines.card_rules import add_statement_from_soa
    _admin(web)
    card = _card(db)
    _, _, sid = add_statement_from_soa(card, "2026-10", date(2026, 10, 30), 66_511.48, None)
    r = web.post("/credit-cards/", {"action": "snip_upload", "file": SimpleUploadedFile("soa.pdf", _pdf(["TOTAL AMOUNT DUE 66,511.48"]), content_type="application/pdf")})
    token = r["Location"].split("?f=")[1]
    page = web.get(f"/credit-cards/?f={token}").content.decode()
    assert 'class="cc-split on"' in page and 'id="cc_next"' in page and 'class="cc-zoom"' in page and 'class="cc-tabs"' not in page
    page = web.get(f"/credit-cards/?f={token}&edit={sid}").content.decode()
    panel = page[page.index('id="cc_panel"'):page.index('class="cc-doc"')]
    assert 'class="cc-tabs"' in panel and 'id="es-form"' in panel and page.count('id="es-form"') == 1                  # the correction beside the picture, once
    page = web.get(f"/credit-cards/?edit={sid}").content.decode()
    assert 'class="cc-split"' in page and 'id="es-form"' in page and 'class="cc-tabs"' not in page                      # no picture: as before
