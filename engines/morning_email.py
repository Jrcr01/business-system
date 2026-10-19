"""The 8 AM email (owner, 2026-10-14: "is email notification possible every morning?" ... "family too, 8 am" ... "i think it is too much if all?").
Short on purpose, as on docs/mockups/v_morning_email.html: yesterday's sales, today's list, and one "Also" line only when something
needs them. Sent every day, quiet days too, so a missing email means something is wrong. Each person's email follows their pages: a section whose
page is Hidden from them is left out. It carries no document, no check number and no link but the one to the sign-in page."""
from __future__ import annotations

from datetime import date, timedelta
from html import escape

from engines.common import get_conn, write_tx

OVERDUE_DAYS = 30
RUN_OUT_DAYS = 7
OLD_STATEMENT_DAYS = 7        # a card statement missing longer than this is repeated on Mondays only


def role_of(email: str) -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT role FROM app_users WHERE lower(trim(email)) = ?", ((email or "").strip().lower(),)).fetchone()
    return row[0] if row else ""


def _whose_cards(email: str) -> tuple[str, int | None]:
    """Which cards a person hears about: the same choice as the phone reminders (Settings > Notifications): 'all', 'own' (with their cardholder) or 'off'."""
    from engines.card_reminders import people
    me = next((p for p in people() if p["email"].lower() == (email or "").lower()), None)
    return (me["scope"], me["cardholder_id"]) if me else ("off", None)


def _cards_to_type(today: date, email: str = "") -> list[str]:
    """Cards whose statement should be out and is not typed yet, each with its cardholder's first name (owner, 2026-10-15: "what are these i cant see
    this"). A person gets the cards they get phone reminders for: all of them, their own, or none. A card missing for more than a week is named on
    Mondays only: a line that says the same thing every morning stops being read."""
    from engines.cc_service import get_statement_coverage
    scope, holder = _whose_cards(email) if email else ("all", None)
    if scope == "off" or (scope == "own" and not holder):
        return []
    cov = get_statement_coverage(today)
    with get_conn() as conn:
        holder_of = dict(conn.execute("SELECT card_id, cardholder_id FROM credit_cards"))
    out = []
    for r in ([] if cov.empty else cov.to_dict("records")):
        if scope == "own" and holder_of.get(int(r["card_id"])) != int(holder):
            continue
        exp = r.get("expected_by")
        exp = date.fromisoformat(exp[:10]) if isinstance(exp, str) and exp else None
        if r["status"] != "Missing this month":                  # the Missing statement check's own rule and its "Days to wait" (owner, 2026-10-19:
            continue                                             # "this is the only card included in the missing statement check")
        age = (today - exp).days if exp else OLD_STATEMENT_DAYS + 1
        if age <= OLD_STATEMENT_DAYS or today.weekday() == 0:
            who = str(r.get("cardholder") or "").split(" ")[0]
            out.append(f"{r['card_name']} {r['card_number_last4']}" + (f" ({who})" if who else ""))
    return out


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
        y, before = _totals(yesterday, yesterday), _totals(yesterday - timedelta(days=7), yesterday - timedelta(days=7))     # the same weekday a week before: a fair comparison
        month = _totals(today.replace(day=1), yesterday) if yesterday >= today.replace(day=1) else None
        last = _totals(shift_months(today.replace(day=1), 1), shift_months(yesterday, 1)) if month else None
        with get_conn() as conn:
            paid = dict(conn.execute("""SELECT CASE WHEN p.method = 'CHARGE' THEN 'credit' ELSE 'cash' END, SUM(p.amount) FROM sale_payments p
                                        JOIN sales s ON s.sale_id = p.sale_id WHERE s.status = 'SAVED' AND s.is_opening = 0 AND s.sale_date = ? GROUP BY 1""", (str(yesterday),)))
        out["sales"] = {"y": y, "vs": _pct(y["sales"], before["sales"]), "up": y["sales"] >= before["sales"], "cash": float(paid.get("cash", 0)), "credit": float(paid.get("credit", 0)),
                        "month": month, "month_vs": _pct(month["sales"], last["sales"]) if month else "", "month_up": bool(month) and month["sales"] >= last["sales"],
                        "last_label": shift_months(today, 1).strftime("%B"), "vs_label": f"last {yesterday:%a}"}
        if today.weekday() == 0:                                  # Mondays: last week in one line
            mon = today - timedelta(days=7)
            wk, prev = _totals(mon, mon + timedelta(days=6)), _totals(mon - timedelta(days=7), mon - timedelta(days=1))
            out["sales"]["week"] = {"sales": wk["sales"], "vs": _pct(wk["sales"], prev["sales"]), "up": wk["sales"] >= prev["sales"]}
        if today.day == 1 and "income-statement" not in hidden:    # the 1st: last month's profit in one line
            try:
                from engines.income_statement import month_report
                out["sales"]["profit"] = {"label": yesterday.strftime("%B"), "profit": month_report(yesterday.strftime("%Y-%m"))["profit"]}
            except Exception:                                   # noqa: BLE001
                pass
    # no cash section (owner, 2026-10-14: "i think cash line should be remove": the bank figure is only as fresh as the last statement uploaded)
    rows: list[tuple[str, str]] = []
    with get_conn() as conn:
        if "order-statements" not in hidden or "lenders" not in hidden:
            n, total = conn.execute("""SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM checks_issued WHERE status = 'Active' AND check_date = ?
                                       AND bank_cleared_date IS NULL AND manual_cleared_at IS NULL AND settled_outside_at IS NULL""", (str(today),)).fetchone()
            if n:
                rows.append((f"Checks dated today ({n})", _peso(total)))
            # owner, 2026-10-16 ("it should include those not cleared yet or ticked"): checks dated before today that the bank has not cleared and nobody
            # ticked on Daily payables are still money that can go out today
            n, total = conn.execute("""SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM checks_issued WHERE status = 'Active' AND check_date < ?
                                       AND bank_cleared_date IS NULL AND manual_cleared_at IS NULL AND settled_outside_at IS NULL""", (str(today),)).fetchone()
            if n:
                rows.append((f"Earlier checks not yet cleared or ticked ({n})", _peso(total)))
        if "credit-cards" not in hidden:
            scope, holder = _whose_cards(email)                  # the person's own cards, or all of them (as the phone reminders)
            for card, due, who, hid in conn.execute("""SELECT cc.card_name || ' ' || cc.card_number_last4, cs.total_amount_due, COALESCE(h.name, ''), cc.cardholder_id
                                             FROM cc_statements cs JOIN credit_cards cc ON cc.card_id = cs.card_id LEFT JOIN cardholders h ON h.cardholder_id = cc.cardholder_id
                                             WHERE cs.due_date = ? AND cs.status = 'Unpaid' AND COALESCE(cs.amount_paid, 0) <= 0.005""", (str(today),)).fetchall():
                if scope == "all" or (scope == "own" and holder and hid == int(holder)):
                    rows.append((f"Card due: {card}" + (f" ({who.split(' ')[0]})" if who else ""), _peso(due)))
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
            cards = _cards_to_type(today, email)
            if cards:
                rows.append((f"Card statement{'s' if len(cards) != 1 else ''} to type", ", ".join(cards[:3]) + (f" +{len(cards) - 3} more" if len(cards) > 3 else "")))
        except Exception:                               # noqa: BLE001
            pass
    if charged:
        from engines.collect_rules import aging
        late = [(a["d60"] + a["d90"] + a["over"]) for a in (aging(c) for c in charged)]
        late = [v for v in late if v > 0.005]
        if late:
            out["also"].insert(0, f"{len(late)} customer{'s' if len(late) != 1 else ''} over {OVERDUE_DAYS} days ({_peso(sum(late))})")
    if role_of(email) == "admin":                            # the System admin alone hears what went wrong on the server yesterday
        try:
            from engines.server_health import errors_on
            found = errors_on(yesterday)
            if found and found[0]:
                n, top = found
                out["also"].append(f"{n} error{'s' if n != 1 else ''} on the server yesterday" + (f": {top[0]}" if top else ""))
        except Exception:                                   # noqa: BLE001
            pass
    out["rows"] = rows
    out["show_today"] = not {"order-statements", "lenders", "credit-cards", "purchase-orders"} <= set(hidden)
    return out


