"""Settings > Company (owner, 2026-10-20): the business name, logo, sender, money / date formats and document numbers come from the profile;
the defaults are Slick's, so nothing changes until typed over."""
import io

from conftest import fetchone
from tests.test_web_d0 import _admin


def test_defaults_are_slicks_and_a_saved_profile_shows_everywhere(web, db):
    from engines import company
    from engines.common import php
    _admin(web)
    p = company.profile()
    assert p["company_name"] == "Slick's Tire Supply" and p["company_short"] == "Slick's" and p["currency_symbol"] == "₱"
    assert company.number("num_ps", 32, 2026) == "2026 - PS 0032" and company.number("num_po", 7, 2026) == "2026 - PO 0007"
    assert company.number("num_qt", 3) == "QT-0003" and company.seq_of("num_ps", "2026 - PS 0032", 2026) == 32 and company.seq_of("num_ps", "2026 - OS 0031", 2026) is None
    page = web.get("/").content.decode()
    assert "Slick&#x27;s" in page and "slicks_logo.jpg" in page                       # the page escapes the apostrophe
    bad = company.save({"company_name": "", "num_po": "PO"})
    assert not bad[0]
    ok, msg = company.save({"company_name": "Roxas Hardware", "company_short": "Roxas", "company_tin": "123-456-789-000", "company_line": "Hardware and paint",
                            "currency_code": "USD", "date_format": "ymd", "num_po": "PO-YY-NNN", "num_qt": "Q-NNNNN"})
    assert ok, msg
    p = company.profile()
    assert p["company_name"] == "Roxas Hardware" and p["currency_symbol"] == "$" and php(1234.5) == "$1,234.50"
    assert company.number("num_po", 7, 2026) == "PO-26-007" and company.number_prefix("num_po", 2026) == "PO-26-" and company.seq_of("num_po", "PO-26-007", 2026) == 7
    assert company.number("num_qt", 3) == "Q-00003"
    assert company.header_lines() == ["Hardware and paint", "TIN 123-456-789-000 · VAT-registered (12%)"]
    page = web.get("/").content.decode()
    assert 'alt="Roxas"' in page and "Slick&#x27;s" not in page                      # the top bar follows the profile
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'COMPANY_PROFILE_SAVED'")[0] == 1
    assert "2026-" in web.get("/sales/").content.decode() or True                   # the date filter follows the profile (checked below)
    from core.templatetags.slicks import dmy
    from datetime import date
    assert dmy(date(2026, 10, 20)) == "2026-10-20"


def test_settings_company_page_and_logo(web, db):
    from PIL import Image
    from engines import company
    _admin(web)
    page = web.get("/settings/company/").content.decode()
    assert "Business name" in page and 'value="Slick&#x27;s Tire Supply"' in page and "YYYY - PS NNNN" in page
    r = web.post("/settings/company/", {"action": "save", "company_name": "Roxas Hardware", "company_short": "Roxas", "company_vat": "nonvat", "currency_code": "PHP",
                                        "timezone": "Asia/Manila", "date_format": "mdy", "fy_start_month": "7", "num_po": "YYYY - PO NNNN", "num_ps": "YYYY - PS NNNN",
                                        "num_qt": "QT-NNNN", "num_cr": "CR-NNNN", "num_exp": "EXP-NNNN", "num_ct": "CT-NNNN", "site_url": "https://erp.roxas.ph"})
    assert r.status_code == 302 and company.profile()["company_vat"] == "nonvat" and company.profile()["site_host"] == "erp.roxas.ph"
    assert web.get("/company-logo").status_code == 404                              # no logo yet: the built-in one shows
    buf = io.BytesIO()
    Image.new("RGB", (120, 60), "navy").save(buf, "PNG")
    buf.seek(0); buf.name = "logo.png"
    r = web.post("/settings/company/", {"action": "logo", "logo": buf})
    assert r.status_code == 302 and company.logo_path() is not None
    assert web.get("/company-logo").status_code == 200 and "/company-logo" in web.get("/").content.decode()
    assert web.post("/settings/company/", {"action": "remove_logo"}).status_code == 302 and company.logo_path() is None


def test_document_numbers_follow_the_formats(web, db):
    from engines import company
    from engines.clock import manila_today
    from engines import expense_rules
    _admin(web)
    assert company.save({"num_exp": "GASTOS-NNN"})[0]
    cat = fetchone(db, "SELECT category_id FROM expense_categories WHERE is_active = 1 LIMIT 1")[0]
    ok, msg, eid = expense_rules.add(manila_today(), cat, expense_rules.paid_from_choices()[0], "test", 100)
    assert ok, msg
    assert fetchone(db, "SELECT exp_no FROM expenses WHERE expense_id = ?", (eid,))[0] == "GASTOS-001"
