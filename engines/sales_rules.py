"""Selling at the counter (the revenue cycle, stage A; owner, 2026-10-11). A sale is ONE document: saving it takes the stock and records how it was paid
in the same locked step -- the mirror of a DR on the buying side. The paper sales invoice (the BIR booklet) stays the invoice; the system records it.

  * customers: save_customer / set_credit_allowed / customers / customer
  * the booklets: register_booklet / use_booklet / cancel_leaf / booklets / next_si_no / skipped
  * the sale: save_sale / release (goods out before the invoice) / invoice_release / cancel_release / void_sale / return_item
  * the day: day_sheet / get_sale / open_releases / flag_open_releases

Controls flag, they do not block (the owner's rule): below cost, above stock, a charge to a customer without "credit allowed", a number skipped or outside
the booklets, a sale typed after its date, a void -- all are saved and put on the Review list. What IS refused protects the record itself: a sales invoice
number used twice, a payment that does not add up to the total, a check without its details, a charge with no named customer.
Decided 2026-10-11: the seller sees cost and profit; a post-dated check makes the sale paid (it waits to be deposited, stage B); the seller voids and the
System admin reviews afterwards; one booklet in use at a time."""
from __future__ import annotations

from datetime import date

from engines.clock import manila_today
from engines.common import get_conn, write_tx
from engines.db_engine import next_doc_number
from engines.permissions import require as require_permission

METHODS = {"CASH": "Cash", "CHECK": "Check", "TRANSFER": "Transfer / GCash / Maya", "CARD": "Card", "CHARGE": "Charge (on credit)"}
CUSTOMER_TYPES = {"RETAIL": "Retail", "FLEET": "Fleet", "DEALER": "Dealer", "OTHER": "Other"}
EPS = 0.005


def _text(v) -> str:
    return " ".join(str(v or "").split())


def _as_date(v) -> date | None:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None


def si_text(si_no) -> str:
    return f"SI {int(si_no):06d}" if si_no else ""


# --- customers ----------------------------------------------------------------------------------------------------------------------------------------

def save_customer(customer_id: int | None, name: str, customer_type: str = "RETAIL", terms: str = "", contact_person: str = "", contact_number: str = "",
                  is_affiliate: bool = False) -> tuple[bool, str, int | None]:
    """A named customer (a walk-in needs none). An affiliate is a relative's business we lend tires to and borrow from."""
    allowed = require_permission("sales.use")[0] or require_permission("inventory.use")[0]
    if not allowed:
        return False, "Customers are switched off for you.", None
    name = _text(name)
    if not name:
        return False, "Type the customer's name.", None
    if customer_type not in CUSTOMER_TYPES:
        return False, "Pick the kind of customer.", None
    with write_tx() as conn:
        clash = conn.execute("SELECT customer_id FROM customers WHERE LOWER(customer_name) = LOWER(?)", (name,)).fetchone()
        if clash and (customer_id is None or int(clash[0]) != int(customer_id)):
            return False, f"{name} is already a customer.", None
        if customer_id is None:
            customer_id = conn.execute("""INSERT INTO customers (customer_name, customer_type, terms, contact_person, contact_number, is_affiliate, created_by)
                                          VALUES (?, ?, ?, ?, ?, ?, acting_user()) RETURNING customer_id""",
                                       (name, customer_type, _text(terms) or None, _text(contact_person) or None, _text(contact_number) or None, 1 if is_affiliate else 0)).fetchone()[0]
            action = "CUSTOMER_ADDED"
        else:
            if not conn.execute("SELECT 1 FROM customers WHERE customer_id = ?", (int(customer_id),)).fetchone():
                return False, "Customer not found.", None
            conn.execute("UPDATE customers SET customer_name = ?, customer_type = ?, terms = ?, contact_person = ?, contact_number = ?, is_affiliate = ? WHERE customer_id = ?",
                         (name, customer_type, _text(terms) or None, _text(contact_person) or None, _text(contact_number) or None, 1 if is_affiliate else 0, int(customer_id)))
            action = "CUSTOMER_CHANGED"
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), ?, 'customers', ?, ?)",
                     (action, int(customer_id), f"{name} ({CUSTOMER_TYPES[customer_type]}{', affiliate' if is_affiliate else ''})"))
    return True, f"{name} saved.", int(customer_id)


def set_credit_allowed(customer_id: int, on: bool) -> tuple[bool, str]:
    """May this customer buy on charge? The System admin's judgement alone (no peso limit: alerts instead). Logged."""
    allowed, why = require_permission("inventory.approve")
    if not allowed:
        return False, why
    with write_tx() as conn:
        row = conn.execute("SELECT customer_name FROM customers WHERE customer_id = ?", (int(customer_id),)).fetchone()
        if not row:
            return False, "Customer not found."
        conn.execute("UPDATE customers SET credit_allowed = ? WHERE customer_id = ?", (1 if on else 0, int(customer_id)))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CUSTOMER_CREDIT', 'customers', ?, ?)",
                     (int(customer_id), f"{row[0]}: credit {'allowed' if on else 'not allowed'}"))
    return True, f"{row[0]}: buying on charge is {'allowed' if on else 'not allowed'}."


