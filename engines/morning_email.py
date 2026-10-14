"""The 8 AM email (owner, 2026-10-14: "is email notification possible every morning?" ... "family too, 8 am" ... "i think it is too much if all?").
Short on purpose, as on docs/mockups/v_morning_email.html: yesterday's sales, the cash, today's list, and one "Also" line only when something
needs them. Sent every day, quiet days too, so a missing email means something is wrong. Each person's email follows their pages: a section whose
page is Hidden from them is left out. It carries no document, no check number and no link but the one to the sign-in page."""
from __future__ import annotations

from datetime import date, timedelta
from html import escape

from engines.common import get_conn, write_tx

OVERDUE_DAYS = 30
RUN_OUT_DAYS = 7


def on() -> bool:
    from engines.settings_registry import int_value
    return int_value("email_morning") == 1


def people() -> list[dict]:
    """Every active person (never the Scanner account) and whether they receive it: yes until the System admin says no."""
    with get_conn() as conn:
        users = conn.execute("SELECT email, name, role FROM app_users WHERE is_active = 1 AND role != 'scanner' ORDER BY (role != 'admin'), name COLLATE NOCASE").fetchall()
        prefs = {e.lower(): bool(v) for e, v in conn.execute("SELECT user_email, receives FROM morning_email_prefs")}
        last = dict(conn.execute("SELECT user_email, MAX(day || CASE WHEN ok = 1 THEN ' sent' ELSE ' not sent: ' || COALESCE(note, '') END) FROM morning_emails_sent GROUP BY user_email"))
    return [{"email": e, "name": n, "role": r, "receives": prefs.get(e.lower(), True), "last": last.get(e.lower(), "")} for e, n, r in users]


def set_pref(email: str, receives: bool) -> tuple[bool, str]:
    from engines.permissions import require
    ok, why = require("settings.edit")
    if not ok:
        return False, why
    email = (email or "").strip().lower()
    with write_tx() as conn:
        if not conn.execute("SELECT 1 FROM app_users WHERE lower(trim(email)) = ?", (email,)).fetchone():
            return False, "No such person."
        conn.execute("INSERT INTO morning_email_prefs (user_email, receives) VALUES (?, ?) ON CONFLICT(user_email) DO UPDATE SET receives = excluded.receives", (email, int(receives)))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'MORNING_EMAIL_PREF', 'app_users', 0, ?)",
                     (f"{email}: {'receives' if receives else 'does not receive'} the morning email",))
    return True, f"{email} {'receives' if receives else 'no longer receives'} the morning email."


def _peso(v: float) -> str:
    v = float(v or 0)
    return f"{'-' if v < -0.5 else ''}₱{abs(v):,.0f}"


def _pct(now: float, before: float) -> str:
    if not before:
        return ""
    p = (now - before) / abs(before) * 100
    return f"{'▲' if p >= 0 else '▼'} {abs(p):.0f}%"