def subject(c: dict) -> str:
    head = f"{c['today']:%a %b} {c['today'].day}"
    if "sales" in c:
        s = c["sales"]["y"]["sales"]
        if s:
            return f"{head} · Sales {_peso(s)} yesterday"
    if c.get("rows"):                                   # a quiet sales day: lead with what today holds
        a, b = c["rows"][0]
        return f"{head} · {a}" + (f" {b}" if b.startswith("₱") else "")
    return f"{head} · Nothing dated today"


def render(c: dict, site: str = "") -> tuple[str, str]:
    """(html, plain text). Plain table-and-inline-style HTML: that is what mail programs draw the same way."""
    h2 = 'style="font-size:14px;margin:0 0 7px;color:#0B4F8A"'
    sec = 'style="padding:12px 16px;border-bottom:1px solid #e5e7eb"'
    grey = 'style="color:#4b5563"'
    html = [f'<div style="font-family:Segoe UI,Arial,sans-serif;color:#1c2733;font-size:15px;max-width:440px;margin:0 auto;border:1px solid #d5dbe3;border-radius:10px;overflow:hidden">',
            f'<div style="background:#0f2a4a;color:#fff;padding:13px 16px"><b style="font-size:17px;display:block">Good morning, {escape(c["name"])}</b>'
            f'<span style="color:#f5c518;font-size:13px">{c["today"]:%A, %B} {c["today"].day}, {c["today"].year}</span></div>']
    # what the inbox shows beside the subject, before the email is opened: the day in one line, not the greeting run into the date
    peek = []
    if "sales" in c:
        y = c["sales"]["y"]
        peek.append(f"Sales {_peso(y['sales'])} yesterday" if y["sales"] else f"No sales recorded for {c['yesterday']:%A}")
    peek += [f"{a} {b}" for a, b in c.get("rows", [])[:2] if len(b) <= 14] + c["also"][:1]
    html.insert(0, f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;font-size:1px;line-height:1px">{escape(" · ".join(peek) or "Nothing dated today")}</div>')
    text = [f"Good morning, {c['name']}", f"{c['today']:%A, %B} {c['today'].day}, {c['today'].year}", ""]
    if "sales" in c:
        s = c["sales"]
        y = s["y"]
        html.append(f'<div {sec}><h2 {h2}>Yesterday\'s sales</h2>')
        text.append("YESTERDAY'S SALES")
        if y["sales"] or y["invoices"]:
            colour = "#0b6e4f" if s["up"] else "#C62828"
            cells = [("Sales", _peso(y["sales"]), f'<span style="color:{colour}">{s["vs"]} vs {s["vs_label"]}</span>' if s["vs"] else ""), ("Margin", _peso(y["margin"]), f"{y['margin_pct']}%"),
                     ("Pieces", str(y["pieces"]), f"{y['invoices']} invoice{'s' if y['invoices'] != 1 else ''}"), ("Cash taken", _peso(s["cash"]), f"{_peso(s['credit'])} credit" if s["credit"] else "")]
            html.append('<table style="width:100%;border-collapse:separate;border-spacing:4px 0"><tr>' + "".join(
                f'<td style="background:#f3f5f8;border-radius:8px;padding:7px 8px;vertical-align:top;width:25%"><span style="display:block;font-size:11px;color:#4b5563">{a}</span>'
                f'<b style="font-size:15px">{b}</b><span style="display:block;font-size:11px;color:#4b5563">{d}</span></td>' for a, b, d in cells) + "</tr></table>")
            text.append(f"Sales {_peso(y['sales'])} {s['vs']}{' vs ' + s['vs_label'] if s['vs'] else ''} · margin {_peso(y['margin'])} ({y['margin_pct']}%) · {y['pieces']} pieces, {y['invoices']} invoices · cash {_peso(s['cash'])}"
                        + (f", credit {_peso(s['credit'])}" if s["credit"] else ""))
        else:
            html.append(f'<p style="margin:0;font-size:14px;color:#4b5563">No sales recorded for {c["yesterday"]:%A}.</p>')
            text.append(f"No sales recorded for {c['yesterday']:%A}.")
        if s["month"]:
            colour = "#0b6e4f" if s["month_up"] else "#C62828"
            vs = f' <span style="color:{colour}">{s["month_vs"]} vs {s["last_label"]}</span>' if s["month_vs"] else ""
            html.append(f'<p style="margin:7px 0 0;font-size:14px;color:#4b5563">{c["today"]:%B} so far: {_peso(s["month"]["sales"])}{vs}</p>')
            text.append(f"{c['today']:%B} so far: {_peso(s['month']['sales'])}" + (f" {s['month_vs']} vs {s['last_label']}" if s["month_vs"] else ""))
        for key, words in (("week", "Last week"), ("profit", None)):
            w = s.get(key)
            if not w:
                continue
            if key == "week":
                colour = "#0b6e4f" if w["up"] else "#C62828"
                vs = f' <span style="color:{colour}">{w["vs"]} vs the week before</span>' if w["vs"] else ""
                html.append(f'<p style="margin:4px 0 0;font-size:14px;color:#4b5563">Last week: {_peso(w["sales"])}{vs}</p>')
                text.append(f"Last week: {_peso(w['sales'])}" + (f" {w['vs']} vs the week before" if w["vs"] else ""))
            else:
                colour = "#0b6e4f" if w["profit"] >= 0 else "#C62828"
                html.append(f'<p style="margin:4px 0 0;font-size:14px;color:#4b5563">{w["label"]} profit: <b style="color:{colour}">{_peso(w["profit"])}</b></p>')
                text.append(f"{w['label']} profit: {_peso(w['profit'])}")
        html.append("</div>")
        text.append("")
    if c.get("show_today"):
        html.append(f'<div {sec}><h2 {h2}>Today</h2>')
        text.append("TODAY")
        if c["rows"]:
            # an amount or a count sits at the right of its line; a list of names goes under its label and wraps (owner, 2026-10-14: on the phone a long
            # list of cards squeezed its label into three lines and ran off the screen)
            cell = "padding:5px 0;border-bottom:1px solid #f0f2f5"
            html.append('<table style="width:100%;border-collapse:collapse;font-size:14px">' + "".join(
                (f'<tr><td style="{cell}">{escape(a)}</td><td style="{cell};text-align:right;white-space:nowrap;padding-left:10px">{escape(b)}</td></tr>' if len(b) <= 14 else
                 f'<tr><td colspan="2" style="{cell}">{escape(a)}<div style="color:#4b5563;font-size:13px;margin-top:2px">{escape(b)}</div></td></tr>')
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
    html.append('<div style="padding:10px 16px;font-size:12px;color:#4b5563">This is an automatic email. Replies are not read.</div></div>')     # owner, 2026-10-14
    text.append("This is an automatic email. Replies are not read.")
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
    if not force and now.weekday() >= 5 and not int_value("email_morning_weekends"):
        return "Weekend: no morning email."                      # owner, 2026-10-17: "no email on weekends"
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
