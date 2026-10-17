"""Affiliates: lending tires to and borrowing them from the relatives' businesses (the revenue cycle, stage A; owner, 2026-10-11 -- this is where stock
"gets lost" today). A lend (AL-0001) takes stock out without a sale; a borrow (AB-0001) brings stock in without a purchase. Each stays on the affiliate's
running balance, in pieces and pesos, until it is settled:

  * RETURN -- the tires come back (a lend) or we give theirs back (a borrow): the stock moves the other way;
  * PAY    -- a lend they pay for becomes a sale to them AT OUR COST (owner: "at cost"; a sales invoice number is asked, the stock already left);
              a borrow we pay for is closed with how it was paid;
  * OFFSET -- tires they hold of ours are set against tires we hold of theirs.

A lend or a borrow open longer than Settings > "Flag tires lent or borrowed and still open after" (30 days) goes on the Review list. Nothing is blocked."""
from __future__ import annotations

from datetime import date

from engines.clock import manila_today
from engines.common import get_conn, write_tx
from engines.db_engine import next_doc_number
from engines.permissions import require as require_permission

OPEN_DAYS_KEY = "affiliate_open_days"
HOW = {"RETURN": "Returned", "PAY": "Paid", "OFFSET": "Set off"}


def _text(v) -> str:
    return " ".join(str(v or "").split())


def open_days() -> int:
    from engines.settings_registry import int_value
    return int_value(OPEN_DAYS_KEY)


def record_move(customer_id: int, direction: str, lines: list[dict], move_date=None, taken_by: str = "", note: str = "") -> tuple[bool, str, int | None]:
    """A lend (stock out at our cost) or a borrow (stock in at the cost typed, or our average when none is). lines: [{'item_id', 'qty', 'price'}]."""
    allowed, why = require_permission("inventory.use")
    if not allowed:
        return False, why, None
    direction = str(direction).upper()
    if direction not in ("LEND", "BORROW"):
        return False, "Choose Lend or Borrow.", None
    try:
        day = move_date if isinstance(move_date, date) else (date.fromisoformat(str(move_date)[:10]) if move_date else manila_today())
    except ValueError:
        return False, "Pick the date.", None
    if day > manila_today():
        return False, "The date cannot be after today.", None
    from engines.stock_ledger import stock_in, stock_out
    with write_tx() as conn:
        who = conn.execute("SELECT customer_name, is_affiliate FROM customers WHERE customer_id = ? AND is_active = 1", (int(customer_id),)).fetchone()
        if not who or not who[1]:
            return False, "Choose an affiliate.", None
        clean = []
        for ln in lines or []:
            try:
                item_id, qty = int(ln.get("item_id") or 0), float(str(ln.get("qty") or 0).replace(",", ""))
            except (TypeError, ValueError):
                return False, "A quantity is not a number.", None
            it = conn.execute("SELECT name, COALESCE(NULLIF(next_cost, 0), avg_cost) FROM items WHERE id = ?", (item_id,)).fetchone()
            if not it or qty <= 0:
                return False, "Choose the items and give each a quantity above zero.", None
            cost = float(it[1] or 0)
            if direction == "BORROW":
                raw = str(ln.get("price") if ln.get("price") is not None else "").replace(",", "").strip()
                if raw:
                    try:
                        cost = float(raw)
                    except ValueError:
                        return False, f"{it[0]}: '{raw}' is not a cost.", None
                if cost <= 0:
                    return False, f"{it[0]}: type what the tires cost them (we have no cost for this item yet).", None
            clean.append((item_id, it[0], qty, cost))
        if not clean:
            return False, "Scan or choose the items.", None
        series = "AL" if direction == "LEND" else "AB"
        used = [int(r[0][3:]) for r in conn.execute("SELECT move_no FROM affiliate_moves WHERE move_no LIKE ?", (series + "-%",)) if r[0][3:].isdigit()]
        number = f"{series}-{next_doc_number(conn, series, 0, max(used, default=0)):04d}"
        move_id = conn.execute("INSERT INTO affiliate_moves (move_no, customer_id, direction, move_date, taken_by, note, created_by) VALUES (?, ?, ?, ?, ?, ?, acting_user()) RETURNING move_id",
                               (number, int(customer_id), direction, str(day), _text(taken_by) or None, _text(note) or None)).fetchone()[0]
        for item_id, name, qty, cost in clean:
            if direction == "LEND":
                cost = stock_out(conn, item_id, qty, "affiliate_moves", move_id, day, note=f"lent to {who[0]} on {number}")
            else:
                stock_in(conn, item_id, qty, cost, "affiliate_moves", move_id, day, note=f"borrowed from {who[0]} on {number}")
            conn.execute("INSERT INTO affiliate_move_lines (move_id, item_id, qty, unit_cost) VALUES (?, ?, ?, ?)", (move_id, item_id, qty, cost))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), ?, 'affiliate_moves', ?, ?)",
                     ("AFFILIATE_LEND" if direction == "LEND" else "AFFILIATE_BORROW", move_id,
                      f"{number} · {who[0]}: " + ", ".join(f"{q:g} x {n}" for _, n, q, _ in clean) + (f"; taken by {_text(taken_by)}" if _text(taken_by) else "")))
    verb = "lent to" if direction == "LEND" else "borrowed from"
    return True, f"{number}: {sum(q for _, _, q, _ in clean):g} pc(s) {verb} {who[0]}.", int(move_id)