def content(email: str, name: str, today: date) -> dict:
    """What this person's email says today: only the sections whose page they may open."""
    from engines.permissions import hidden_for
    hidden = hidden_for(email)
    yesterday = today - timedelta(days=1)
    out: dict = {"name": (name or "").split(" ")[0] or "there", "today": today, "yesterday": yesterday, "also": []}
    if "sales" not in hidden:
        from engines.bi_service import _totals, shift_months
        y, before = _totals(yesterday, yesterday), _totals(yesterday - timedelta(days=1), yesterday - timedelta(days=1))
        month = _totals(today.replace(day=1), yesterday) if yesterday >= today.replace(day=1) else None
        last = _totals(shift_months(today.replace(day=1), 1), shift_months(yesterday, 1)) if month else None
        with get_conn() as conn:
            paid = dict(conn.execute("""SELECT CASE WHEN p.method = 'CHARGE' THEN 'credit' ELSE 'cash' END, SUM(p.amount) FROM sale_payments p
                                        JOIN sales s ON s.sale_id = p.sale_id WHERE s.status = 'SAVED' AND s.is_opening = 0 AND s.sale_date = ? GROUP BY 1""", (str(yesterday),)))
        out["sales"] = {"y": y, "vs": _pct(y["sales"], before["sales"]), "up": y["sales"] >= before["sales"], "cash": float(paid.get("cash", 0)), "credit": float(paid.get("credit", 0)),
                        "month": month, "month_vs": _pct(month["sales"], last["sales"]) if month else "", "month_up": bool(month) and month["sales"] >= last["sales"],
                        "last_label": shift_months(today, 1).strftime("%B")}
    if "bank-cash" not in hidden:
        try:
            from engines.bi_service import liquidity
            q = liquidity(today)
            out["cash"] = None if q.get("none") else {"bank": q["opening"], "as_of": q["as_of"], "lowest": q["lowest"], "lowest_day": q["lowest_day"]}
        except Exception:                               # noqa: BLE001 -- a figure that cannot be made is left out, the email still goes
            out["cash"] = None
    rows: list[tuple[str, str]] = []
    with get_conn() as conn:
        if "order-statements" not in hidden or "lenders" not in hidden:
            n, total = conn.execute("""SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM checks_issued WHERE status = 'Active' AND check_date = ?
                                       AND bank_cleared_date IS NULL AND manual_cleared_at IS NULL AND settled_outside_at IS NULL""", (str(today),)).fetchone()
            if n:
                rows.append((f"Checks dated today ({n})", _peso(total)))
        if "credit-cards" not in hidden:
            for card, due in conn.execute("""SELECT cc.card_name || ' ' || cc.card_number_last4, cs.total_amount_due FROM cc_statements cs
                                             JOIN credit_cards cc ON cc.card_id = cs.card_id
                                             WHERE cs.due_date = ? AND cs.status = 'Unpaid' AND COALESCE(cs.amount_paid, 0) <= 0.005""", (str(today),)):
                rows.append((f"Card due: {card}", _peso(due)))
        if "purchase-orders" not in hidden:
            n = conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE status IN ('ORDERED', 'PARTIAL') AND expected_delivery_date = ?", (str(today),)).fetchone()[0]
            if n:
                rows.append(("Deliveries expected", f"{n} order{'s' if n != 1 else ''}"))
        if "stock" not in hidden:
            n = conn.execute("""SELECT COUNT(*) FROM items i JOIN (SELECT item_id, -SUM(qty) AS sold FROM stock_ledger WHERE kind = 'OUT' AND moved_on >= ? GROUP BY item_id) m
                                ON m.item_id = i.id WHERE i.is_active = 1 AND i.on_hand > 0 AND m.sold > 0 AND i.on_hand * 30.0 / m.sold < ?""",
                             (str(today - timedelta(days=30)), RUN_OUT_DAYS)).fetchone()[0]
            if n:
                out["also"].append(f"{n} item{'s' if n != 1 else ''} will run out this week")
        charged = [r[0] for r in conn.execute("""SELECT DISTINCT s.customer_id FROM sales s JOIN sale_payments p ON p.sale_id = s.sale_id
                                                 WHERE p.method = 'CHARGE' AND s.customer_id IS NOT NULL AND s.status = 'SAVED'""")] if "collect" not in hidden else []
    if "credit-cards" not in hidden:
        try:
            from engines.card_reminders import missing_count
            n = missing_count(today)
            if n:
                rows.append((f"Card statement{'s' if n != 1 else ''} to type", str(n)))
        except Exception:                               # noqa: BLE001
            pass
    if charged:
        from engines.collect_rules import aging
        late = [(a["d60"] + a["d90"] + a["over"]) for a in (aging(c) for c in charged)]
        late = [v for v in late if v > 0.005]
        if late:
            out["also"].insert(0, f"{len(late)} customer{'s' if len(late) != 1 else ''} over {OVERDUE_DAYS} days ({_peso(sum(late))})")
    out["rows"] = rows
    out["show_today"] = not {"order-statements", "lenders", "credit-cards", "purchase-orders"} <= set(hidden)
    return out


