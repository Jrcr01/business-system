"""Credit card reminders on the phone (owner, 2026-10-13: a daily payables message "is a good idea but it will only stresses us. instead have
credit cards only incoming statement, due date, annual fee"). Three kinds, each with its own switch in Settings > Notifications:

  * STATEMENT -- from two days after the statement was expected ("when the bank has usually sent it"), again every 3 days, until that month's
                 statement is recorded or the card is marked "No due this month": the same cards as the Missing statement check
  * DUE       -- on the due date only, twice that day (8 AM and 2 PM), while the statement is not paid in full
  * FEE       -- on the 1st of the month before the card's annual-fee month ("the month enough")

Who: the System admin for every card, each cardholder for their own cards, with "all cards" as a choice per person. run() is called every hour
by the server's timer and sends only at the two hours set; what was sent is remembered, so a second call the same hour sends nothing."""
from __future__ import annotations

from datetime import date

from engines.common import get_conn, write_tx

STATEMENT_AFTER_DAYS = 2
REPEAT_DAYS = 3
MONTHS = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def _on(key: str) -> bool:
    from engines.settings_registry import int_value
    return int_value(key) == 1


def _day(d: date) -> str:
    return f"{d.day} {MONTHS[d.month][:3]}"


def _first(name) -> str:
    return (str(name or "").split() or [""])[0]


def missing_statements(today: date) -> dict:
    """Cards to remind about a statement today (owner, 2026-10-13: "so i am not looking this everyday"): {card_id: (month, expected by, again)}.
    The same cards as Credit cards > Missing statement check -- had a statement last month (or a "No due"), none recorded for this one, not marked
    "No due this month" -- from two days after the day the statement was expected ("when the bank has usually sent it"), and then again every
    REPEAT_DAYS until it is recorded or marked."""
    from engines.cc_service import get_statement_coverage
    cov = get_statement_coverage(today)
    if cov.empty:
        return {}
    month, out = today.strftime("%Y-%m"), {}
    with get_conn() as conn:
        last = dict(conn.execute("SELECT card_id, MAX(substr(key, 9)) FROM card_reminders_sent WHERE kind = 'STATEMENT' AND key LIKE ? GROUP BY card_id", (month + "@%",)))
    for r in cov.to_dict("records"):
        expected = r.get("expected_by")
        expected = date.fromisoformat(expected[:10]) if isinstance(expected, str) and expected else None
        due_now = r["status"] == "Missing this month" or (r["status"] == "Not yet expected" and expected and (today - expected).days >= STATEMENT_AFTER_DAYS)
        if not due_now:
            continue
        before = last.get(int(r["card_id"]))
        if before and (today - date.fromisoformat(before)).days < REPEAT_DAYS:
            continue
        out[int(r["card_id"])] = (month, expected, bool(before))
    return out


def missing_count(today: date | None = None) -> int:
    """How many cards are on the Missing statement check right now (for Home): "Missing this month" only, as that page counts them."""
    from engines.cc_service import get_statement_coverage
    cov = get_statement_coverage(today)
    return 0 if cov.empty else int((cov["status"] == "Missing this month").sum())


def due_messages(today: date, slot: str) -> list[dict]:
    """What is worth saying today in this slot ('am' or 'pm'): [{kind, card_id, key, holder_id, title, body, url}]."""
    out = []
    missing = missing_statements(today) if slot == "am" and _on("notify_card_statement") else {}
    with get_conn() as conn:
        cards = conn.execute("""SELECT cc.card_id, cc.card_name || ' ' || cc.card_number_last4, cc.statement_day, cc.annual_fee_due_month, cc.cardholder_id, ch.name
                                FROM credit_cards cc LEFT JOIN cardholders ch ON ch.cardholder_id = cc.cardholder_id WHERE cc.is_active = 1""").fetchall()
        for card_id, name, st_day, fee_month, holder_id, holder in cards:
            whose = f"{_first(holder)}'s card" if holder else "The card"
            if card_id in missing:
                month, expected, again = missing[card_id]
                out.append({"kind": "STATEMENT", "card_id": card_id, "key": f"{month}@{today}", "holder_id": holder_id, "url": "/credit-cards/?tab=missing",
                            "title": f"{name} · statement {'still ' if again else ''}not recorded",
                            "body": (f"{whose} should have had its statement by {_day(expected)}. " if expected else f"{whose} has no statement recorded for {MONTHS[today.month]}. ")
                                    + "Record it, or press \"No due this month\" on Credit cards."})
            if _on("notify_card_due"):
                for due, total, paid in conn.execute("""SELECT due_date, total_amount_due, amount_paid FROM cc_statements
                                                        WHERE card_id = ? AND due_date = ? AND status IN ('Unpaid', 'Partially Paid')""", (card_id, str(today))):
                    left = round(float(total or 0) - float(paid or 0), 2)
                    if left > 0:
                        out.append({"kind": "DUE", "card_id": card_id, "key": str(due), "holder_id": holder_id, "url": "/checks/",
                                    "title": f"{name} · due today",
                                    "body": f"{whose}: ₱{left:,.2f} due today, {_day(today)}. Not yet marked paid."
                                            + (" Second reminder." if slot == "pm" else "")})
            if slot == "am" and _on("notify_card_fee") and fee_month and today.day == 1 and int(fee_month) == today.month % 12 + 1:
                out.append({"kind": "FEE", "card_id": card_id, "key": f"{today.year + (today.month == 12)}-{int(fee_month):02d}", "holder_id": holder_id, "url": "/credit-cards/",
                            "title": f"{name} · annual fee in {MONTHS[int(fee_month)]}",
                            "body": f"{whose} is charged its annual fee next month. Call the bank now to ask for a waiver."})
    return out