def customers(search: str = "", affiliates: bool | None = None) -> list[dict]:
    """Active customers: customer_id, name, type, type_label, terms, contact_person, contact_number, is_affiliate, credit_allowed, sold (to date), owed (charge sales)."""
    words = _text(search).lower().split()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT c.customer_id, c.customer_name, c.customer_type, c.terms, c.contact_person, c.contact_number, c.is_affiliate, c.credit_allowed,
                      (SELECT COALESCE(SUM(s.total), 0) FROM sales s WHERE s.customer_id = c.customer_id AND s.status = 'SAVED'),
                      (SELECT COALESCE(SUM(p.amount), 0) FROM sale_payments p JOIN sales s ON s.sale_id = p.sale_id WHERE s.customer_id = c.customer_id AND s.status = 'SAVED' AND p.method = 'CHARGE'),
                      (SELECT MAX(s.sale_date) FROM sales s WHERE s.customer_id = c.customer_id AND s.status = 'SAVED')
               FROM customers c WHERE c.is_active = 1 ORDER BY c.customer_name COLLATE NOCASE""").fetchall()
    out = []
    for r in rows:
        if any(w not in str(r[1]).lower() for w in words) or (affiliates is not None and bool(r[6]) != affiliates):
            continue
        out.append({"customer_id": int(r[0]), "name": r[1], "type": r[2], "type_label": CUSTOMER_TYPES.get(r[2], r[2]), "terms": r[3] or "", "contact_person": r[4] or "",
                    "contact_number": r[5] or "", "is_affiliate": bool(r[6]), "credit_allowed": bool(r[7]), "sold": float(r[8] or 0), "charged": float(r[9] or 0), "last_sale": r[10]})
    return out


def customer(customer_id: int) -> dict | None:
    return next((c for c in customers() if c["customer_id"] == int(customer_id)), None)


# --- sales invoice booklets ---------------------------------------------------------------------------------------------------------------------------

def _used(conn, start: int, end: int) -> tuple[set[int], set[int]]:
    used = {int(r[0]) for r in conn.execute("SELECT si_no FROM sales WHERE si_no BETWEEN ? AND ?", (start, end))}
    spoiled = {int(r[0]) for r in conn.execute("SELECT si_no FROM si_cancelled WHERE si_no BETWEEN ? AND ?", (start, end))}
    return used, spoiled


def register_booklet(start, end, holder: str = "") -> tuple[bool, str]:
    """A new booklet's numbers. One booklet is in use at a time (owner, 2026-10-11): this one waits if another is in use."""
    allowed, why = require_permission("sales.use")
    if not allowed:
        return False, why
    try:
        start, end = int(str(start).replace(",", "")), int(str(end).replace(",", ""))
    except (TypeError, ValueError):
        return False, "Type the booklet's first and last number."
    if start <= 0 or end < start or end - start > 499:
        return False, "The last number must follow the first, and a booklet has at most 500 leaves."
    with write_tx() as conn:
        clash = conn.execute("SELECT code FROM si_booklets WHERE start_no <= ? AND end_no >= ?", (end, start)).fetchone()
        if clash:
            return False, f"Those numbers overlap booklet {clash[0]}."
        used = [int(r[0][3:]) for r in conn.execute("SELECT code FROM si_booklets WHERE code LIKE 'SB-%'") if r[0][3:].isdigit()]
        code = f"SB-{next_doc_number(conn, 'SB', 0, max(used, default=0)):04d}"
        busy = conn.execute("SELECT 1 FROM si_booklets WHERE status = 'IN_USE'").fetchone()
        conn.execute("INSERT INTO si_booklets (code, start_no, end_no, holder, status, registered_by) VALUES (?, ?, ?, ?, ?, acting_user())",
                     (code, start, end, _text(holder) or None, "WAITING" if busy else "IN_USE"))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'SI_BOOKLET_REGISTERED', 'si_booklets', NULL, ?)",
                     (f"{code}: {start:06d}–{end:06d}" + (f", held by {_text(holder)}" if _text(holder) else ""),))
    return True, f"{code} registered: {start:06d} to {end:06d}." + (" It waits until the booklet in use is finished." if busy else " It is the booklet in use.")


def use_booklet(booklet_id: int) -> tuple[bool, str]:
    """Makes a waiting booklet the one in use; the one before it is finished (or waits, if it still has unused leaves)."""
    allowed, why = require_permission("sales.use")
    if not allowed:
        return False, why
    with write_tx() as conn:
        row = conn.execute("SELECT code, status FROM si_booklets WHERE booklet_id = ?", (int(booklet_id),)).fetchone()
        if not row:
            return False, "Booklet not found."
        for bid, s, e in conn.execute("SELECT booklet_id, start_no, end_no FROM si_booklets WHERE status = 'IN_USE' AND booklet_id != ?", (int(booklet_id),)).fetchall():
            used, spoiled = _used(conn, s, e)
            conn.execute("UPDATE si_booklets SET status = ? WHERE booklet_id = ?", ("FINISHED" if len(used | spoiled) >= e - s + 1 else "WAITING", bid))
        conn.execute("UPDATE si_booklets SET status = 'IN_USE' WHERE booklet_id = ?", (int(booklet_id),))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'SI_BOOKLET_IN_USE', 'si_booklets', ?, ?)",
                     (int(booklet_id), row[0]))
    return True, f"{row[0]} is now the booklet in use."


def booklets() -> list[dict]:
    """Every booklet, newest first: booklet_id, code, start_no, end_no, holder, status, used, cancelled, unused, next (the lowest unused number), skipped."""
    with get_conn() as conn:
        rows = conn.execute("SELECT booklet_id, code, start_no, end_no, holder, status FROM si_booklets ORDER BY start_no DESC").fetchall()
        out = []
        for bid, code, s, e, holder, status in rows:
            used, spoiled = _used(conn, s, e)
            free = [n for n in range(s, e + 1) if n not in used and n not in spoiled]
            top = max(used | spoiled, default=s - 1)
            out.append({"booklet_id": int(bid), "code": code, "start_no": s, "end_no": e, "holder": holder or "", "status": status,
                        "status_label": {"IN_USE": "In use", "WAITING": "Waiting", "FINISHED": "Finished"}[status], "used": len(used), "cancelled": len(spoiled),
                        "unused": len(free), "next": free[0] if free else None, "skipped": [n for n in free if n < top]})
    return out


