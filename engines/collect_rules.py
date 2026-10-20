"""Stage B, part 1 (owner, 2026-10-11: "the important to us is receivables can be track, any payments thereafter, BIR CR is optional"):
every charge sale is a receivable; a collection pays the oldest first, partial is fine, more than owed stays as the customer's advance;
the receipt number is the system's own and optional; write-off is the System admin's alone, with a reason, and a later payment is a recovery.
Collected at the counter by whoever is signed in with the switch. Controls flag, they never block."""
from __future__ import annotations

from engines.clock import manila_now, manila_today
from engines.common import get_conn, write_tx
from engines.permissions import require as require_permission
from engines.sales_rules import _as_date, _text, si_text

EPS = 0.005
METHODS = {"CASH": "Cash", "CHECK": "Check (incl. post-dated)", "TRANSFER": "Transfer / GCash / Maya", "CARD": "Card", "ADVANCE": "Use the customer's advance"}


def _ref(si_no, release_no, description=None) -> str:
    return si_text(si_no) or release_no or (description or "")


def owed_rows(customer_id: int, conn=None) -> list[dict]:
    """The customer's open charge invoices, oldest first: sale_id, ref, sale_date, due_date, days_over, charged, collected, owed, written_off."""
    own = conn is None
    conn = conn or get_conn().__enter__()
    try:
        rows = conn.execute(
            """SELECT s.sale_id, s.si_no, s.release_no, s.sale_date, s.is_opening, s.written_off_at, COALESCE(c.terms, ''),
                      (SELECT COALESCE(SUM(p.amount), 0) FROM sale_payments p WHERE p.sale_id = s.sale_id AND p.method = 'CHARGE')
                      + (SELECT COALESCE(SUM(p.amount), 0) FROM sale_payments p JOIN customer_check_state m ON m.source = 'sale' AND m.payment_id = p.payment_id
                         WHERE p.sale_id = s.sale_id AND p.method = 'CHECK' AND m.bounced_on IS NOT NULL),
                      (SELECT COALESCE(SUM(l.amount), 0) FROM collection_lines l JOIN collections k ON k.collection_id = l.collection_id
                        WHERE l.sale_id = s.sale_id AND k.voided_at IS NULL)
               FROM sales s LEFT JOIN customers c ON c.customer_id = s.customer_id
               WHERE s.customer_id = ? AND s.status = 'SAVED' ORDER BY s.sale_date, s.sale_id""", (int(customer_id),)).fetchall()
    finally:
        if own:
            conn.close()
    out = []
    today = manila_today()
    for sale_id, si, rl, day, opening, woff, terms, charged, collected in rows:
        owed = round(float(charged) - float(collected), 2)
        if owed <= EPS and not woff:
            continue
        if charged <= EPS:
            continue
        from datetime import timedelta
        days = int(str(terms).strip() or 0) if str(terms).strip().isdigit() else 0
        due = _as_date(day) + timedelta(days=days)
        out.append({"sale_id": int(sale_id), "ref": _ref(si, rl) or ("opening" if opening else f"sale {sale_id}"), "sale_date": day, "due_date": str(due),
                    "days_over": max(0, (today - due).days), "charged": round(float(charged), 2), "collected": round(float(collected), 2),
                    "owed": owed, "written_off": bool(woff), "is_opening": bool(opening)})
    return out


def advance_balance(customer_id: int, conn=None) -> float:
    """What the customer has paid beyond their invoices and not used yet: received minus allocated over their collections."""
    own = conn is None
    conn = conn or get_conn().__enter__()
    try:
        r = conn.execute("SELECT COALESCE(SUM(received), 0) - COALESCE(SUM(allocated), 0) FROM collections WHERE customer_id = ? AND voided_at IS NULL",
                         (int(customer_id),)).fetchone()
    finally:
        if own:
            conn.close()
    return round(float(r[0] or 0), 2)