def subject(c: dict) -> str:
    head = f"{c['today']:%a %b} {c['today'].day}"
    if "sales" in c:
        s = c["sales"]["y"]["sales"]
        return f"{head} · Sales {_peso(s)} yesterday" if s else f"{head} · No sales recorded yesterday"
    return f"{head} · Slick's ERP this morning"


def render(c: dict, site: str = "") -> tuple[str, str]:
    """(html, plain text). Plain table-and-inline-style HTML: that is what mail programs draw the same way."""
    h2 = 'style="font-size:14px;margin:0 0 7px;color:#0B4F8A"'
    sec = 'style="padding:12px 16px;border-bottom:1px solid #e5e7eb"'
    grey = 'style="color:#4b5563"'
    html = [f'<div style="font-family:Segoe UI,Arial,sans-serif;color:#1c2733;font-size:15px;max-width:440px;margin:0 auto;border:1px solid #d5dbe3;border-radius:10px;overflow:hidden">',
            f'<div style="background:#0f2a4a;color:#fff;padding:13px 16px"><b style="font-size:17px;display:block">Good morning, {escape(c["name"])}</b>'
            f'<span style="color:#f5c518;font-size:13px">{c["today"]:%A, %B} {c["today"].day}, {c["today"].year}</span></div>']
    text = [f"Good morning, {c['name']}", f"{c['today']:%A, %B} {c['today'].day}, {c['today'].year}", ""]
    if "sales" in c:
        s = c["sales"]
        y = s["y"]
        html.append(f'<div {sec}><h2 {h2}>Yesterday\'s sales</h2>')
        text.append("YESTERDAY'S SALES")
        if y["sales"] or y["invoices"]:
            colour = "#0b6e4f" if s["up"] else "#C62828"
            cells = [("Sales", _peso(y["sales"]), f'<span style="color:{colour}">{s["vs"]}</span>' if s["vs"] else ""), ("Margin", _peso(y["margin"]), f"{y['margin_pct']}%"),
                     ("Pieces", str(y["pieces"]), f"{y['invoices']} invoice{'s' if y['invoices'] != 1 else ''}"), ("Cash taken", _peso(s["cash"]), f"{_peso(s['credit'])} credit" if s["credit"] else "")]
            html.append('<table style="width:100%;border-collapse:separate;border-spacing:4px 0"><tr>' + "".join(
                f'<td style="background:#f3f5f8;border-radius:8px;padding:7px 8px;vertical-align:top;width:25%"><span style="display:block;font-size:11px;color:#4b5563">{a}</span>'
                f'<b style="font-size:15px">{b}</b><span style="display:block;font-size:11px;color:#4b5563">{d}</span></td>' for a, b, d in cells) + "</tr></table>")
            text.append(f"Sales {_peso(y['sales'])} {s['vs']} · margin {_peso(y['margin'])} ({y['margin_pct']}%) · {y['pieces']} pieces, {y['invoices']} invoices · cash {_peso(s['cash'])}"
                        + (f", credit {_peso(s['credit'])}" if s["credit"] else ""))
        else:
            html.append(f'<p style="margin:0;font-size:14px;color:#4b5563">No sales recorded for {c["yesterday"]:%A}.</p>')
            text.append(f"No sales recorded for {c['yesterday']:%A}.")
        if s["month"]:
            colour = "#0b6e4f" if s["month_up"] else "#C62828"
            vs = f' <span style="color:{colour}">{s["month_vs"]} vs {s["last_label"]}</span>' if s["month_vs"] else ""
            html.append(f'<p style="margin:7px 0 0;font-size:14px;color:#4b5563">{c["today"]:%B} so far: {_peso(s["month"]["sales"])}{vs}</p>')
            text.append(f"{c['today']:%B} so far: {_peso(s['month']['sales'])}" + (f" {s['month_vs']} vs {s['last_label']}" if s["month_vs"] else ""))
        html.append("</div>")
        text.append("")
    if c.get("cash"):
        k = c["cash"]
        html.append(f'<div {sec}><h2 {h2}>Cash</h2><p style="margin:0;font-size:14px">Bank <b>{_peso(k["bank"])}</b> <span {grey}>({k["as_of"]:%b} {k["as_of"].day})</span> · '
                    f'lowest ahead <b>{_peso(k["lowest"])}</b> <span {grey}>on {k["lowest_day"]:%b} {k["lowest_day"].day}</span></p></div>')
        text += ["CASH", f"Bank {_peso(k['bank'])} ({k['as_of']:%b} {k['as_of'].day}) · lowest ahead {_peso(k['lowest'])} on {k['lowest_day']:%b} {k['lowest_day'].day}", ""]
    if c.get("show_today"):
        html.append(f'<div {sec}><h2 {h2}>Today</h2>')
        text.append("TODAY")
        if c["rows"]:
            html.append('<table style="width:100%;border-collapse:collapse;font-size:14px">' + "".join(
                f'<tr><td style="padding:4px 0;border-bottom:1px solid #f0f2f5">{escape(a)}</td><td style="padding:4px 0;border-bottom:1px solid #f0f2f5;text-align:right;white-space:nowrap">{escape(b)}</td></tr>'
                for a, b in c["rows"]) + "</table>")
            text += [f"{a}: {b}" for a, b in c["rows"]]
        else:
            html.append(f'<p style="margin:0;font-size:14px;color:#4b5563">Nothing dated today.</p>')
            text.append("Nothing dated today.")
        html.append("</div>")
        text.append("")
    if c["also"]:
        html.append(f'<div style="background:#fff7e0;padding:10px 16px;font-size:14px;border-bottom:1px solid #e5e7eb"><b>Also:</b> {escape(" · ".join(c["also"]))}</div>')
        text += ["Also: " + " · ".join(c["also"]), ""]
    if site:
        html.append(f'<div style="padding:12px 16px;text-align:center"><a href="{escape(site)}/" style="display:inline-block;background:#0B4F8A;color:#fff;border-radius:8px;padding:8px 16px;'
                    f'font-weight:600;font-size:14px;text-decoration:none">Open Slick\'s ERP</a></div>')
        text.append(f"Open Slick's ERP: {site}/")
    html.append("</div>")
    return "".join(html), "\n".join(text)