def next_si_no() -> int | None:
    """The number to offer on the next sale: the lowest unused one of the booklet in use (a skipped number comes first, so it is not forgotten)."""
    b = next((x for x in booklets() if x["status"] == "IN_USE"), None)
    return b["next"] if b else None


def skipped() -> list[int]:
    return sorted(n for b in booklets() for n in b["skipped"])


def cancel_leaf(si_no, reason: str) -> tuple[bool, str]:
    """A spoiled leaf: the number is accounted for, with why. The paper copies are kept, as BIR requires."""
    allowed, why = require_permission("sales.use")
    if not allowed:
        return False, why
    reason = _text(reason)
    try:
        n = int(str(si_no).replace(",", ""))
    except (TypeError, ValueError):
        return False, "Type the number of the spoiled leaf."
    if not reason:
        return False, "Say why the leaf was spoiled."
    with write_tx() as conn:
        if not conn.execute("SELECT 1 FROM si_booklets WHERE start_no <= ? AND end_no >= ?", (n, n)).fetchone():
            return False, f"{n:06d} is not in a registered booklet."
        if conn.execute("SELECT 1 FROM sales WHERE si_no = ?", (n,)).fetchone():
            return False, f"{si_text(n)} is already a sale. Void that sale instead."
        if conn.execute("SELECT 1 FROM si_cancelled WHERE si_no = ?", (n,)).fetchone():
            return False, f"{n:06d} is already cancelled."
        conn.execute("INSERT INTO si_cancelled (si_no, reason, cancelled_by) VALUES (?, ?, acting_user())", (n, reason))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'SI_LEAF_CANCELLED', 'si_booklets', NULL, ?)",
                     (f"{n:06d}: {reason}",))
    return True, f"{n:06d} is cancelled: {reason}."


def _flag_numbers(conn, si_no: int) -> None:
    """After a sale is saved: a number outside every booklet, and numbers below it that were skipped, go on the Review list."""
    from engines.review_flags import flag
    book = conn.execute("SELECT code, start_no FROM si_booklets WHERE start_no <= ? AND end_no >= ?", (si_no, si_no)).fetchone()
    if not book:
        flag(conn, "SI_SKIPPED", "sales", None, f"{si_text(si_no)} is not in any registered booklet. Register the booklet, or check the number.")
        return
    used = {int(r[0]) for r in conn.execute("SELECT si_no FROM sales WHERE si_no BETWEEN ? AND ?", (book[1], si_no))} | \
           {int(r[0]) for r in conn.execute("SELECT si_no FROM si_cancelled WHERE si_no BETWEEN ? AND ?", (book[1], si_no))}
    for n in range(book[1], si_no):
        if n not in used:
            flag(conn, "SI_SKIPPED", "sales", None, f"{si_text(n)} ({book[0]}) was skipped: {si_text(si_no)} is typed and it is not. Type it, or cancel the leaf with a reason.")


# --- the sale -----------------------------------------------------------------------------------------------------------------------------------------

def _clean_payments(payments: list[dict], total: float, named: bool) -> tuple[list[dict] | None, str]:
    out = []
    for p in payments or []:
        method = str(p.get("method") or "").upper()
        try:
            amount = round(float(str(p.get("amount") or 0).replace(",", "")), 2)
        except (TypeError, ValueError):
            return None, "A payment amount is not a number."
        if amount <= 0:
            continue
        if method not in METHODS:
            return None, "Choose how each amount was paid."
        row = {"method": method, "amount": amount, "bank": _text(p.get("bank")) or None, "check_no": _text(p.get("check_no")) or None,
               "check_date": _as_date(p.get("check_date")), "reference": _text(p.get("reference")) or None}
        if method == "CHECK" and not (row["bank"] and row["check_no"] and row["check_date"]):
            return None, "A check needs its bank, its number and its date."
        if method in ("TRANSFER", "CARD") and not row["reference"]:
            return None, f"{METHODS[method]}: type the reference or approval number."
        if method == "CHARGE" and not named:
            return None, "A charge sale needs a named customer: choose or add the customer first."
        out.append(row)
    paid = round(sum(r["amount"] for r in out), 2)
    if not out:
        return None, "Say how it was paid."
    if abs(paid - round(total, 2)) > EPS:
        return None, f"The payments add up to ₱{paid:,.2f}; the sale is ₱{total:,.2f}. Put the rest on another way, or on Charge."
    return out, ""


def _after_save(conn, sale_id: int, si_no: int, sale_date: date, customer_id, below: list[str], reason: str, charged: float) -> None:
    from engines.review_flags import flag
    who = conn.execute("SELECT COALESCE(c.customer_name, NULLIF(s.customer_name, ''), 'Walk-in'), COALESCE(c.credit_allowed, 0) FROM sales s LEFT JOIN customers c ON c.customer_id = s.customer_id WHERE s.sale_id = ?",
                       (sale_id,)).fetchone()
    if below:
        flag(conn, "SALE_BELOW_COST", "sales", sale_id, f"{si_text(si_no)} · {who[0]}: " + "; ".join(below) + (f". Why: {reason}" if reason else ". No reason given."))
    if charged > EPS and not who[1]:
        flag(conn, "SALE_NO_CREDIT", "sales", sale_id, f"{si_text(si_no)}: ₱{charged:,.2f} charged to {who[0]}, who is not marked \"credit allowed\".")
    if sale_date < manila_today():
        flag(conn, "SALE_TYPED_LATE", "sales", sale_id, f"{si_text(si_no)} dated {sale_date:%m/%d/%Y} was typed on {manila_today():%m/%d/%Y}.")
    _flag_numbers(conn, si_no)