def balance(customer_id: int) -> dict:
    """{'owed', 'advance', 'net'} for the customer's head line."""
    rows = owed_rows(customer_id)
    owed = round(sum(r["owed"] for r in rows if not r["written_off"]), 2)
    adv = advance_balance(customer_id)
    return {"owed": owed, "advance": adv, "net": round(owed - adv, 2)}


def aging(customer_id: int) -> dict:
    """The owed amount in the statement's buckets, by each invoice's due date (sale date + the customer's terms)."""
    out = {"current": 0.0, "d30": 0.0, "d60": 0.0, "d90": 0.0, "over": 0.0}
    for r in owed_rows(customer_id):
        if r["written_off"]:
            continue
        d = r["days_over"]
        key = "current" if d <= 0 else "d30" if d <= 30 else "d60" if d <= 60 else "d90" if d <= 90 else "over"
        out[key] = round(out[key] + r["owed"], 2)
    out["total"] = round(sum(out.values()), 2)
    return out


def _clean_payments(payments: list[dict]) -> tuple[list[dict] | None, str]:
    out = []
    for p in payments or []:
        method = _text(p.get("method")).upper()
        raw = str(p.get("amount") or "").replace(",", "").strip()
        if not method and not raw:
            continue
        if method not in METHODS:
            return None, "Pick how it was paid on every line."
        try:
            amount = round(float(raw), 2)
        except ValueError:
            return None, f"'{raw}' is not an amount."
        if amount <= 0:
            return None, "An amount must be above zero."
        bank, no, day = _text(p.get("bank")), _text(p.get("check_no")), _as_date(p.get("check_date"))
        if method == "CHECK" and not (bank and no and day):
            return None, "A check needs its bank, check number and check date."
        out.append({"method": method, "amount": amount, "bank": bank or None, "check_no": no or None, "check_date": str(day) if day else None,
                    "reference": _text(p.get("reference")) or None})
    return out, ""


def _cr_label(n) -> str:
    """The receipt number when none was typed: the format from Settings > Company (default CR-NNNN)."""
    from engines.company import number
    return number("num_cr", int(n))


