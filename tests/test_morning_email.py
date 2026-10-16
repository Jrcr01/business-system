"""The 8 AM email (owner, 2026-10-14): short, every day, to each person who receives it, following their pages; sent through a mail service whose
key the System admin types once."""
from datetime import datetime

from tests.test_web_d0 import _admin, _sign_in_new, fetchone_uid
from tests.test_web_sell import _ready, _sale


def _setup(web, monkeypatch):
    from engines import mail_out
    sent = []
    monkeypatch.setattr(mail_out, "_post", lambda url, headers, body: (sent.append((url, headers, body)) or (200, "{}")))
    r = web.post("/settings/notifications/", {"action": "mail_setup", "provider": "resend", "sender": "reports@slicks.test", "key": "re_test_key_12345678"})
    assert r.status_code == 302
    return sent


def _at(hour):
    from engines.clock import manila_today
    t = manila_today()
    return datetime(t.year, t.month, t.day, hour, 5)


def test_nothing_is_sent_until_the_service_is_set_up_and_the_key_is_never_shown(web, db, monkeypatch):
    from engines import morning_email
    _admin(web)
    assert morning_email.run(_at(8)) == "Email sending is not set up yet."
    page = web.get("/settings/notifications/").content.decode()
    assert "The morning email" in page and "not set up yet" in page
    _setup(web, monkeypatch)
    page = web.get("/settings/notifications/").content.decode()
    assert "sending is set up" in page and "ends in 5678" in page and "re_test_key_12345678" not in page
    assert web.post("/settings/notifications/", {"action": "mail_setup", "provider": "resend", "sender": "reports@slicks.test", "key": ""}).status_code == 302
    from engines.mail_out import _read
    assert _read()["key"] == "re_test_key_12345678"                              # a blank key keeps the saved one


def test_it_goes_at_eight_once_a_day_to_each_person_with_only_their_sections(web, db, monkeypatch):
    from engines import morning_email
    from engines.auth_service import set_access
    _admin(web)
    a, _ = _ready(web)
    _sale(web, a, si="004501", qty="2", price="5000")
    _sign_in_new("ana@slicks.test", "owner")
    _sign_in_new("rack@slicks.test", "scanner")
    set_access(fetchone_uid("ana@slicks.test"), {"sales", "bank-cash"}, set())
    sent = _setup(web, monkeypatch)
    assert morning_email.run(_at(7)) == "Not the email hour." and not sent
    assert morning_email.run(_at(8), site="https://erp.slicks.test") == "2 morning email(s) sent."
    assert morning_email.run(_at(8)) == "0 morning email(s) sent."                # never twice in a day
    to = {s[2]["to"][0]: s[2] for s in sent}
    assert set(to) == {"admin@slicks.test", "ana@slicks.test"}                    # never the Scanner account
    mine, hers = to["admin@slicks.test"], to["ana@slicks.test"]
    assert sent[0][0] == "https://api.resend.com/emails" and sent[0][1]["Authorization"] == "Bearer re_test_key_12345678"
    assert mine["from"] == "Slick's Tire Supply <reports@slicks.test>" and "reply_to" not in mine and "Cash</h2>" not in mine["html"] and "Good morning, Admin" in mine["html"]
    assert "Yesterday's sales" in mine["html"] and "Today" in mine["html"] and "https://erp.slicks.test/" in mine["html"]
    assert "Yesterday's sales" not in hers["html"] and "Cash</h2>" not in hers["html"] and "Good morning, Ana" in hers["html"]
    assert "Sales" not in hers["subject"] and mine["text"].startswith("Good morning, Admin")