def _check_si(conn, si_no, sale_id: int | None = None) -> tuple[int | None, str]:
    try:
        n = int(str(si_no or "").replace(",", "").strip())
    except ValueError:
        return None, "Type the sales invoice number from the booklet."
    if n <= 0:
        return None, "Type the sales invoice number from the booklet."
    row = conn.execute("SELECT sale_id FROM sales WHERE si_no = ?", (n,)).fetchone()
    if row and (sale_id is None or int(row[0]) != int(sale_id)):
        return None, f"{si_text(n)} is already used. Each number is used once: check the booklet."
    if conn.execute("SELECT 1 FROM si_cancelled WHERE si_no = ?", (n,)).fetchone():
        return None, f"{n:06d} was cancelled as a spoiled leaf."
    return n, ""


def _clean_lines(conn, lines: list[dict], need_price: bool) -> tuple[list[dict] | None, str]:
    out = []
    for ln in lines or []:
        try:
            item_id, qty = int(ln.get("item_id") or 0), float(str(ln.get("qty") or 0).replace(",", ""))
        except (TypeError, ValueError):
            return None, "A quantity is not a number."
        it = conn.execute("SELECT name, avg_cost FROM items WHERE id = ?", (item_id,)).fetchone()
        if not it:
            return None, "An item was not found. Choose it again."
        if qty <= 0:
            return None, f"{it[0]}: the quantity must be more than zero."
        price = None
        if need_price:
            raw = str(ln.get("price") if ln.get("price") is not None else "").replace(",", "").strip()
            if raw == "":
                return None, f"{it[0]}: type the price agreed."
            try:
                price = round(float(raw), 2)
            except ValueError:
                return None, f"{it[0]}: '{raw}' is not a price."
            if price < 0:
                return None, f"{it[0]}: the price cannot be negative."
        out.append({"item_id": item_id, "name": it[0], "qty": qty, "price": price, "cost": float(it[1] or 0)})
    return out, ""


def _clean_services(services: list[dict]) -> tuple[list[dict] | None, str]:
    out = []
    for s in services or []:
        text = _text(s.get("description"))
        raw = str(s.get("amount") or "").replace(",", "").strip()
        if not text and not raw:
            continue
        try:
            amount = round(float(raw), 2)
        except ValueError:
            return None, f"Service '{text}': type its amount."
        if not text or amount <= 0:
            return None, "A service line needs what it was and an amount above zero."
        out.append({"description": text, "amount": amount})
    return out, ""


def save_sale(si_no, sale_date, customer_id: int | None, customer_name: str, lines: list[dict], services: list[dict], payments: list[dict],
              below_cost_reason: str = "", no_stock_out: bool = False) -> tuple[bool, str, int | None]:
    """The counter sale. lines: [{'item_id', 'qty', 'price'}]; services: [{'description', 'amount'}] (sold, never stocked); payments: [{'method',
    'amount', 'bank', 'check_no', 'check_date', 'reference'}], one or several ways, adding up to the total. Saving takes the stock (unless no_stock_out:
    an affiliate paying for tires already lent) and records the payment in ONE locked step. Returns (ok, message, sale_id)."""
    allowed, why = require_permission("sales.use")
    if not allowed:
        return False, why, None
    day = _as_date(sale_date)
    if day is None:
        return False, "Pick the date of the sale.", None
    if day > manila_today():
        return False, "The sale's date cannot be after today.", None
    from engines.stock_ledger import stock_out
    with write_tx() as conn:
        n, problem = _check_si(conn, si_no)
        if n is None:
            return False, problem, None
        if customer_id is not None and not conn.execute("SELECT 1 FROM customers WHERE customer_id = ? AND is_active = 1", (int(customer_id),)).fetchone():
            return False, "Customer not found.", None
        clean, problem = _clean_lines(conn, lines, True)
        if clean is None:
            return False, problem, None
        svc, problem = _clean_services(services)
        if svc is None:
            return False, problem, None
        if not clean and not svc:
            return False, "Scan or choose at least one item, or add a service.", None
        total = round(sum(round(ln["qty"] * ln["price"], 2) for ln in clean) + sum(s["amount"] for s in svc), 2)
        pays, problem = _clean_payments(payments, total, customer_id is not None)
        if pays is None:
            return False, problem, None
        sale_id = conn.execute(
            """INSERT INTO sales (si_no, sale_date, customer_id, customer_name, status, total, typed_late, below_cost_reason, no_stock_out, created_by)
               VALUES (?, ?, ?, ?, 'SAVED', ?, ?, ?, ?, acting_user()) RETURNING sale_id""",
            (n, str(day), customer_id, _text(customer_name) or None, total, 1 if day < manila_today() else 0, _text(below_cost_reason) or None, 1 if no_stock_out else 0)).fetchone()[0]
        cost_total, below = 0.0, []
        for ln in clean:
            cost = ln["cost"] if no_stock_out else stock_out(conn, ln["item_id"], ln["qty"], "sales", sale_id, day, note=f"sold on {si_text(n)}")
            cost_total += ln["qty"] * cost
            conn.execute("INSERT INTO sale_lines (sale_id, item_id, description, qty, unit_price, amount, unit_cost) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (sale_id, ln["item_id"], ln["name"], ln["qty"], ln["price"], round(ln["qty"] * ln["price"], 2), cost))
            if ln["price"] < cost - EPS and not no_stock_out:
                below.append(f"{ln['name']} sold at ₱{ln['price']:,.2f}, cost ₱{cost:,.2f} (₱{(cost - ln['price']) * ln['qty']:,.2f} below)")
        for s in svc:
            conn.execute("INSERT INTO sale_lines (sale_id, item_id, description, qty, unit_price, amount, unit_cost) VALUES (?, NULL, ?, 1, ?, ?, 0)",
                         (sale_id, s["description"], s["amount"], s["amount"]))
        for p in pays:
            conn.execute("INSERT INTO sale_payments (sale_id, method, amount, bank, check_no, check_date, reference) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (sale_id, p["method"], p["amount"], p["bank"], p["check_no"], str(p["check_date"]) if p["check_date"] else None, p["reference"]))
        conn.execute("UPDATE sales SET cost_total = ? WHERE sale_id = ?", (round(cost_total, 2), sale_id))
        if clean and not no_stock_out:
            # typed straight in, with no scan slip (owner, 2026-10-11: the scan tracks the movement): saved all the same, noted so a missed scan is seen
            from engines.review_flags import flag
            flag(conn, "SALE_NOT_SCANNED", "sales", int(sale_id),
                 f"{si_text(n)}: {sum(ln['qty'] for ln in clean):g} pc(s) typed without a scan slip. The usual way: Scan out when the goods leave, then write the invoice on the slip.")
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'SALE_SAVED', 'sales', ?, ?)",
                     (sale_id, f"{si_text(n)}: ₱{total:,.2f}, " + " + ".join(f"{METHODS[p['method']].split(' ')[0].lower()} {p['amount']:,.2f}" for p in pays)))
        _after_save(conn, sale_id, n, day, customer_id, below, _text(below_cost_reason), sum(p["amount"] for p in pays if p["method"] == "CHARGE"))
    return True, f"{si_text(n)} saved: ₱{total:,.2f}." + (f" {len(below)} line(s) below cost: noted for review." if below else ""), int(sale_id)