def collect(customer_id: int, cr_no, collect_date, lines: list[dict], payments: list[dict], note: str = "", white_copy: bool = False) -> tuple[bool, str, int | None]:
    """A customer pays: lines [{'sale_id', 'amount'}] against their open invoices (oldest first is the screen's suggestion, any split is allowed),
    payments one or several ways. Paid more than allocated: the rest stays as their advance. ADVANCE pays from that balance. The receipt number is
    optional (owner: "BIR CR is optional"). white_copy: the white copies of invoices fully paid now were handed over."""
    allowed, why = require_permission("collect.use")
    if not allowed:
        return False, why, None
    day = _as_date(collect_date)
    if day is None:
        return False, "Pick the date.", None
    if day > manila_today():
        return False, "The date cannot be after today.", None
    pays, problem = _clean_payments(payments)
    if pays is None:
        return False, problem, None
    with write_tx() as conn:
        cust = conn.execute("SELECT customer_name FROM customers WHERE customer_id = ? AND is_active = 1", (int(customer_id),)).fetchone()
        if not cust:
            return False, "Customer not found.", None
        open_by_id = {r["sale_id"]: r for r in owed_rows(customer_id, conn)}
        clean, recovered = [], []
        for ln in lines or []:
            raw = str(ln.get("amount") or "").replace(",", "").strip()
            if not raw:
                continue
            try:
                amount = round(float(raw), 2)
            except ValueError:
                return False, f"'{raw}' is not an amount.", None
            if amount <= 0:
                continue
            r = open_by_id.get(int(ln.get("sale_id") or 0))
            if r is None:
                return False, "An invoice on the receipt is not this customer's open invoice.", None
            if amount > r["owed"] + EPS:
                return False, f"{r['ref']}: {amount:,.2f} is more than the {r['owed']:,.2f} still owed on it.", None
            clean.append({**r, "amount": amount})
            if r["written_off"]:
                recovered.append(r)
        received = round(sum(p["amount"] for p in pays if p["method"] != "ADVANCE"), 2)
        advance_used = round(sum(p["amount"] for p in pays if p["method"] == "ADVANCE"), 2)
        allocated = round(sum(ln["amount"] for ln in clean), 2)
        if not clean and received <= 0:
            return False, "Nothing to collect: tick an invoice and type the amount.", None
        if advance_used > advance_balance(customer_id, conn) + EPS:
            return False, f"Only ₱{advance_balance(customer_id, conn):,.2f} of advance is available.", None
        if allocated > received + advance_used + EPS:
            return False, f"₱{allocated:,.2f} is put on invoices but only ₱{received + advance_used:,.2f} came in.", None
        extra = round(received + advance_used - allocated, 2)
        kid = conn.execute("""INSERT INTO collections (cr_no, customer_id, collect_date, received, allocated, advance_used, note, created_by)
                              VALUES (?, ?, ?, ?, ?, ?, ?, acting_user()) RETURNING collection_id""",
                           (_text(cr_no) or None, int(customer_id), str(day), received, allocated, advance_used, _text(note) or None)).fetchone()[0]
        for ln in clean:
            conn.execute("INSERT INTO collection_lines (collection_id, sale_id, amount) VALUES (?, ?, ?)", (kid, ln["sale_id"], ln["amount"]))
            if white_copy and abs(ln["amount"] - ln["owed"]) <= EPS:
                conn.execute("UPDATE sales SET white_copy_at = ? WHERE sale_id = ? AND white_copy_at IS NULL", (manila_now().strftime("%Y-%m-%d %H:%M:%S"), ln["sale_id"]))
        for p in pays:
            conn.execute("INSERT INTO collection_payments (collection_id, method, amount, bank, check_no, check_date, reference) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (kid, p["method"], p["amount"], p["bank"], p["check_no"], p["check_date"], p["reference"]))
        for r in recovered:
            conn.execute("UPDATE sales SET written_off_at = NULL, write_off_reason = NULL WHERE sale_id = ?", (r["sale_id"],))
            conn.execute("INSERT INTO review_flags (kind, ref_type, ref_id, detail, raised_by) VALUES ('AR_RECOVERED', 'sales', ?, ?, acting_user())",
                         (r["sale_id"], f"{r['ref']} · {cust[0]}: written off, now being paid again. The write-off is lifted."))
        label = (_text(cr_no) or _cr_label(kid))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'AR_COLLECTED', 'collections', ?, ?)",
                     (kid, f"{label} · {cust[0]}: received ₱{received:,.2f}" + (f" + advance ₱{advance_used:,.2f}" if advance_used else "")
                      + f", on {len(clean)} invoice(s)" + (f", ₱{extra:,.2f} kept as their advance" if extra > EPS else "")))
    msg = f"{label} saved: ₱{received + advance_used:,.2f}" + (f" — ₱{extra:,.2f} stays as {cust[0]}'s advance." if extra > EPS else ".")
    return True, msg, int(kid)


def void_collection(collection_id: int, reason: str) -> tuple[bool, str]:
    """A receipt typed wrongly: taken back with a reason, the invoices owed again. Nothing is deleted; noted for review."""
    allowed, why = require_permission("collect.use")
    if not allowed:
        return False, why
    if not _text(reason):
        return False, "Say why it is taken back."
    with write_tx() as conn:
        row = conn.execute("SELECT cr_no, customer_id, received, allocated, advance_used, voided_at FROM collections WHERE collection_id = ?",
                           (int(collection_id),)).fetchone()
        if not row:
            return False, "No such receipt."
        if row[5]:
            return False, "Already taken back."
        extra = round(float(row[2]) + float(row[4] or 0) - float(row[3]), 2)        # what this receipt left as the customer's advance
        if extra > EPS and advance_balance(int(row[1]), conn) < extra - EPS:
            return False, "This receipt's extra was already used as the customer's advance. Collect the difference instead."
        conn.execute("UPDATE collections SET voided_at = CURRENT_TIMESTAMP, voided_by = acting_user(), void_reason = ? WHERE collection_id = ?",
                     (_text(reason), int(collection_id)))
        label = row[0] or _cr_label(collection_id)
        conn.execute("INSERT INTO review_flags (kind, ref_type, ref_id, detail, raised_by) VALUES ('AR_COLLECTION_VOIDED', 'collections', ?, ?, acting_user())",
                     (int(collection_id), f"{label}: taken back — {_text(reason)}"))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'AR_COLLECTION_VOIDED', 'collections', ?, ?)",
                     (int(collection_id), f"{label}: {_text(reason)}"))
    return True, f"{label} taken back: its invoices are owed again."


