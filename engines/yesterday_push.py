"""The morning "Yesterday" phone notification (owner, 2026-10-19: "me and owners, sales and purchases, send no sales and purchases too"; "our shop is
always open. remind my family to input as soon as possible"; "not 2 pm but after working hours"). At the card reminders' hour: yesterday's sales and
the purchases received. With no sale recorded for yesterday, it asks to type them; at the end of working hours, one more reminder if still none.
Each person can be switched off on Settings > Notifications. A tap opens Sales today on yesterday."""
from __future__ import annotations

from datetime import date, timedelta

from engines.common import get_conn, write_tx

WORDS = {"CASH": "cash", "CHECK": "check", "TRANSFER": "GCash/transfer", "CARD": "card", "CHARGE": "charge"}


def people() -> list[dict]:
    """The System admin and the owners, each with their choice (yes until switched off)."""
    with get_conn() as conn:
        users = conn.execute("SELECT email, name, role FROM app_users WHERE is_active = 1 AND role IN ('admin', 'owner') "
                             "ORDER BY (role != 'admin'), name COLLATE NOCASE").fetchall()
        prefs = {e.lower(): bool(v) for e, v in conn.execute("SELECT user_email, receives FROM yesterday_push_prefs")}
    return [{"email": e, "name": n, "role": r, "receives": prefs.get(e.lower(), True)} for e, n, r in users]


def set_pref(email: str, receives: bool) -> tuple[bool, str]:
    from engines.permissions import require
    ok, why = require("settings.edit")
    if not ok:
        return False, why
    email = (email or "").strip().lower()
    with write_tx() as conn:
        if not conn.execute("SELECT 1 FROM app_users WHERE lower(trim(email)) = ?", (email,)).fetchone():
            return False, "No such person."
        conn.execute("INSERT INTO yesterday_push_prefs (user_email, receives) VALUES (?, ?) ON CONFLICT(user_email) DO UPDATE SET receives = excluded.receives",
                     (email, int(receives)))
    return True, f"{email} {'receives' if receives else 'no longer receives'} the Yesterday notification."


def _peso(v: float) -> str:
    return f"₱{v:,.0f}" if abs(v - round(v)) < 0.005 else f"₱{v:,.2f}"


def summary(day: date) -> dict:
    """The day's sales (saved invoices, less returns) and purchases received: counts, totals, profit, money by how it was paid, and the documents."""
    with get_conn() as conn:
        n, total, cost = conn.execute("SELECT COUNT(*), COALESCE(SUM(total), 0), COALESCE(SUM(cost_total), 0) FROM sales WHERE status = 'SAVED' AND sale_date = ?",
                                      (str(day),)).fetchone()
        returned = conn.execute("SELECT COALESCE(SUM(r.amount), 0) FROM sale_returns r JOIN sales s ON s.sale_id = r.sale_id WHERE s.status = 'SAVED' AND s.sale_date = ?",
                                (str(day),)).fetchone()[0]
        money = conn.execute("""SELECT p.method, SUM(p.amount) FROM sale_payments p JOIN sales s ON s.sale_id = p.sale_id WHERE s.status = 'SAVED' AND s.sale_date = ?
                                GROUP BY p.method ORDER BY SUM(p.amount) DESC""", (str(day),)).fetchall()
        bought = conn.execute("""SELECT COALESCE(i.doc_ref, r.supplier_invoice_no, 'no document'), COALESCE(i.amount, r.actual_amount, 0), c.creditor_name
                                 FROM po_receipts r JOIN purchase_orders p ON p.po_id = r.po_id JOIN creditors c ON c.creditor_id = p.creditor_id
                                 LEFT JOIN invoices i ON i.invoice_id = r.invoice_id WHERE r.received_date = ? ORDER BY r.receipt_id""", (str(day),)).fetchall()
    seen, docs = set(), []
    for doc, amt, who in bought:                                 # one payable may cover two orders: its document is counted once
        if doc not in seen:
            seen.add(doc)
            docs.append({"doc": doc, "amount": float(amt or 0), "supplier": who})
    net = round(float(total) - float(returned), 2)
    return {"invoices": int(n), "sales": net, "profit": round(net - float(cost), 2), "money": [(m, float(a)) for m, a in money],
            "purchases": docs, "bought": round(sum(d["amount"] for d in docs), 2)}


def message(day: date, slot: str) -> dict | None:
    """{'title', 'body'} for the morning report (slot 'am') or the after-hours reminder (slot 'pm'; None once yesterday has a sale)."""
    s = summary(day)
    when = f"{day:%a} {day:%m/%d}"
    if s["purchases"]:
        k = len(s["purchases"])
        buy = (f"Purchases: {k} deliver{'y' if k == 1 else 'ies'} · {_peso(s['bought'])} ("
               + ", ".join(d["doc"] for d in s["purchases"][:3]) + (" …" if k > 3 else "") + ")")
    else:
        buy = "No purchases received yesterday."
    if slot == "pm":
        if s["invoices"]:
            return None
        return {"title": f"Reminder: yesterday's sales · {when}", "body": "Still no sales recorded for yesterday. Please type them today."}
    if not s["invoices"]:
        return {"title": f"Yesterday's sales not typed in yet · {when}",
                "body": "No sales recorded for yesterday. Please type yesterday's invoices as soon as possible.\n" + buy}
    paid = " · ".join(f"{WORDS.get(m, m.lower())} {_peso(a)}" for m, a in s["money"])
    first = f"Sales: {s['invoices']} invoice{'s' if s['invoices'] != 1 else ''} · {_peso(s['sales'])} · profit {_peso(s['profit'])}"
    return {"title": f"Yesterday · {when}", "body": first + "\n" + (paid + "\n" if paid else "") + buy}


def run(now=None, slot: str | None = None) -> str:
    """Called every hour (the card reminders' timer): the report at the morning reminder hour, the reminder at the end of working hours."""
    from engines import web_push
    from engines.clock import manila_now
    from engines.settings_registry import int_value
    now = now or manila_now()
    if slot is None:
        slot = "am" if now.hour == int_value("notify_hour") else ("pm" if now.hour == int_value("work_end_hour") else None)
    if slot is None:
        return "Not a Yesterday hour."
    day = now.date() - timedelta(days=1)
    m = message(day, slot)
    if m is None:
        return "Yesterday has sales: no reminder."
    said = 0
    for p in people():
        if not p["receives"]:
            continue
        with write_tx() as conn:
            fresh = conn.execute("INSERT OR IGNORE INTO yesterday_push_sent (day, slot, user_email) VALUES (?, ?, ?)", (str(day), slot, p["email"].lower())).rowcount
        if not fresh:
            continue
        phones = web_push.send(p["email"], m["title"], m["body"], f"/phone/day/?d={day.isoformat()}", tag=f"yesterday-{slot}")
        with write_tx() as conn:
            conn.execute("UPDATE yesterday_push_sent SET phones = ? WHERE day = ? AND slot = ? AND user_email = ?", (phones, str(day), slot, p["email"].lower()))
        said += 1
    return f"Yesterday ({slot}): sent to {said} person(s)."