def release(customer_id: int | None, customer_name: str, lines: list[dict], note: str = "", si_hint=None) -> tuple[bool, str, int | None]:
    """Goods leave before the invoice is written (RL-0001): the stock goes out now; the invoice must follow the same day. This is what the Scan out page
    saves (owner, 2026-10-11: the barcode tracks the movement, the invoice is typed after the store closes). si_hint = the number already on the paper
    invoice, when known, offered back when the invoice is typed. A slip without a name is a walk-in; the invoice names the customer."""
    allowed, why = require_permission("sales.use")
    if not allowed:
        return False, why, None
    from engines.stock_ledger import stock_out
    with write_tx() as conn:
        clean, problem = _clean_lines(conn, lines, False)
        if clean is None:
            return False, problem, None
        if not clean:
            return False, "Scan or choose what is being released.", None
        used = [int(r[0][3:]) for r in conn.execute("SELECT release_no FROM sales WHERE release_no LIKE 'RL-%'") if r[0][3:].isdigit()]
        number = f"RL-{next_doc_number(conn, 'RL', 0, max(used, default=0)):04d}"
        today = manila_today()
        hint = "".join(ch for ch in str(si_hint or "") if ch.isdigit()) or None
        sale_id = conn.execute("""INSERT INTO sales (release_no, sale_date, customer_id, customer_name, status, released_at, si_hint, created_by)
                                  VALUES (?, ?, ?, ?, 'RELEASED', CURRENT_TIMESTAMP, ?, acting_user()) RETURNING sale_id""",
                               (number, str(today), customer_id, _text(customer_name) or None, hint)).fetchone()[0]
        for ln in clean:
            cost = stock_out(conn, ln["item_id"], ln["qty"], "sales", sale_id, today, note=f"released on {number}, invoice to follow")
            conn.execute("INSERT INTO sale_lines (sale_id, item_id, description, qty, unit_price, amount, unit_cost) VALUES (?, ?, ?, ?, 0, 0, ?)",
                         (sale_id, ln["item_id"], ln["name"], ln["qty"], cost))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'GOODS_RELEASED', 'sales', ?, ?)",
                     (sale_id, f"{number}: " + ", ".join(f"{ln['qty']:g} x {ln['name']}" for ln in clean) + (f" ({_text(note)})" if _text(note) else "")))
    return True, f"{number} recorded: the goods are out of stock. Write the invoice today.", int(sale_id)