def write_off(sale_id: int, reason: str) -> tuple[bool, str]:
    """Owner's judgement alone: this will not be collected. The invoice keeps its record and shows 'written off'; a later payment lifts it (a recovery)."""
    allowed, why = require_permission("settings.edit")          # the System admin's alone, like the other judgement calls
    if not allowed:
        return False, why
    if not _text(reason):
        return False, "Say why."
    with write_tx() as conn:
        row = conn.execute("""SELECT s.si_no, s.release_no, COALESCE(c.customer_name, s.customer_name, 'Walk-in') FROM sales s
                              LEFT JOIN customers c ON c.customer_id = s.customer_id WHERE s.sale_id = ? AND s.status = 'SAVED'""", (int(sale_id),)).fetchone()
        if not row:
            return False, "No such invoice."
        conn.execute("UPDATE sales SET written_off_at = CURRENT_TIMESTAMP, write_off_reason = ? WHERE sale_id = ?", (_text(reason), int(sale_id)))
        conn.execute("INSERT INTO review_flags (kind, ref_type, ref_id, detail, raised_by) VALUES ('AR_WRITTEN_OFF', 'sales', ?, ?, acting_user())",
                     (int(sale_id), f"{_ref(row[0], row[1])} · {row[2]}: written off — {_text(reason)}"))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'AR_WRITTEN_OFF', 'sales', ?, ?)",
                     (int(sale_id), f"{_ref(row[0], row[1])}: {_text(reason)}"))
    return True, "Written off. A payment later lifts it."


def opening(rows: list[dict]) -> tuple[bool, str]:
    """The notebook's unpaid invoices, typed once by the System admin before the start: each becomes a charge invoice (is_opening) whose balance
    moves only by collections from then on. rows: [{'customer' (name), 'si_no' (optional), 'sale_date', 'amount'}]."""
    allowed, why = require_permission("settings.edit")
    if not allowed:
        return False, why
    saved = 0
    with write_tx() as conn:
        for r in rows or []:
            name = _text(r.get("customer"))
            raw = str(r.get("amount") or "").replace(",", "").strip()
            if not name and not raw:
                continue
            day = _as_date(r.get("sale_date")) or manila_today()
            try:
                amount = round(float(raw), 2)
            except ValueError:
                return False, f"{name}: '{raw}' is not an amount."
            if not name or amount <= 0:
                return False, "Every line needs the customer and the amount still owed."
            row_c = conn.execute("SELECT customer_id FROM customers WHERE lower(trim(customer_name)) = ? AND is_active = 1", (name.lower(),)).fetchone()
            if row_c:
                cust_id = int(row_c[0])
            else:                                               # a new name on the list: made here, in the same locked step
                cust_id = conn.execute("INSERT INTO customers (customer_name, customer_type) VALUES (?, 'RETAIL') RETURNING customer_id",
                                       (name,)).fetchone()[0]
            si = "".join(ch for ch in str(r.get("si_no") or "") if ch.isdigit())
            if si and conn.execute("SELECT 1 FROM sales WHERE si_no = ?", (int(si),)).fetchone():
                return False, f"SI {si} is already on file."
            sale_id = conn.execute("""INSERT INTO sales (si_no, sale_date, customer_id, status, total, is_opening, created_by)
                                      VALUES (?, ?, ?, 'SAVED', ?, 1, acting_user()) RETURNING sale_id""",
                                   (int(si) if si else None, str(day), cust_id, amount)).fetchone()[0]
            conn.execute("INSERT INTO sale_lines (sale_id, item_id, description, qty, unit_price, amount, unit_cost) VALUES (?, NULL, ?, 1, ?, ?, 0)",
                         (sale_id, f"Opening balance (typed {manila_today().strftime('%m/%d/%Y')})", amount, amount))
            conn.execute("INSERT INTO sale_payments (sale_id, method, amount) VALUES (?, 'CHARGE', ?)", (sale_id, amount))
            saved += 1
        if not saved:
            return False, "Nothing to save."
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'AR_OPENING_TYPED', 'sales', NULL, ?)",
                     (f"{saved} opening receivable(s) typed from the notebook.",))
    return True, f"{saved} opening receivable(s) saved."