def send_to(email: str, name: str, today: date, site: str = "") -> tuple[bool, str]:
    from engines import mail_out
    c = content(email, name, today)
    html, text = render(c, site)
    return mail_out.send(email, subject(c), html, text)


def run(now=None, force: bool = False, site: str = "") -> str:
    """Called every hour by the server's timer: sends at the hour set in Settings (Manila time), once a day to each person who receives it."""
    from engines import mail_out
    from engines.clock import manila_now
    from engines.settings_registry import int_value
    now = now or manila_now()
    if not on():
        return "The morning email is switched off."
    if not force and now.hour != int_value("email_morning_hour"):
        return "Not the email hour."
    if not mail_out.ready():
        return "Email sending is not set up yet."
    sent = failed = 0
    day = now.strftime("%Y-%m-%d")
    for p in people():
        if not p["receives"]:
            continue
        with write_tx() as conn:
            fresh = conn.execute("INSERT OR IGNORE INTO morning_emails_sent (day, user_email, sent_at) VALUES (?, ?, ?)", (day, p["email"].lower(), now.strftime("%Y-%m-%d %H:%M:%S"))).rowcount
        if not fresh:
            continue
        try:
            ok, note = send_to(p["email"], p["name"], now.date(), site)
        except Exception as e:                          # noqa: BLE001 -- one person's email never stops the others
            ok, note = False, str(e)[:200]
        with write_tx() as conn:
            conn.execute("UPDATE morning_emails_sent SET ok = ?, note = ? WHERE day = ? AND user_email = ?", (int(ok), None if ok else note[:200], day, p["email"].lower()))
        sent, failed = sent + ok, failed + (not ok)
    return f"{sent} morning email(s) sent" + (f", {failed} not sent" if failed else "") + "."