def invoice_release(sale_id: int, si_no, sale_date, prices: dict, services: list[dict], payments: list[dict], below_cost_reason: str = "") -> tuple[bool, str]:
    """The invoice for goods released earlier: the SI number, the price of each line, how it was paid. The stock already left with the release."""
    allowed, why = require_permission("sales.use")
    if not allowed:
        return False, why
    day = _as_date(sale_date)
    if day is None or day > manila_today():
        return False, "Pick the invoice date (today or earlier)."
    with write_tx() as conn:
        head = conn.execute("SELECT status, release_no, customer_id FROM sales WHERE sale_id = ?", (int(sale_id),)).fetchone()
        if not head or head[0] != "RELEASED":
            return False, "This release is already invoiced or cancelled."
        n, problem = _check_si(conn, si_no, sale_id)
        if n is None:
            return False, problem
        svc, problem = _clean_services(services)
        if svc is None:
            return False, problem
        lines = conn.execute("SELECT line_id, description, qty, unit_cost FROM sale_lines WHERE sale_id = ? AND item_id IS NOT NULL", (int(sale_id),)).fetchall()
        total, cost_total, below, priced = 0.0, 0.0, [], []
        for line_id, name, qty, cost in lines:
            raw = str(prices.get(int(line_id), "")).replace(",", "").strip()
            try:
                price = round(float(raw), 2)
            except ValueError:
                return False, f"{name}: type the price agreed."
            if price < 0:
                return False, f"{name}: the price cannot be negative."
            priced.append((line_id, price, round(float(qty) * price, 2)))
            total += round(float(qty) * price, 2)
            cost_total += float(qty) * float(cost)
            if price < float(cost) - EPS:
                below.append(f"{name} sold at ₱{price:,.2f}, cost ₱{float(cost):,.2f} (₱{(float(cost) - price) * float(qty):,.2f} below)")
        total = round(total + sum(s["amount"] for s in svc), 2)
        pays, problem = _clean_payments(payments, total, head[2] is not None)
        if pays is None:
            return False, problem
        for line_id, price, amount in priced:
            conn.execute("UPDATE sale_lines SET unit_price = ?, amount = ? WHERE line_id = ?", (price, amount, line_id))
        for s in svc:
            conn.execute("INSERT INTO sale_lines (sale_id, item_id, description, qty, unit_price, amount, unit_cost) VALUES (?, NULL, ?, 1, ?, ?, 0)",
                         (int(sale_id), s["description"], s["amount"], s["amount"]))
        for p in pays:
            conn.execute("INSERT INTO sale_payments (sale_id, method, amount, bank, check_no, check_date, reference) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (int(sale_id), p["method"], p["amount"], p["bank"], p["check_no"], str(p["check_date"]) if p["check_date"] else None, p["reference"]))
        conn.execute("UPDATE sales SET si_no = ?, sale_date = ?, status = 'SAVED', total = ?, cost_total = ?, typed_late = ?, below_cost_reason = ? WHERE sale_id = ?",
                     (n, str(day), total, round(cost_total, 2), 1 if day < manila_today() else 0, _text(below_cost_reason) or None, int(sale_id)))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'SALE_SAVED', 'sales', ?, ?)",
                     (int(sale_id), f"{si_text(n)} for {head[1]}: ₱{total:,.2f}"))
        _after_save(conn, int(sale_id), n, day, head[2], below, _text(below_cost_reason), sum(p["amount"] for p in pays if p["method"] == "CHARGE"))
    return True, f"{si_text(n)} saved for {head[1]}: ₱{total:,.2f}."


def _put_back(conn, sale_id: int, what: str) -> list[str]:
    from engines.stock_ledger import stock_return
    said = []
    for item_id, name, qty, returned, cost in conn.execute("SELECT item_id, description, qty, qty_returned, unit_cost FROM sale_lines WHERE sale_id = ? AND item_id IS NOT NULL", (sale_id,)).fetchall():
        left = float(qty) - float(returned or 0)
        if left > 1e-9:
            stock_return(conn, item_id, left, float(cost), "sales", sale_id, note=what)
            said.append(f"{left:g} x {name}")
    return said


def cancel_release(sale_id: int, reason: str) -> tuple[bool, str]:
    """The goods came back before any invoice: the stock returns and the release is closed."""
    allowed, why = require_permission("sales.use")
    if not allowed:
        return False, why
    reason = _text(reason)
    if not reason:
        return False, "Say why the release is cancelled (e.g. 'customer did not take them')."
    with write_tx() as conn:
        head = conn.execute("SELECT status, release_no FROM sales WHERE sale_id = ?", (int(sale_id),)).fetchone()
        if not head or head[0] != "RELEASED":
            return False, "This release is already invoiced or cancelled."
        said = _put_back(conn, int(sale_id), f"{head[1]} cancelled: {reason}")
        conn.execute("UPDATE sales SET status = 'VOID', void_reason = ?, voided_by = acting_user(), voided_at = CURRENT_TIMESTAMP WHERE sale_id = ?", (reason, int(sale_id)))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'RELEASE_CANCELLED', 'sales', ?, ?)",
                     (int(sale_id), f"{head[1]}: {', '.join(said)} back in stock. Why: {reason}"))
    return True, f"{head[1]} cancelled: {', '.join(said)} back in stock."


def void_sale(sale_id: int, reason: str) -> tuple[bool, str]:
    """The seller voids a saved invoice, with a reason; the System admin reviews it afterwards (owner, 2026-10-11: flag, do not block). The items go back
    to stock, the invoice stays on file as Cancelled and its number stays used."""
    allowed, why = require_permission("sales.use")
    if not allowed:
        return False, why
    reason = _text(reason)
    if not reason:
        return False, "Say why the invoice is voided."
    from engines.review_flags import flag
    with write_tx() as conn:
        head = conn.execute("SELECT status, si_no, total, no_stock_out FROM sales WHERE sale_id = ?", (int(sale_id),)).fetchone()
        if not head or head[0] != "SAVED":
            return False, "Only a saved invoice can be voided."
        said = [] if head[3] else _put_back(conn, int(sale_id), f"{si_text(head[1])} voided: {reason}")
        conn.execute("UPDATE sales SET status = 'VOID', void_reason = ?, voided_by = acting_user(), voided_at = CURRENT_TIMESTAMP WHERE sale_id = ?", (reason, int(sale_id)))
        note = f"{si_text(head[1])} (₱{float(head[2]):,.2f}) voided" + (f"; {', '.join(said)} back in stock" if said else "") + f". Why: {reason}"
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'SALE_VOIDED', 'sales', ?, ?)", (int(sale_id), note))
        flag(conn, "SALE_VOIDED", "sales", int(sale_id), note)
    return True, f"{si_text(head[1])} is voided" + (f": {', '.join(said)} back in stock." if said else ".")


