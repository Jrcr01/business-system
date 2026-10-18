"""Stage C, customers (owner, 2026-10-17; mock-up docs/mockups/f_stage_c_customers.html): quotations, agreed prices, follow-ups.

A quotation IS the quick quote kept for a customer ("for quotation we need the quick one"): a size, the brands on hand at cost, a markup -- then
"Save as quotation" with a customer and a good-until date. It moves no stock and is not money owed. When the customer says yes, Record a sale opens
with its lines, and the sale marks it sold. An agreed price (one item's fixed price, or a percent off a brand or everything) is used by itself when
that customer is quoted. Follow-ups are the quotes waiting for an answer and the calls someone said to make again; "Note a contact" records a call.
Nothing here blocks anything."""
from __future__ import annotations

from datetime import date, timedelta

from engines.clock import manila_today
from engines.common import get_conn, write_tx
from engines.db_engine import next_doc_number
from engines.permissions import require as require_permission

GOOD_DAYS = 7                          # a quote's default life (owner's answer pending; a setting: quote_good_days)
STATE = {"WAITING": "waiting", "SOLD": "sold", "LAPSED": "lapsed", "CANCELLED": "cancelled"}


def _text(v) -> str:
    return " ".join(str(v or "").split())


def _day(v) -> date | None:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None


def good_days() -> int:
    from engines.settings_registry import int_value
    try:
        return int_value("quote_good_days")
    except KeyError:
        return GOOD_DAYS


def _customer_of(conn, customer_id, name: str) -> tuple[int | None, str]:
    """A known customer (by id, or by its exact name), else a walk-in kept by the name typed."""
    if customer_id:
        row = conn.execute("SELECT customer_id, customer_name FROM customers WHERE customer_id = ? AND is_active = 1", (int(customer_id),)).fetchone()
        if row:
            return int(row[0]), row[1]
    name = _text(name)
    if name:
        row = conn.execute("SELECT customer_id, customer_name FROM customers WHERE LOWER(customer_name) = LOWER(?) AND is_active = 1", (name,)).fetchone()
        if row:
            return int(row[0]), row[1]
    return None, name


# --- agreed prices ------------------------------------------------------------------------------------------------------------------------------------

def agreed(customer_id: int | None, item_ids: list[int], on: date | None = None) -> dict[int, dict]:
    """For each item, this customer's agreed price in force: {item_id: {'price': fixed or None, 'pct': % off or None, 'label'}}. One item's own
    agreement wins over its brand's, the brand's over 'everything'."""
    if not customer_id or not item_ids:
        return {}
    on = str(on or manila_today())
    with get_conn() as conn:
        rows = conn.execute("""SELECT item_id, brand, price, pct_off FROM customer_prices WHERE customer_id = ? AND is_active = 1 AND since <= ?
                               AND (until IS NULL OR until >= ?) ORDER BY cp_id""", (int(customer_id), on, on)).fetchall()
        brands = {int(i): (b or "").lower() for i, b in conn.execute(f"SELECT id, brand FROM items WHERE id IN ({','.join('?' for _ in item_ids)})", [int(i) for i in item_ids])}
    out = {}
    for iid in item_ids:
        iid = int(iid)
        hit = next((r for r in rows if r[0] == iid), None) or next((r for r in rows if r[0] is None and (r[1] or "").lower() == brands.get(iid) and r[1]), None) \
            or next((r for r in rows if r[0] is None and not r[1]), None)
        if hit:
            out[iid] = {"price": hit[2], "pct": hit[3], "label": f"agreed {hit[2]:,.0f}" if hit[2] is not None else f"agreed {hit[3]:g}% off"}
    return out


def set_agreed(customer_id: int, item_id: int | None, brand: str, price, pct, since=None, until=None) -> tuple[bool, str]:
    allowed, why = require_permission("sales.use")
    if not allowed:
        return False, why
    brand = _text(brand)
    try:
        price = float(str(price).replace(",", "")) if str(price or "").strip() else None
        pct = float(str(pct).replace("%", "")) if str(pct or "").strip() else None
    except ValueError:
        return False, "Type the price or the percent as a number."
    if (price is None) == (pct is None):
        return False, "Give either a fixed price (for one item) or a percent off, not both."
    if price is not None and not item_id:
        return False, "A fixed price is for one item: pick the item, or give a percent off instead."
    if pct is not None and not 0 < pct < 100:
        return False, "The percent off is between 0 and 100."
    since, until = _day(since) or manila_today(), _day(until)
    with write_tx() as conn:
        name = conn.execute("SELECT customer_name FROM customers WHERE customer_id = ?", (int(customer_id),)).fetchone()
        if not name:
            return False, "Customer not found."
        conn.execute("""UPDATE customer_prices SET is_active = 0 WHERE customer_id = ? AND is_active = 1 AND COALESCE(item_id, 0) = ? AND LOWER(COALESCE(brand, '')) = LOWER(?)""",
                     (int(customer_id), int(item_id or 0), brand if not item_id else ""))
        conn.execute("INSERT INTO customer_prices (customer_id, item_id, brand, price, pct_off, since, until, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, acting_user())",
                     (int(customer_id), int(item_id) if item_id else None, None if item_id else (brand or None), price, pct, str(since), str(until) if until else None))
        what = (conn.execute("SELECT name FROM items WHERE id = ?", (int(item_id),)).fetchone()[0] if item_id else (f"all {brand}" if brand else "everything"))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'AGREED_PRICE_SET', 'customers', ?, ?)",
                     (int(customer_id), f"{name[0]}: {what} at " + (f"{price:,.2f}" if price is not None else f"{pct:g}% off")))
    return True, f"{name[0]}: agreed price saved for {what}."