def open_lines(customer_id: int) -> list[dict]:
    """What is still open with an affiliate, oldest first: line_id, move_no, direction, date, item_id, item, open (pieces), unit_cost, value, days."""
    today = manila_today()
    with get_conn() as conn:
        rows = conn.execute("""SELECT l.line_id, m.move_no, m.direction, m.move_date, l.item_id, i.name, l.qty - l.qty_settled, l.unit_cost
                               FROM affiliate_move_lines l JOIN affiliate_moves m ON m.move_id = l.move_id JOIN items i ON i.id = l.item_id
                               WHERE m.customer_id = ? AND l.qty - l.qty_settled > 0.0000001 ORDER BY m.move_date, l.line_id""", (int(customer_id),)).fetchall()
    return [{"line_id": int(a), "move_no": b, "direction": c, "date": d, "item_id": int(e), "item": f, "open": float(g), "unit_cost": float(h), "value": round(float(g) * float(h), 2),
             "days": (today - date.fromisoformat(str(d)[:10])).days} for a, b, c, d, e, f, g, h in rows]


def balances() -> list[dict]:
    """Every affiliate with what is open: customer_id, name, lent_pcs, lent_value (they hold of ours), borrowed_pcs, borrowed_value (we hold of theirs),
    net (positive = they owe us), oldest_days, long_open."""
    from engines.sales_rules import customers
    limit, out = open_days(), []
    for c in customers(affiliates=True):
        lines = open_lines(c["customer_id"])
        lent, borrowed = [x for x in lines if x["direction"] == "LEND"], [x for x in lines if x["direction"] == "BORROW"]
        oldest = max((x["days"] for x in lines), default=None)
        out.append({"customer_id": c["customer_id"], "name": c["name"], "contact": c["contact_number"], "lent_pcs": sum(x["open"] for x in lent), "lent_value": round(sum(x["value"] for x in lent), 2),
                    "borrowed_pcs": sum(x["open"] for x in borrowed), "borrowed_value": round(sum(x["value"] for x in borrowed), 2),
                    "net": round(sum(x["value"] for x in lent) - sum(x["value"] for x in borrowed), 2), "oldest_days": oldest, "long_open": oldest is not None and oldest > limit})
    return out