def return_item(sale_id: int, line_id: int, qty, reason: str) -> tuple[bool, str]:
    """One item taken back: the invoice stays, the item returns to stock and the invoice's amount goes down (a credit note). Flagged for review."""
    allowed, why = require_permission("sales.use")
    if not allowed:
        return False, why
    reason = _text(reason)
    try:
        qty = float(str(qty).replace(",", ""))
    except (TypeError, ValueError):
        return False, "Type how many came back."
    if not reason:
        return False, "Say why the item came back."
    from engines.review_flags import flag
    from engines.stock_ledger import stock_return
    with write_tx() as conn:
        row = conn.execute("""SELECT s.status, s.si_no, l.item_id, l.description, l.qty, l.qty_returned, l.unit_price, l.unit_cost FROM sale_lines l JOIN sales s ON s.sale_id = l.sale_id
                              WHERE l.line_id = ? AND l.sale_id = ?""", (int(line_id), int(sale_id))).fetchone()
        if not row or row[0] != "SAVED" or row[2] is None:
            return False, "Choose an item of a saved invoice."
        left = float(row[4]) - float(row[5] or 0)
        if qty <= 0 or qty > left + 1e-9:
            return False, f"{row[3]}: {left:g} can come back, not {qty:g}."
        amount = round(qty * float(row[6]), 2)
        stock_return(conn, row[2], qty, float(row[7]), "sales", int(sale_id), note=f"returned from {si_text(row[1])}: {reason}")
        conn.execute("UPDATE sale_lines SET qty_returned = qty_returned + ? WHERE line_id = ?", (qty, int(line_id)))
        conn.execute("INSERT INTO sale_returns (sale_id, line_id, qty, amount, reason, returned_on, created_by) VALUES (?, ?, ?, ?, ?, ?, acting_user())",
                     (int(sale_id), int(line_id), qty, amount, reason, str(manila_today())))
        note = f"{si_text(row[1])}: {qty:g} x {row[3]} came back (₱{amount:,.2f}). Why: {reason}"
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'SALE_RETURN', 'sales', ?, ?)", (int(sale_id), note))
        flag(conn, "SALE_RETURN", "sales", int(sale_id), note)
    return True, f"{qty:g} x {row[3]} back in stock; {si_text(row[1])} is ₱{amount:,.2f} less."


# --- reading ------------------------------------------------------------------------------------------------------------------------------------------

def get_sale(sale_id: int) -> dict | None:
    with get_conn() as conn:
        h = conn.execute("""SELECT s.sale_id, s.si_no, s.release_no, s.sale_date, s.customer_id, COALESCE(c.customer_name, NULLIF(s.customer_name, ''), 'Walk-in'), s.status, s.total,
                                   s.cost_total, s.typed_late, s.below_cost_reason, s.void_reason, s.voided_by, s.created_by, s.created_at, s.no_stock_out, s.si_hint
                            FROM sales s LEFT JOIN customers c ON c.customer_id = s.customer_id WHERE s.sale_id = ?""", (int(sale_id),)).fetchone()
        if not h:
            return None
        lines = [{"line_id": int(a), "item_id": b, "description": c, "qty": float(d), "unit_price": float(e), "amount": float(f), "unit_cost": float(g), "qty_returned": float(x or 0),
                  "profit": round((float(e) - float(g)) * (float(d) - float(x or 0)), 2), "service": b is None}
                 for a, b, c, d, e, f, g, x in conn.execute("SELECT line_id, item_id, description, qty, unit_price, amount, unit_cost, qty_returned FROM sale_lines WHERE sale_id = ? ORDER BY line_id", (int(sale_id),))]
        pays = [{"method": a, "label": METHODS.get(a, a), "amount": float(b), "bank": c or "", "check_no": d or "", "check_date": e, "reference": f or ""}
                for a, b, c, d, e, f in conn.execute("SELECT method, amount, bank, check_no, check_date, reference FROM sale_payments WHERE sale_id = ? ORDER BY payment_id", (int(sale_id),))]
        returns = [{"description": a, "qty": float(b), "amount": float(c), "reason": d, "on": e, "by": f or ""}
                   for a, b, c, d, e, f in conn.execute("""SELECT l.description, r.qty, r.amount, r.reason, r.returned_on, r.created_by FROM sale_returns r JOIN sale_lines l ON l.line_id = r.line_id
                                                           WHERE r.sale_id = ? ORDER BY r.return_id""", (int(sale_id),))]
    keys = ("sale_id", "si_no", "release_no", "sale_date", "customer_id", "customer", "status", "total", "cost_total", "typed_late", "below_cost_reason", "void_reason", "voided_by",
            "created_by", "created_at", "no_stock_out", "si_hint")
    out = dict(zip(keys, h))
    returned = round(sum(r["amount"] for r in returns), 2)
    out.update({"lines": lines, "payments": pays, "returns": returns, "returned": returned, "net": round(float(out["total"] or 0) - returned, 2),
                "ref": si_text(out["si_no"]) or out["release_no"], "charged": sum(p["amount"] for p in pays if p["method"] == "CHARGE"),
                "profit": round(sum(ln["profit"] for ln in lines), 2)})
    return out