def stop_agreed(cp_id: int) -> tuple[bool, str]:
    allowed, why = require_permission("sales.use")
    if not allowed:
        return False, why
    with write_tx() as conn:
        conn.execute("UPDATE customer_prices SET is_active = 0 WHERE cp_id = ?", (int(cp_id),))
    return True, "Agreed price stopped."


def agreed_list(customer_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""SELECT p.cp_id, i.name, p.brand, p.price, p.pct_off, p.since, p.until FROM customer_prices p LEFT JOIN items i ON i.id = p.item_id
                               WHERE p.customer_id = ? AND p.is_active = 1 ORDER BY p.item_id IS NULL, i.name, p.brand""", (int(customer_id),)).fetchall()
    return [{"id": a, "what": b or (f"All {c}" if c else "Everything, unless listed"), "price": d, "pct": e, "since": f, "until": g} for a, b, c, d, e, f, g in rows]


# --- quotations ---------------------------------------------------------------------------------------------------------------------------------------

def save_quote(customer_id, customer_name: str, good_until, lines: list[dict], note: str = "") -> tuple[bool, str, int | None]:
    """lines: [{'item_id', 'qty', 'price'}]. The description and the cost are taken from the item now (the cost is never shown to the customer)."""
    allowed, why = require_permission("sales.use")
    if not allowed:
        return False, why, None
    today = manila_today()
    until = _day(good_until) or today + timedelta(days=good_days())
    if until < today:
        return False, "Good until cannot be before today.", None
    clean = []
    for ln in lines or []:
        try:
            iid, qty, price = int(ln.get("item_id") or 0), float(str(ln.get("qty") or 0).replace(",", "")), float(str(ln.get("price") or 0).replace(",", ""))
        except (TypeError, ValueError):
            return False, "A quantity or a price is not a number.", None
        if iid and qty > 0 and price >= 0:
            clean.append((iid, qty, price))
    if not clean:
        return False, "Tick at least one brand to quote.", None
    with write_tx() as conn:
        cid, name = _customer_of(conn, customer_id, customer_name)
        if not cid and not name:
            return False, "Type the customer's name: a quotation is kept for someone.", None
        used = [int(r[0][3:]) for r in conn.execute("SELECT qt_no FROM quotations WHERE qt_no LIKE 'QT-%'") if r[0][3:].isdigit()]
        number = f"QT-{next_doc_number(conn, 'QT', 0, max(used, default=0)):04d}"
        qid = conn.execute("""INSERT INTO quotations (qt_no, customer_id, customer_name, quote_date, good_until, note, created_by) VALUES (?, ?, ?, ?, ?, ?, acting_user())
                              RETURNING qt_id""", (number, cid, name, str(today), str(until), _text(note) or None)).fetchone()[0]
        total = 0.0
        for iid, qty, price in clean:
            row = conn.execute("SELECT name, COALESCE(NULLIF(next_cost, 0), avg_cost, 0) FROM items WHERE id = ?", (iid,)).fetchone()
            if not row:
                continue
            conn.execute("INSERT INTO quotation_lines (qt_id, item_id, description, qty, unit_price, unit_cost) VALUES (?, ?, ?, ?, ?, ?)",
                         (qid, iid, row[0], qty, price, float(row[1] or 0)))
            total += qty * price
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'QUOTATION_SAVED', 'quotations', ?, ?)",
                     (qid, f"{number} for {name}: ₱{total:,.2f}, good until {until.strftime('%m/%d/%Y')}"))
    return True, f"{number} saved for {name}: ₱{total:,.2f}, good until {until.strftime('%m/%d/%Y')}.", int(qid)


def _state(status: str, good_until: str, today: date) -> str:
    if status == "SOLD":
        return "SOLD"
    if status == "CANCELLED":
        return "CANCELLED"
    return "LAPSED" if str(good_until) < str(today) else "WAITING"


def quotes(show: str = "waiting", customer_id: int | None = None) -> list[dict]:
    """The list: show = waiting | sold | lapsed | all."""
    today = manila_today()
    with get_conn() as conn:
        rows = conn.execute("""SELECT q.qt_id, q.qt_no, q.customer_id, q.customer_name, q.quote_date, q.good_until, q.status, q.sale_id, s.si_no,
                                      (SELECT COALESCE(SUM(qty * unit_price), 0) FROM quotation_lines l WHERE l.qt_id = q.qt_id)
                               FROM quotations q LEFT JOIN sales s ON s.sale_id = q.sale_id"""
                            + (" WHERE q.customer_id = ?" if customer_id else "") + " ORDER BY q.qt_id DESC LIMIT 300",
                            (int(customer_id),) if customer_id else ()).fetchall()
    out = []
    for qid, no, cid, name, d, until, status, sale_id, si, total in rows:
        st = _state(status, until, today)
        if show != "all" and STATE.get(st) != show:
            continue
        days = (today - _day(d)).days if _day(d) else 0
        left = (_day(until) - today).days if _day(until) else 0
        out.append({"id": int(qid), "no": no, "customer_id": cid, "customer": name, "date": d, "good_until": until, "state": st, "label": STATE[st],
                    "sale_id": sale_id, "si_no": si, "total": round(float(total), 2), "days": days, "left": left})
    return out


def counts() -> dict:
    allq = quotes("all")
    return {k: sum(1 for q in allq if q["label"] == k) for k in ("waiting", "sold", "lapsed")}


def get_quote(qt_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("""SELECT qt_id, qt_no, customer_id, customer_name, quote_date, good_until, status, sale_id, note, cancel_reason, created_by
                              FROM quotations WHERE qt_id = ?""", (int(qt_id),)).fetchone()
        if not row:
            return None
        lines = conn.execute("SELECT line_id, item_id, description, qty, unit_price, unit_cost FROM quotation_lines WHERE qt_id = ? ORDER BY line_id", (int(qt_id),)).fetchall()
        si = conn.execute("SELECT si_no FROM sales WHERE sale_id = ?", (row[7],)).fetchone() if row[7] else None
    keys = ("id", "no", "customer_id", "customer", "date", "good_until", "status", "sale_id", "note", "cancel_reason", "by")
    q = dict(zip(keys, row))
    q["state"] = _state(q["status"], q["good_until"], manila_today())
    q["label"] = STATE[q["state"]]
    q["si_no"] = si[0] if si else None
    q["lines"] = [{"line_id": a, "item_id": b, "description": c, "qty": float(d), "price": float(e), "cost": float(f), "amount": round(float(d) * float(e), 2)}
                  for a, b, c, d, e, f in lines]
    q["total"] = round(sum(ln["amount"] for ln in q["lines"]), 2)
    return q


def cancel_quote(qt_id: int, reason: str) -> tuple[bool, str]:
    allowed, why = require_permission("sales.use")
    if not allowed:
        return False, why
    reason = _text(reason)
    if not reason:
        return False, "Say why the quotation is cancelled (e.g. 'bought elsewhere')."
    with write_tx() as conn:
        row = conn.execute("SELECT qt_no, status FROM quotations WHERE qt_id = ?", (int(qt_id),)).fetchone()
        if not row or row[1] != "OPEN":
            return False, "Only a quotation still open can be cancelled."
        conn.execute("UPDATE quotations SET status = 'CANCELLED', cancel_reason = ? WHERE qt_id = ?", (reason, int(qt_id)))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'QUOTATION_CANCELLED', 'quotations', ?, ?)",
                     (int(qt_id), f"{row[0]}: {reason}"))
    return True, f"{row[0]} cancelled."


def mark_sold(qt_id: int, sale_id: int) -> None:
    """The sale made from it (Record a sale opened from the quotation) marks it sold."""
    with write_tx() as conn:
        conn.execute("UPDATE quotations SET status = 'SOLD', sale_id = ? WHERE qt_id = ? AND status = 'OPEN'", (int(sale_id), int(qt_id)))


def share_text(q: dict) -> str:
    """For Viber / SMS: selling prices only, never a cost or a margin."""
    lines = [f"Slick's Tire Supply · quotation {q['no']}", f"For {q['customer']}, {_day(q['date']).strftime('%m/%d/%Y')}"]
    for ln in q["lines"]:
        lines.append(f"• {ln['description']}: {ln['qty']:g} x ₱{ln['price']:,.2f} = ₱{ln['amount']:,.2f}")
    lines.append(f"Total ₱{q['total']:,.2f}. Good until {_day(q['good_until']).strftime('%m/%d/%Y')}, while stock lasts.")
    from engines.rim_stock import quote_footer
    if quote_footer():
        lines.append(quote_footer())
    return "\n".join(lines)


# --- follow-ups and contacts --------------------------------------------------------------------------------------------------------------------------

def note_contact(customer_id: int, note: str, next_on=None, qt_id: int | None = None) -> tuple[bool, str]:
    allowed, why = require_permission("sales.use")
    if not allowed:
        return False, why
    note = _text(note)
    if not note:
        return False, "Write what was said."
    nxt = _day(next_on) if next_on else None
    with write_tx() as conn:
        name = conn.execute("SELECT customer_name FROM customers WHERE customer_id = ?", (int(customer_id),)).fetchone()
        if not name:
            return False, "Customer not found."
        conn.execute("INSERT INTO customer_contacts (customer_id, qt_id, contacted_on, note, next_on, created_by) VALUES (?, ?, ?, ?, ?, acting_user())",
                     (int(customer_id), int(qt_id) if qt_id else None, str(manila_today()), note, str(nxt) if nxt else None))
    return True, f"{name[0]}: contact noted" + (f", call again {nxt.strftime('%m/%d/%Y')}." if nxt else ".")


def contacts(customer_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""SELECT c.contacted_on, c.note, c.next_on, c.created_by, q.qt_no FROM customer_contacts c LEFT JOIN quotations q ON q.qt_id = c.qt_id
                               WHERE c.customer_id = ? ORDER BY c.contact_id DESC LIMIT 50""", (int(customer_id),)).fetchall()
    return [{"on": a, "note": b, "next_on": c, "by": d, "qt": e} for a, b, c, d, e in rows]