def test_a_person_can_be_left_out_and_a_refusal_is_recorded_not_raised(web, db, monkeypatch):
    from engines import mail_out, morning_email
    _admin(web)
    _sign_in_new("ana@slicks.test", "owner")
    sent = _setup(web, monkeypatch)
    assert web.post("/settings/notifications/", {"action": "mail_pref", "email": "ana@slicks.test", "receives": "0"}).status_code == 302
    monkeypatch.setattr(mail_out, "_post", lambda url, headers, body: (403, "domain not verified"))
    assert morning_email.run(_at(8)) == "0 morning email(s) sent, 1 not sent."
    last = {p["email"]: p for p in morning_email.people()}
    assert "not sent" in last["admin@slicks.test"]["last"] and "domain not verified" in last["admin@slicks.test"]["last"] and not last["ana@slicks.test"]["receives"]
    monkeypatch.setattr(mail_out, "_post", lambda url, headers, body: (sent.append(body) or (200, "{}")))
    r = web.post("/settings/notifications/", {"action": "mail_test"}, follow=True)
    assert "Today&#x27;s email was sent to admin@slicks.test" in r.content.decode() or "Today's email was sent to admin@slicks.test" in r.content.decode()
    assert sent[-1]["to"] == ["admin@slicks.test"]


def test_the_admin_alone_hears_of_yesterdays_server_errors(web, db, monkeypatch):
    from datetime import date
    from engines import morning_email, server_health
    from engines.clock import manila_today
    _admin(web)
    _sign_in_new("ana@slicks.test", "owner")
    log = "Oct 14 gunicorn[1]: [2026-10-14 01:08:44 +0000] [1] [ERROR] Control server error: denied\nOct 14 gunicorn[2]: ERROR django.request: Internal Server Error: /calendar/\nOct 14 gunicorn[2]: ERROR django.request: Internal Server Error: /calendar/\n"
    assert server_health.summarise(log) == (2, ["ERROR django.request: Internal Server Error: /calendar/ (×2)"])
    monkeypatch.setattr(server_health, "errors_on", lambda day: server_health.summarise(log))
    mine = morning_email.content("admin@slicks.test", "Admin", manila_today())
    hers = morning_email.content("ana@slicks.test", "Ana", manila_today())
    assert any("2 errors on the server yesterday" in a for a in mine["also"]) and not any("error" in a for a in hers["also"])
    monkeypatch.setattr(server_health, "errors_on", lambda day: None)                       # no log to read: nothing said
    assert not any("error" in a for a in morning_email.content("admin@slicks.test", "Admin", manila_today())["also"])


def test_today_also_lists_earlier_checks_not_cleared_or_ticked(web, db):
    # owner, 2026-10-16: "I think it should include those not cleared yet or ticked"
    import sqlite3
    from datetime import timedelta
    from conftest import make_creditor
    from engines.acting_user import acting_as
    from engines.check_rules import issue_check
    from engines.clock import manila_today
    from engines.loan_schedule_service import create_note_with_payments
    from engines.morning_email import content
    from tests.test_web_d0 import _admin
    _admin(web)
    today = manila_today()
    lender = make_creditor(db, "Luz", creditor_type="LENDER")
    for days in (0, 3):
        day = today - timedelta(days=days)
        with acting_as("admin@slicks.test", "admin"):
            ok, msg, lnid = create_note_with_payments(lender, day - timedelta(days=10), 10000.0 + days, 0.0, [(day, 10000.0 + days)])
            assert ok, msg
            import engines.db_engine as d
            inst = sqlite3.connect(d.DB_PATH).execute("SELECT installment_id FROM loan_installments WHERE loan_note_id = ?", (lnid,)).fetchone()[0]
            ok, msg, _ = issue_check(lender, 10000.0 + days, day, loan_note_id=lnid, installment_id=inst)
            assert ok, msg
    rows = dict(content("admin@slicks.test", "Admin", today)["rows"])
    assert rows["Checks dated today (1)"] == "₱10,000" and rows["Earlier checks not yet cleared or ticked (1)"] == "₱10,003"
    import engines.db_engine as d
    with sqlite3.connect(d.DB_PATH) as conn:                                     # ticked on Daily payables: no longer listed
        conn.execute("UPDATE checks_issued SET manual_cleared_at = CURRENT_TIMESTAMP WHERE amount = 10003")
    assert not any(k.startswith("Earlier checks") for k in dict(content("admin@slicks.test", "Admin", today)["rows"]))
