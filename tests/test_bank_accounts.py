"""Settings > Bank accounts (owner, 2026-10-20): the accounts' nature, check-number rule, statement file and 'used for' -- read by the code
instead of Metrobank / 399 / account 1. Slick's account keeps the old values, so nothing changes until another account is set up."""
from conftest import fetchone
from tests.test_web_d0 import _admin


def test_slicks_account_keeps_its_rule_and_a_second_account_has_its_own(web, db):
    from engines import bank_accounts as ba
    from engines.check_engine import format_metrobank_check_number
    from engines.check_rules import _check_number_key, _check_number_variants
    from engines.bank_statement_service import norm_check_number
    _admin(web)
    a = ba.check_account()
    assert a["bank_account_id"] == 1 and a["check_prefix"] == "399" and a["check_digits"] == 7 and a["writes_checks"] and a["statement_reader"] == "mbos"
    assert format_metrobank_check_number(2554301) == "3992554301" and ba.leaf_of("3992554301") == 2554301 and ba.leaf_of("2554301") == 2554301
    assert _check_number_key("3992554301") == "2554301" and "3992554301" in _check_number_variants("2554301") and norm_check_number("3992554301") == norm_check_number("2554301")
    ok, msg, bid = ba.add({"bank_name": "BDO", "account_name": "Roxas Hardware", "account_number": "0012345678", "shown_as": "BDO Business", "nature": "settlement"})
    assert ok, msg
    b = ba.get(bid)
    assert b["nature"] == "settlement" and b["in_forecast"] == 1 and b["writes_checks"] == 0 and b["statement_reader"] == "generic"
    ok, msg = ba.save(bid, {**b, "check_prefix": "12", "check_digits": 8, "writes_checks": "1", "in_forecast": "1", "cards_paid_from": "", "statement_number_form": "leaf"})
    assert ok, msg
    assert ba.check_account_id() == bid and ba.get(1)["writes_checks"] == 0            # check writing moved: only one account has it
    assert format_metrobank_check_number(123) == "1200000123" and ba.leaf_of("1200000123") == 123
    assert _check_number_key("3992554301") == "2554301"                               # Slick's old numbers are still understood
    assert not ba.close(bid)[0]                                                        # the check account cannot be closed
    assert ba.save(1, {**ba.get(1), "writes_checks": "1", "in_forecast": "1", "cards_paid_from": "1"})[0]
    assert ba.close(bid)[0] and ba.get(bid)["is_active"] == 0 and ba.reopen(bid)[0]
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action LIKE 'BANK_ACCOUNT_%'")[0] >= 5


def test_settings_bank_accounts_page(web, db):
    from engines import bank_accounts as ba
    _admin(web)
    page = web.get("/settings/banks/").content.decode()
    assert "Metrobank Account 1" in page and "399</b> + 7 digits" in page and "Metrobank MBOS" in page
    r = web.post("/settings/banks/", {"action": "add", "bank_name": "BPI", "account_name": "Payroll", "account_number": "9988776655", "shown_as": "BPI Payroll", "nature": "payroll"})
    assert r.status_code == 302 and "edit=" in r["Location"]
    bid = ba.listing()[-1]["bank_account_id"]
    page = web.get(f"/settings/banks/?edit={bid}").content.decode()
    assert "Edit account · BPI Payroll" in page and "Close this account" in page
    r = web.post("/settings/banks/", {"action": "save", "id": bid, "bank_name": "BPI", "account_name": "Payroll", "account_number": "9988776655", "shown_as": "BPI Payroll",
                                      "nature": "payroll", "check_prefix": "", "check_digits": "10", "statement_number_form": "either", "statement_reader": "generic",
                                      "in_forecast": "1", "check_width_mm": "203.2", "check_height_mm": "76.2", "check_offset_x_mm": "0", "check_offset_y_mm": "0"})
    assert r.status_code == 302 and ba.get(bid)["check_digits"] == 10
    assert "BPI Payroll" in web.get("/bank-cash/").content.decode()                     # the import form offers the account
    assert web.post("/settings/banks/", {"action": "close", "id": bid}).status_code == 302 and ba.get(bid)["is_active"] == 0