def follow_ups() -> list[dict]:
    """Who to call: quotes waiting for an answer (oldest first), quotes lapsed in the last 14 days and not sold, and the calls someone said to make
    again whose day has come. Each: {'why', 'customer', 'customer_id', 'qt_id', 'qt_no', 'total', 'when'}."""
    today = manila_today()
    out = []
    for q in quotes("all"):
        if q["state"] == "WAITING":
            out.append({"why": f"quote waiting {q['days']} day{'s' if q['days'] != 1 else ''}" + (f", {q['left']} left" if q["left"] <= 2 else ""), "kind": "waiting", **_fu(q)})
        elif q["state"] == "LAPSED" and (today - _day(q["good_until"])).days <= 14:
            out.append({"why": f"quote lapsed {(today - _day(q['good_until'])).days} day(s) ago", "kind": "lapsed", **_fu(q)})
    with get_conn() as conn:
        due = conn.execute("""SELECT c.customer_id, k.customer_name, c.note, c.next_on FROM customer_contacts c JOIN customers k ON k.customer_id = c.customer_id
                              WHERE c.next_on IS NOT NULL AND c.next_on <= ? AND NOT EXISTS (SELECT 1 FROM customer_contacts x WHERE x.customer_id = c.customer_id
                              AND x.contact_id > c.contact_id) ORDER BY c.next_on""", (str(today),)).fetchall()
    for cid, name, note, nxt in due:
        out.append({"why": f"call again (said on a call: {note})", "kind": "call", "customer": name, "customer_id": cid, "qt_id": None, "qt_no": "", "total": None, "when": nxt})
    return out


def _fu(q: dict) -> dict:
    return {"customer": q["customer"], "customer_id": q["customer_id"], "qt_id": q["id"], "qt_no": q["no"], "total": q["total"], "when": q["date"]}


def customer_page(customer_id: int) -> dict | None:
    """One customer: who they are, what they owe, their quotes, their last sales, their agreed prices, the contact notes."""
    from engines import sales_rules as sr
    c = sr.customer(int(customer_id))
    if not c:
        return None
    with get_conn() as conn:
        sales = conn.execute("""SELECT sale_id, si_no, sale_date, total, status FROM sales WHERE customer_id = ? AND status IN ('SAVED', 'RELEASED')
                                ORDER BY sale_date DESC, sale_id DESC LIMIT 20""", (int(customer_id),)).fetchall()
    try:
        from engines.collect_rules import balance
        owed = balance(int(customer_id))
    except Exception:                                  # noqa: BLE001 -- stage B off: no receivables shown
        owed = None
    return {"customer": c, "owed": owed, "quotes": quotes("all", int(customer_id)), "agreed": agreed_list(int(customer_id)), "contacts": contacts(int(customer_id)),
            "sales": [{"id": a, "si": b, "date": d, "total": float(t or 0), "status": s} for a, b, d, t, s in sales]}