def people() -> list[dict]:
    """Every active person who may receive card reminders, with their choice: email, name, role, scope ('off' / 'own' / 'all'), cardholder_id, phones.
    Until the System admin chooses: the admin gets all cards, an owner their own (the cardholder with the same first name), anyone else none."""
    with get_conn() as conn:
        users = conn.execute("SELECT email, name, role FROM app_users WHERE is_active = 1 AND role != 'scanner' ORDER BY (role != 'admin'), name COLLATE NOCASE").fetchall()
        prefs = {e.lower(): (s, c) for e, s, c in conn.execute("SELECT user_email, scope, cardholder_id FROM card_reminder_prefs")}
        holders = conn.execute("SELECT cardholder_id, name FROM cardholders WHERE is_active = 1").fetchall()
        phones = dict(conn.execute("SELECT user_email, COUNT(*) FROM push_subscriptions GROUP BY user_email"))
    out = []
    for email, name, role in users:
        guess = next((h for h, n in holders if _first(n).lower() == _first(name).lower()), None)
        scope, holder = prefs.get(email.lower(), ("all" if role == "admin" else ("own" if role == "owner" and guess else "off"), guess))
        out.append({"email": email, "name": name, "role": role, "scope": scope, "cardholder_id": holder if holder is not None else guess, "phones": int(phones.get(email.lower(), 0))})
    return out


def set_pref(email: str, scope: str, cardholder_id) -> tuple[bool, str]:
    from engines.permissions import require
    allowed, why = require("users.manage")
    if not allowed:
        return False, why
    if scope not in ("off", "own", "all"):
        return False, "Choose none, own cards or all cards."
    with write_tx() as conn:
        row = conn.execute("SELECT name FROM app_users WHERE lower(email) = lower(?)", (email,)).fetchone()
        if not row:
            return False, "Person not found."
        holder = int(cardholder_id) if str(cardholder_id or "").isdigit() else None
        conn.execute("""INSERT INTO card_reminder_prefs (user_email, scope, cardholder_id) VALUES (?, ?, ?)
                        ON CONFLICT(user_email) DO UPDATE SET scope = excluded.scope, cardholder_id = excluded.cardholder_id""", (email.lower(), scope, holder))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CARD_REMINDER_CHOICE', 'app_users', NULL, ?)",
                     (f"{row[0]}: card reminders {scope}" + (f", cardholder {holder}" if holder else ""),))
    return True, f"{row[0]}: saved."


def _receivers(holder_id) -> list[str]:
    return [p["email"] for p in people() if p["scope"] == "all" or (p["scope"] == "own" and holder_id is not None and p["cardholder_id"] == holder_id)]


def missing() -> list[dict]:
    """Active cards that cannot be reminded about: no statement day, or no annual-fee month."""
    with get_conn() as conn:
        rows = conn.execute("""SELECT card_id, card_name || ' ' || card_number_last4, statement_day, annual_fee_due_month FROM credit_cards
                               WHERE is_active = 1 AND (statement_day IS NULL OR annual_fee_due_month IS NULL) ORDER BY card_name""").fetchall()
    return [{"card_id": a, "name": b, "no_statement_day": c is None, "no_fee_month": d is None} for a, b, c, d in rows]


def run(now=None, slot: str | None = None) -> str:
    """Called every hour. Sends at the two hours set in Settings (Manila time); a kind already sent for that card, day and slot is not sent again."""
    from engines import web_push
    from engines.clock import manila_now
    from engines.settings_registry import int_value
    now = now or manila_now()
    if slot is None:
        slot = "am" if now.hour == int_value("notify_hour") else ("pm" if now.hour == int_value("notify_second_hour") else None)
    if slot is None:
        return "Not a reminder hour."
    said = 0
    for m in due_messages(now.date(), slot):
        with write_tx() as conn:
            fresh = conn.execute("INSERT OR IGNORE INTO card_reminders_sent (kind, card_id, key, slot) VALUES (?, ?, ?, ?)", (m["kind"], m["card_id"], m["key"], slot)).rowcount
        if not fresh:
            continue
        phones = sum(web_push.send(email, m["title"], m["body"], m["url"], tag=f"{m['kind']}-{m['card_id']}") for email in _receivers(m["holder_id"]))
        with write_tx() as conn:
            conn.execute("UPDATE card_reminders_sent SET phones = ? WHERE kind = ? AND card_id = ? AND key = ? AND slot = ?", (phones, m["kind"], m["card_id"], m["key"], slot))
        said += 1
    return f"{said} reminder(s) sent ({slot})."