def settle(customer_id: int, picks: list[dict], how: str, ref: str = "", si_no=None, payments: list[dict] | None = None) -> tuple[bool, str]:
    """Settles open lines. picks: [{'line_id', 'qty'}]; how: RETURN, PAY or OFFSET. PAY of lent tires needs the sales invoice number and how they paid
    (it becomes a sale to them at our cost); PAY of borrowed tires needs `ref` (how we paid them). OFFSET needs both sides in the picks."""
    allowed, why = require_permission("inventory.use")
    if not allowed:
        return False, why
    how = str(how).upper()
    if how not in HOW:
        return False, "Choose how it is settled: returned, paid or set off."
    opened = {x["line_id"]: x for x in open_lines(customer_id)}
    chosen = []
    for p in picks or []:
        ln = opened.get(int(p.get("line_id") or 0))
        if not ln:
            continue
        try:
            qty = float(str(p.get("qty") or ln["open"]).replace(",", ""))
        except (TypeError, ValueError):
            return False, f"{ln['item']}: the quantity is not a number."
        if qty <= 0 or qty > ln["open"] + 1e-9:
            return False, f"{ln['item']} on {ln['move_no']}: {ln['open']:g} is open, not {qty:g}."
        chosen.append((ln, qty))
    if not chosen:
        return False, "Tick what is being settled."
    lends, borrows = [c for c in chosen if c[0]["direction"] == "LEND"], [c for c in chosen if c[0]["direction"] == "BORROW"]
    today, ref = manila_today(), _text(ref)
    if how == "OFFSET" and not (lends and borrows):
        return False, "To set off, tick tires they hold of ours AND tires we hold of theirs."
    if how == "PAY" and lends and borrows:
        return False, "Settle what they pay and what we pay separately, or set them off."
    if how == "PAY" and borrows and not ref:
        return False, "Say how we paid them (e.g. 'cash 10/11', 'check 0048812')."
    if how == "PAY" and lends:                                    # a sale to them at our cost: the stock already left with the lend
        from engines.sales_rules import save_sale
        ok, msg, sale_id = save_sale(si_no, today, int(customer_id), "", [{"item_id": ln["item_id"], "qty": qty, "price": ln["unit_cost"]} for ln, qty in lends], [],
                                     payments or [], no_stock_out=True)
        if not ok:
            return False, msg
        ref = f"SI {int(str(si_no).replace(',', '')):06d}"
    from engines.stock_ledger import stock_out, stock_return
    with write_tx() as conn:
        name = conn.execute("SELECT customer_name FROM customers WHERE customer_id = ?", (int(customer_id),)).fetchone()[0]
        said = []
        for ln, qty in chosen:
            move_id = conn.execute("SELECT move_id FROM affiliate_move_lines WHERE line_id = ?", (ln["line_id"],)).fetchone()[0]
            if how == "RETURN":
                if ln["direction"] == "LEND":
                    stock_return(conn, ln["item_id"], qty, ln["unit_cost"], "affiliate_moves", move_id, today, note=f"{name} returned what was lent on {ln['move_no']}")
                else:
                    stock_out(conn, ln["item_id"], qty, "affiliate_moves", move_id, today, note=f"returned to {name} what was borrowed on {ln['move_no']}")
            conn.execute("UPDATE affiliate_move_lines SET qty_settled = qty_settled + ? WHERE line_id = ?", (qty, ln["line_id"]))
            conn.execute("INSERT INTO affiliate_settlements (line_id, how, qty, settled_on, ref, created_by) VALUES (?, ?, ?, ?, ?, acting_user())",
                         (ln["line_id"], how, qty, str(today), ref or None))
            said.append(f"{qty:g} x {ln['item']} ({ln['move_no']})")
        lent_value, borrowed_value = sum(q * ln["unit_cost"] for ln, q in lends), sum(q * ln["unit_cost"] for ln, q in borrows)
        extra = ""
        if how == "OFFSET":
            diff = round(lent_value - borrowed_value, 2)
            extra = (f" Ours ₱{lent_value:,.2f} against theirs ₱{borrowed_value:,.2f}: " + ("even." if abs(diff) < 0.005 else
                     (f"they still owe ₱{diff:,.2f}." if diff > 0 else f"we still owe ₱{-diff:,.2f}.")))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'AFFILIATE_SETTLED', 'customers', ?, ?)",
                     (int(customer_id), f"{name}: {HOW[how].lower()} " + ", ".join(said) + (f" ({ref})" if ref else "") + extra))
    return True, f"{HOW[how]}: " + ", ".join(said) + "." + extra


def history(customer_id: int, limit: int = 60) -> list[dict]:
    """The affiliate's statement, newest first: date, what (Lent / Borrowed / Returned / Paid / Set off), ref, item, qty, value."""
    with get_conn() as conn:
        moves = conn.execute("""SELECT m.move_date, m.direction, m.move_no, i.name, l.qty, l.unit_cost, m.move_id FROM affiliate_move_lines l JOIN affiliate_moves m ON m.move_id = l.move_id
                                JOIN items i ON i.id = l.item_id WHERE m.customer_id = ?""", (int(customer_id),)).fetchall()
        setts = conn.execute("""SELECT s.settled_on, s.how, m.move_no, i.name, s.qty, l.unit_cost, s.ref, m.direction FROM affiliate_settlements s
                                JOIN affiliate_move_lines l ON l.line_id = s.line_id JOIN affiliate_moves m ON m.move_id = l.move_id JOIN items i ON i.id = l.item_id
                                WHERE m.customer_id = ?""", (int(customer_id),)).fetchall()
    out = [{"date": d, "what": "Lent" if k == "LEND" else "Borrowed", "ref": no, "item": name, "qty": float(q), "value": round(float(q) * float(c), 2), "sort": (str(d), 0)}
           for d, k, no, name, q, c, _ in moves]
    out += [{"date": d, "what": HOW[h] + (" (ours)" if k == "LEND" else " (theirs)"), "ref": no + (f" · {r}" if r else ""), "item": name, "qty": float(q),
             "value": round(float(q) * float(c), 2), "sort": (str(d), 1)} for d, h, no, name, q, c, r, k in setts]
    return sorted(out, key=lambda x: x["sort"], reverse=True)[:limit]


def flag_long_open() -> int:
    """Lends and borrows open longer than the Settings number of days go on the Review list (once each)."""
    from engines.review_flags import flag
    limit, n = open_days(), 0
    for b in balances():
        for ln in open_lines(b["customer_id"]):
            if ln["days"] > limit:
                with write_tx() as conn:
                    flag(conn, "AFFILIATE_LONG_OPEN", "customers", b["customer_id"],
                         f"{b['name']}: {ln['open']:g} x {ln['item']} {'lent' if ln['direction'] == 'LEND' else 'borrowed'} on {ln['move_no']} ({str(ln['date'])[5:7]}/{str(ln['date'])[8:10]}) is still open.")
                n += 1
    return n