def _paid_text(pays: list[tuple]) -> str:
    parts = []
    for method, amount, check_date in pays:
        word = {"CASH": "Cash", "CHECK": "Check", "TRANSFER": "Transfer", "CARD": "Card", "CHARGE": "Charge"}[method]
        if method == "CHECK" and check_date:
            word += f" {str(check_date)[5:7]}/{str(check_date)[8:10]}"
        parts.append(word)
    return " + ".join(dict.fromkeys(parts))


def day_sheet(day=None) -> dict:
    """The day's sales in the notebook's order (owner, 2026-09-28): one row per item line -- qty, item, profit, the customer when it is a charge -- plus what
    the notebook lacks: the SI number, the amount and how it was paid. {'day', 'rows', 'invoices', 'pieces', 'sales', 'profit', 'below', 'late',
    'money': {method: amount, 'CHECK_TODAY', 'CHECK_LATER'}, 'voided': [...]}."""
    day = _as_date(day) or manila_today()
    with get_conn() as conn:
        heads = conn.execute("""SELECT s.sale_id, s.si_no, COALESCE(c.customer_name, NULLIF(s.customer_name, ''), ''), s.typed_late, s.status, s.total, s.void_reason
                                FROM sales s LEFT JOIN customers c ON c.customer_id = s.customer_id WHERE s.sale_date = ? AND s.status IN ('SAVED', 'VOID') AND s.si_no IS NOT NULL
                                ORDER BY s.sale_id""", (str(day),)).fetchall()
        rows, money, voided, pieces, sales, profit, below, late, n = [], {m: 0.0 for m in METHODS}, [], 0.0, 0.0, 0.0, 0, 0, 0
        money.update({"CHECK_TODAY": 0.0, "CHECK_LATER": 0.0})
        for sale_id, si_no, who, typed_late, status, total, void_reason in heads:
            if status == "VOID":
                voided.append({"sale_id": int(sale_id), "ref": si_text(si_no), "total": float(total or 0), "reason": void_reason or ""})
                continue
            n += 1
            late += 1 if typed_late else 0
            pays = conn.execute("SELECT method, amount, check_date FROM sale_payments WHERE sale_id = ? ORDER BY payment_id", (sale_id,)).fetchall()
            for method, amount, check_date in pays:
                money[method] += float(amount)
                if method == "CHECK":
                    money["CHECK_LATER" if check_date and str(check_date)[:10] > str(day) else "CHECK_TODAY"] += float(amount)
            charge = any(p[0] == "CHARGE" for p in pays)
            for line_id, item_id, name, qty, price, amount, cost, returned in conn.execute(
                    "SELECT line_id, item_id, description, qty, unit_price, amount, unit_cost, qty_returned FROM sale_lines WHERE sale_id = ? ORDER BY line_id", (sale_id,)):
                kept = float(qty) - float(returned or 0)
                p = round((float(price) - float(cost)) * kept, 2)
                rows.append({"sale_id": int(sale_id), "ref": f"{int(si_no):06d}", "qty": kept, "item": name, "profit": p, "customer": who if charge else "", "amount": round(float(price) * kept, 2),
                             "paid": _paid_text(pays), "below": item_id is not None and float(price) < float(cost) - EPS, "service": item_id is None, "late": bool(typed_late),
                             "returned": float(returned or 0)})
                pieces += kept if item_id is not None else 0
                sales += round(float(price) * kept, 2)
                profit += p
                below += 1 if item_id is not None and float(price) < float(cost) - EPS else 0
    return {"day": day, "rows": rows, "invoices": n, "pieces": pieces, "sales": round(sales, 2), "profit": round(profit, 2), "below": below, "late": late, "money": money, "voided": voided}


def open_releases() -> list[dict]:
    """Goods out with no invoice yet: sale_id, release_no, when, customer, items, by, old (released before today)."""
    today = str(manila_today())
    with get_conn() as conn:
        heads = conn.execute("""SELECT s.sale_id, s.release_no, s.sale_date, s.released_at, COALESCE(c.customer_name, NULLIF(s.customer_name, ''), 'Walk-in'), s.created_by, s.si_hint
                                FROM sales s LEFT JOIN customers c ON c.customer_id = s.customer_id WHERE s.status = 'RELEASED' ORDER BY s.sale_id DESC""").fetchall()
        out = []
        for sale_id, number, day, when, who, by, hint in heads:
            detail = conn.execute("SELECT description, qty FROM sale_lines WHERE sale_id = ? ORDER BY line_id", (sale_id,)).fetchall()
            items = ", ".join(f"{float(q):g} × {d}" for d, q in detail)
            out.append({"sale_id": int(sale_id), "release_no": number, "day": day, "when": when, "customer": who, "items": items, "by": by or "",
                        "old": str(day) < today, "si_hint": hint or "", "pcs": round(sum(float(q) for _, q in detail), 2)})
    return out


def flag_open_releases() -> int:
    """A release still not invoiced the day after goes on the Review list (once)."""
    from engines.review_flags import flag
    old = [r for r in open_releases() if r["old"]]
    if old:
        with write_tx() as conn:
            for r in old:
                flag(conn, "RELEASE_OPEN", "sales", r["sale_id"], f"{r['release_no']} · {r['customer']}: {r['items']} released on {str(r['day'])[5:7]}/{str(r['day'])[8:10]} and not invoiced.")
    return len(old)
