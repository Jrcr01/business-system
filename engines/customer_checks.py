"""Stage B part 2: the customers' checks (owner, 2026-10-11; blueprint: a post-dated check at the counter makes the sale paid, and it waits here).
Every check taken on a sale or a collection sits on one list by its check date: waiting, post-dated, deposited, cleared or bounced. A bounced check
on a sale makes that invoice owed again (the receivable returns) and goes on the Review list. A bounced check on a collection receipt is settled by
taking the receipt back and collecting again without it, so the books stay exact. The bank statement stays the truth: for now cleared and bounced
are marked from the statement by hand; the matching against imported statement lines comes with deposits (part 3)."""
from __future__ import annotations

from engines.clock import manila_today
from engines.common import get_conn, write_tx
from engines.permissions import require as require_permission
from engines.sales_rules import _as_date, _text, si_text

STATES = {"waiting": "To deposit", "postdated": "Post-dated", "deposited": "Deposited", "cleared": "Cleared", "bounced": "Bounced"}


def _rows(conn) -> list[dict]:
    sale_rows = conn.execute(
        """SELECT p.payment_id, s.sale_id, COALESCE(c.customer_name, NULLIF(s.customer_name, ''), 'Walk-in'), s.customer_id,
                  COALESCE('SI ' || printf('%06d', s.si_no), s.release_no), p.bank, p.check_no, p.check_date, p.amount
           FROM sale_payments p JOIN sales s ON s.sale_id = p.sale_id LEFT JOIN customers c ON c.customer_id = s.customer_id
           WHERE p.method = 'CHECK' AND s.status = 'SAVED'""").fetchall()
    coll_rows = conn.execute(
        """SELECT p.payment_id, k.collection_id, c.customer_name, k.customer_id, COALESCE(k.cr_no, 'CR-' || printf('%04d', k.collection_id)),
                  p.bank, p.check_no, p.check_date, p.amount
           FROM collection_payments p JOIN collections k ON k.collection_id = p.collection_id JOIN customers c ON c.customer_id = k.customer_id
           WHERE p.method = 'CHECK' AND k.voided_at IS NULL""").fetchall()
    marks = {(m[0], int(m[1])): m for m in conn.execute("SELECT source, payment_id, deposited_on, cleared_on, bounced_on, note FROM customer_check_state")}
    today = manila_today()
    out = []
    for source, rows in (("sale", sale_rows), ("collection", coll_rows)):
        for pid, ref_id, who, cust_id, ref, bank, no, day, amount in rows:
            m = marks.get((source, int(pid)))
            state = ("bounced" if m and m[4] else "cleared" if m and m[3] else "deposited" if m and m[2]
                     else "postdated" if day and _as_date(day) and _as_date(day) > today else "waiting")
            out.append({"source": source, "payment_id": int(pid), "ref_id": int(ref_id), "customer": who, "customer_id": cust_id, "ref": ref,
                        "bank": bank or "", "check_no": no or "", "check_date": day or "", "amount": float(amount), "state": state,
                        "state_label": STATES[state], "deposited_on": m[2] if m else None, "cleared_on": m[3] if m else None,
                        "bounced_on": m[4] if m else None, "note": (m[5] if m else "") or ""})
    out.sort(key=lambda r: (str(r["check_date"] or "9999"), r["customer"]))
    return out


def checks(show: str = "open") -> list[dict]:
    """The one list: show = 'open' (waiting, post-dated, deposited), 'bounced', or 'all'."""
    with get_conn() as conn:
        rows = _rows(conn)
    if show == "open":
        return [r for r in rows if r["state"] in ("waiting", "postdated", "deposited")]
    if show == "bounced":
        return [r for r in rows if r["state"] == "bounced"]
    return rows


def mark(source: str, payment_id: int, what: str, on=None, note: str = "") -> tuple[bool, str]:
    """From the deposit slip or the bank statement: 'deposited', 'cleared', 'bounced' — or 'waiting' to take a wrong mark back. A bounced check on a
    sale makes the invoice owed again; on a collection receipt it says to take the receipt back instead, so what each receipt paid stays exact."""
    allowed, why = require_permission("collect.use")
    if not allowed:
        return False, why
    if source not in ("sale", "collection") or what not in ("deposited", "cleared", "bounced", "waiting"):
        return False, "Not a known mark."
    day = _as_date(on) or manila_today()
    with write_tx() as conn:
        row = next((r for r in _rows(conn) if r["source"] == source and r["payment_id"] == int(payment_id)), None)
        if row is None:
            return False, "No such check."
        if what == "bounced" and source == "collection":
            return False, (f"{row['ref']}: take the receipt back instead (Collect payments > Receipts > Take back), then collect again without "
                           "this check. That keeps what each receipt paid exact.")
        conn.execute("""INSERT INTO customer_check_state (source, payment_id, created_by) VALUES (?, ?, acting_user())
                        ON CONFLICT(source, payment_id) DO NOTHING""", (source, int(payment_id)))
        if what == "waiting":
            conn.execute("DELETE FROM customer_check_state WHERE source = ? AND payment_id = ?", (source, int(payment_id)))
        else:
            sets = {"deposited": "deposited_on = ?", "cleared": "cleared_on = ?", "bounced": "bounced_on = ?"}[what]
            conn.execute(f"UPDATE customer_check_state SET {sets}, note = COALESCE(NULLIF(?, ''), note) WHERE source = ? AND payment_id = ?",
                         (str(day), _text(note), source, int(payment_id)))
        text = f"{row['bank']} {row['check_no']} ₱{row['amount']:,.2f} of {row['customer']} ({row['ref']})"
        if what == "bounced":
            conn.execute("INSERT INTO review_flags (kind, ref_type, ref_id, detail, raised_by) VALUES ('AR_CHECK_BOUNCED', 'sales', ?, ?, acting_user())",
                         (row["ref_id"], f"Bounced: {text}. The invoice is owed again." + (f" ({_text(note)})" if _text(note) else "")))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), ?, 'customer_checks', ?, ?)",
                     (f"AR_CHECK_{what.upper()}" if what != "waiting" else "AR_CHECK_UNMARKED", int(payment_id), text))
    said = {"deposited": "deposited", "cleared": "cleared — the bank statement is the word", "bounced": "bounced: the invoice is owed again",
            "waiting": "back to waiting"}[what]
    return True, f"{row['ref']} · {row['bank']} {row['check_no']}: {said}."


def bounced_on_sales(customer_id: int, conn) -> dict[int, float]:
    """sale_id -> bounced check amounts for this customer's sales: owed again (collect_rules adds it to what is charged)."""
    rows = conn.execute(
        """SELECT p.sale_id, SUM(p.amount) FROM sale_payments p JOIN customer_check_state m ON m.source = 'sale' AND m.payment_id = p.payment_id
           JOIN sales s ON s.sale_id = p.sale_id
           WHERE p.method = 'CHECK' AND m.bounced_on IS NOT NULL AND s.customer_id = ? GROUP BY p.sale_id""", (int(customer_id),)).fetchall()
    return {int(a): float(b) for a, b in rows}