def receivables_total(conn=None) -> tuple[float, int]:
    """(total owed, customers owing) for the Home card."""
    own = conn is None
    conn = conn or get_conn().__enter__()
    try:
        rows = conn.execute("""SELECT s.customer_id,
                                      (SELECT COALESCE(SUM(p.amount), 0) FROM sale_payments p WHERE p.sale_id = s.sale_id AND p.method = 'CHARGE')
                                      + (SELECT COALESCE(SUM(p.amount), 0) FROM sale_payments p JOIN customer_check_state m
                                         ON m.source = 'sale' AND m.payment_id = p.payment_id
                                         WHERE p.sale_id = s.sale_id AND p.method = 'CHECK' AND m.bounced_on IS NOT NULL)
                                      - (SELECT COALESCE(SUM(l.amount), 0) FROM collection_lines l JOIN collections k ON k.collection_id = l.collection_id
                                         WHERE l.sale_id = s.sale_id AND k.voided_at IS NULL)
                               FROM sales s WHERE s.status = 'SAVED' AND s.customer_id IS NOT NULL AND s.written_off_at IS NULL""").fetchall()
    finally:
        if own:
            conn.close()
    by = {}
    for cust, owed in rows:
        if owed and owed > EPS:
            by[cust] = by.get(cust, 0) + float(owed)
    return round(sum(by.values()), 2), len(by)


def collections_of(customer_id: int) -> list[dict]:
    """The customer's receipts, newest first, for their page."""
    with get_conn() as conn:
        rows = conn.execute("""SELECT k.collection_id, k.cr_no, k.collect_date, k.received, k.allocated, k.advance_used, k.voided_at, k.created_by,
                                      (SELECT GROUP_CONCAT(COALESCE('SI ' || printf('%06d', s.si_no), s.release_no), ', ')
                                       FROM collection_lines l JOIN sales s ON s.sale_id = l.sale_id WHERE l.collection_id = k.collection_id)
                               FROM collections k WHERE k.customer_id = ? ORDER BY k.collection_id DESC""", (int(customer_id),)).fetchall()
    return [{"collection_id": int(a), "label": b or _cr_label(a), "day": c, "received": float(d), "allocated": float(e), "advance_used": float(f or 0),
             "void": bool(g), "by": h or "", "covers": i or "", "extra": round(float(d) + float(f or 0) - float(e), 2)} for a, b, c, d, e, f, g, h, i in rows]


def flag_overdue() -> int:
    """Every open charge invoice past its customer's terms goes on the Review list once. Flags, never blocks."""
    from engines.review_flags import flag
    n = 0
    with get_conn() as conn:
        customers = [int(r[0]) for r in conn.execute("SELECT DISTINCT customer_id FROM sales WHERE status = 'SAVED' AND customer_id IS NOT NULL")]
    late = []
    for cid in customers:
        for r in owed_rows(cid):
            if r["days_over"] > 0 and not r["written_off"]:
                late.append((cid, r))
    if late:
        with write_tx() as conn:
            for cid, r in late:
                name = conn.execute("SELECT customer_name FROM customers WHERE customer_id = ?", (cid,)).fetchone()[0]
                flag(conn, "AR_OVERDUE", "sales", r["sale_id"], f"{r['ref']} · {name}: past its terms (due {r['due_date'][5:7]}/{r['due_date'][8:10]}).")
                n += 1
    return n
