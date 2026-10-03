"""Purchase orders (cash disbursement cycle, stage B2, 2026-10-01).

A purchase order is the obligation BEFORE the supplier's invoice exists. Its lines are estimated from the supplier's price list (engines/price_list_service.py);
when the goods and the invoice arrive the actual price prevails (stage B4). Its expected pay date is the delivery date plus the supplier's terms, moved to a
bank day -- the date the cash forecast will count it (stage B3).

  * save_po / order_po / cancel_po   -- make, change, number and cancel an order
  * expected_pay_date                -- delivery + terms, on a bank day
  * get_po / list_pos                -- read them back (list_pos flags deliveries that are late)
  * open_commitments / po_outflow_rows -- what ordered orders still owe, and when (feeds the cash forecast and the planner, stage B3)
  * cash_check / suggest_order_date  -- does a new order fit the cash on its pay date, and if not, the first later order date that does
  * orders_over_plan / acknowledge_over_plan -- orders placed over the cash plan: the owner is told, not asked"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from engines.check_date_planner import _blocked_days, next_bank_day
from engines import settings_registry
from engines.clock import manila_now, manila_today
from engines.common import get_conn, write_tx
from engines.db_engine import next_doc_number
from engines.acting_user import acting_email
from engines.permissions import require as require_permission
from engines.discount_rules import chain_text, net_price, parse_chain, standing_chain
from engines.price_list_service import add_price, price_on
from engines.statement_rules import resolve_due_date

OPEN_STATUSES = ("DRAFT", "ORDERED")                     # an order can still be changed while it is in one of these and nothing has been received
LIVE_STATUSES = ("ORDERED", "PARTIAL")                   # sent to the supplier, goods still to come: what the cash forecast counts


def _as_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def expected_pay_date(creditor_id: int, delivery_date) -> date | None:
    """Delivery date + the supplier's terms (the same rule as an invoice's due date), moved forward to a bank day. None without a delivery date."""
    delivery = _as_date(delivery_date)
    if delivery is None:
        return None
    with get_conn() as conn:
        row = conn.execute("SELECT terms FROM creditors WHERE creditor_id = ?", (creditor_id,)).fetchone()
    return next_bank_day(resolve_due_date(delivery, row[0] if row else None), _blocked_days())


def _clean_lines(creditor_id: int, order_date: date, lines: list[dict]) -> tuple[list[dict] | None, str]:
    clean = []
    for n, ln in enumerate(lines or [], start=1):
        name = " ".join(str(ln.get("item_name") or "").split())
        if not name:
            return None, f"Line {n}: type the item."
        try:
            qty = float(ln.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            return None, f"Line {n} ({name}): the quantity must be more than zero."
        unit, price_id, price, list_price = str(ln.get("unit") or "").strip(), None, ln.get("est_unit_price"), ln.get("list_price")
        chain = ln.get("discount_chain")                                  # None = the supplier's standing chain; '' = no discount on this line (an override)
        if chain is not None and parse_chain(chain) is None:
            return None, f"Line {n} ({name}): the discount '{chain}' is not readable. Type percentages like 5, 5."
        listed = price_on(creditor_id, name, order_date)
        if listed:
            name = listed["item_name"]                                    # keep the list's spelling so the item is one item everywhere
        if list_price is not None and list_price != "":                   # a list price (typed or taken from the list) less its discount chain
            list_price = float(list_price)
            if list_price < 0:
                return None, f"Line {n} ({name}): the price cannot be negative."
            chain = standing_chain(creditor_id, name, order_date) if chain is None else chain
            price = net_price(list_price, chain)
            if listed and abs(listed["unit_price"] - list_price) < 0.005:
                price_id, unit = listed["price_id"], unit or listed["unit"]
        elif price is None or price == "":                                # nothing typed: the price in force on the order date, if the supplier has one
            if listed:
                list_price, price_id, unit = listed["unit_price"], listed["price_id"], unit or listed["unit"]
                chain = standing_chain(creditor_id, name, order_date) if chain is None else chain
                price = net_price(list_price, chain)
            else:
                price, chain = 0.0, None                          # a draft may wait for its price; ordering refuses a line with none (order_po)
        else:                                                             # a net price typed straight in: no list price, no chain
            chain, list_price = None, None
        price = float(price)
        if price < 0:
            return None, f"Line {n} ({name}): the price cannot be negative."
        clean.append({"item_id": (listed or {}).get("product_id"), "price_id": price_id, "item_name": name, "unit": unit or None, "qty": qty, "est_unit_price": price, "est_amount": round(qty * price, 2),
                      "list_price": list_price, "discount_chain": chain_text(chain) if list_price is not None else None})
    if not clean:
        return None, "Add at least one item."
    return clean, ""


def _may(action: str, po_id: int | None) -> tuple[bool, str]:
    """The permission guard for a purchase order: a role with 'own' (Purchasing) may only change orders it created."""
    created_by = acting_email()                                      # a new order is the signer's own
    if po_id is not None:
        with get_conn() as conn:
            row = conn.execute("SELECT created_by FROM purchase_orders WHERE po_id = ?", (po_id,)).fetchone()
        created_by = row[0] if row else None
        if not row:
            return True, ""                                           # not found: the function says so itself
    return require_permission(action, created_by)


def _has_receipt(conn, po_id: int) -> bool:
    return bool(conn.execute("SELECT 1 FROM po_receipts WHERE po_id = ? LIMIT 1", (po_id,)).fetchone())


def save_po(po_id: int | None, creditor_id: int, order_date, expected_delivery, lines: list[dict], notes: str = "") -> tuple[bool, str, int | None]:
    """Creates a draft order, or changes an open one (Draft / Ordered with nothing received yet): the lines are replaced and the estimate and pay date
    worked out again. Each line: item_name, qty, optional unit and est_unit_price (left out = the supplier's list price on the order date)."""
    allowed, why = _may("po.create", po_id)
    if not allowed:
        return False, why, None
    order = _as_date(order_date)
    delivery = _as_date(expected_delivery)
    if order is None:
        return False, "Pick the order date.", None
    if delivery is not None and delivery < order:
        return False, "The delivery date cannot be before the order date.", None
    clean, problem = _clean_lines(creditor_id, order, lines)
    if clean is None:
        return False, problem, None
    pay = expected_pay_date(creditor_id, delivery)
    with write_tx() as conn:
        terms = (conn.execute("SELECT terms FROM creditors WHERE creditor_id = ?", (creditor_id,)).fetchone() or [None])[0]
        if po_id is None:
            draft_prefix = f"DRAFT-PO-{manila_now().strftime('%y-%m')}-"
            used = [int(r[0][len(draft_prefix):]) for r in conn.execute("SELECT po_no FROM purchase_orders WHERE po_no LIKE ?", (draft_prefix + "%",))
                    if r[0][len(draft_prefix):].isdigit()]
            po_no = f"{draft_prefix}{max(used, default=0) + 1:02d}"
            po_id = conn.execute(
                """INSERT INTO purchase_orders (po_no, creditor_id, order_date, expected_delivery_date, terms_snapshot, expected_pay_date, notes, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, acting_user()) RETURNING po_id""",
                (po_no, creditor_id, str(order), str(delivery) if delivery else None, terms, str(pay) if pay else None, (notes or "").strip() or None)).fetchone()[0]
            action = "PO_CREATED"
        else:
            row = conn.execute("SELECT status, creditor_id FROM purchase_orders WHERE po_id = ?", (po_id,)).fetchone()
            if not row:
                return False, "Order not found.", None
            if row[0] not in OPEN_STATUSES or _has_receipt(conn, po_id):
                return False, f"This order is {row[0].lower()} and can no longer be changed.", None
            if int(row[1]) != int(creditor_id):
                return False, "The supplier of an order cannot be changed. Cancel it and make a new one.", None
            conn.execute(
                """UPDATE purchase_orders SET order_date = ?, expected_delivery_date = ?, terms_snapshot = ?, expected_pay_date = ?, notes = ?,
                          updated_at = CURRENT_TIMESTAMP WHERE po_id = ?""",
                (str(order), str(delivery) if delivery else None, terms, str(pay) if pay else None, (notes or "").strip() or None, po_id))
            conn.execute("DELETE FROM purchase_order_lines WHERE po_id = ?", (po_id,))
            action = "PO_EDITED"
        for ln in clean:
            conn.execute("INSERT INTO purchase_order_lines (po_id, item_id, price_id, item_name, unit, qty, est_unit_price, est_amount, list_price, discount_chain) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (po_id, ln["item_id"], ln["price_id"], ln["item_name"], ln["unit"], ln["qty"], ln["est_unit_price"], ln["est_amount"], ln["list_price"], ln["discount_chain"]))
        total = sum(ln["est_amount"] for ln in clean)
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), ?, 'purchase_orders', ?, ?)",
                     (action, po_id, f"{len(clean)} line(s), estimated {total:,.2f}, pay date {pay}"))
    update_cash_flag(int(po_id))
    return True, "Order saved.", int(po_id)


def order_po(po_id: int) -> tuple[bool, str]:
    """Marks a draft as ordered and gives it its permanent number 'YYYY - PO NNNN' (the year of the order date). Already ordered: nothing changes."""
    allowed, why = _may("po.order", po_id)
    if not allowed:
        return False, why
    with write_tx() as conn:
        row = conn.execute("SELECT status, po_no, order_date, expected_delivery_date FROM purchase_orders WHERE po_id = ?", (po_id,)).fetchone()
        if not row:
            return False, "Order not found."
        status, po_no, order_date, delivery = row
        if status != "DRAFT":
            return (True, f"{po_no} is already ordered.") if status in LIVE_STATUSES else (False, f"This order is {status.lower()}.")
        if not delivery:
            return False, "Enter the expected delivery date first: it decides when the payment falls due."
        total = conn.execute("SELECT COALESCE(SUM(est_amount), 0) FROM purchase_order_lines WHERE po_id = ?", (po_id,)).fetchone()[0]
        if total <= 0:
            return False, "No price on this order yet. Add the supplier's price (Price lists), or type a price on each line."
        unpriced = [r[0] for r in conn.execute("SELECT item_name FROM purchase_order_lines WHERE po_id = ? AND est_unit_price <= 0", (po_id,))]
        if unpriced:                                         # one line at zero would make the money owed look smaller than it is
            return False, "These lines have no price yet: " + ", ".join(unpriced) + ". Type their price (Change), or add them to the price list."
        year = int(str(order_date)[:4])
        prefix = f"{year} - PO "
        used = [int(r[0][len(prefix):]) for r in conn.execute("SELECT po_no FROM purchase_orders WHERE po_no LIKE ?", (prefix + "%",)) if r[0][len(prefix):].strip().isdigit()]
        number = f"{prefix}{next_doc_number(conn, 'PO', year, max(used, default=0)):04d}"
        conn.execute("UPDATE purchase_orders SET po_no = ?, status = 'ORDERED', updated_at = CURRENT_TIMESTAMP WHERE po_id = ?", (number, po_id))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'PO_ORDERED', 'purchase_orders', ?, ?)",
                     (po_id, f"{number}, estimated {total:,.2f}"))
    update_cash_flag(po_id)
    note_high_day(po_id)
    return True, f"Ordered as {number}."


def cancel_po(po_id: int, reason: str = "") -> tuple[bool, str]:
    allowed, why = _may("po.cancel", po_id)
    if not allowed:
        return False, why
    with write_tx() as conn:
        row = conn.execute("SELECT status, po_no FROM purchase_orders WHERE po_id = ?", (po_id,)).fetchone()
        if not row:
            return False, "Order not found."
        if row[0] == "CANCELLED":
            return False, "This order is already cancelled."
        if row[0] == "RECEIVED" or _has_receipt(conn, po_id):
            return False, "Goods have already been received on this order, so it cannot be cancelled."
        conn.execute("UPDATE purchase_orders SET status = 'CANCELLED', over_cash_plan = 0, cash_note = NULL, updated_at = CURRENT_TIMESTAMP WHERE po_id = ?", (po_id,))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'PO_CANCELLED', 'purchase_orders', ?, ?)",
                     (po_id, f"{row[1]}" + (f": {reason.strip()}" if reason and reason.strip() else "")))
    return True, f"{row[1]} cancelled."


def close_short(po_id: int, reason: str) -> tuple[bool, str]:
    """A partly received order whose rest will not come (the supplier dropped it): the order is closed as Received, and what was not delivered stops counting as
    money owed. Same permission as cancelling. A reason is required; audited."""
    allowed, why = _may("po.cancel", po_id)
    if not allowed:
        return False, why
    reason = " ".join(str(reason or "").split())
    if not reason:
        return False, "Say why the rest will not come (e.g. 'supplier out of stock')."
    with write_tx() as conn:
        row = conn.execute("SELECT status, po_no FROM purchase_orders WHERE po_id = ?", (po_id,)).fetchone()
        if not row:
            return False, "Order not found."
        if row[0] != "PARTIAL":
            return False, "Only a partly received order can be closed short. An order with nothing received can be cancelled instead."
        left = conn.execute("SELECT COALESCE(SUM((qty - qty_received) * est_unit_price), 0) FROM purchase_order_lines WHERE po_id = ?", (po_id,)).fetchone()[0]
        conn.execute("UPDATE purchase_orders SET status = 'RECEIVED', over_cash_plan = 0, cash_note = NULL, updated_at = CURRENT_TIMESTAMP WHERE po_id = ?", (po_id,))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'PO_CLOSED_SHORT', 'purchase_orders', ?, ?)",
                     (po_id, f"{row[1]}: closed with {left:,.2f} not delivered ({reason})"))
    return True, f"{row[1]} closed. The {left:,.2f} not delivered no longer counts as owed."


def get_po(po_id: int) -> dict | None:
    with get_conn() as conn:
        head = pd.read_sql_query(
            """SELECT p.*, c.creditor_name AS supplier FROM purchase_orders p JOIN creditors c ON c.creditor_id = p.creditor_id WHERE p.po_id = ?""", conn, params=(po_id,))
        if head.empty:
            return None
        lines = pd.read_sql_query("SELECT * FROM purchase_order_lines WHERE po_id = ? ORDER BY line_id", conn, params=(po_id,))
    header = {k: (None if pd.isna(v) else v) for k, v in head.iloc[0].to_dict().items()}
    return {"header": header, "lines": lines, "total": float(lines["est_amount"].sum()) if not lines.empty else 0.0, "supplier": header["supplier"]}


def list_pos(status: str | None = None, creditor_id: int | None = None, today: date | None = None) -> pd.DataFrame:
    today = today or manila_today()
    where, params = [], []
    if status:
        where.append("p.status = ?")
        params.append(status)
    if creditor_id:
        where.append("p.creditor_id = ?")
        params.append(creditor_id)
    with get_conn() as conn:
        df = pd.read_sql_query(
            f"""SELECT p.po_id, p.po_no, c.creditor_name AS supplier, p.creditor_id, p.order_date, p.expected_delivery_date, p.expected_pay_date, p.status,
                       COALESCE((SELECT SUM(est_amount) FROM purchase_order_lines l WHERE l.po_id = p.po_id), 0) AS total
                FROM purchase_orders p JOIN creditors c ON c.creditor_id = p.creditor_id
                {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY p.po_id DESC""", conn, params=params)
    df["delivery_overdue"] = df["status"].isin(LIVE_STATUSES) & df["expected_delivery_date"].notna() & (df["expected_delivery_date"].fillna("") < today.isoformat())
    return df


# --- the cash plan (stage B3) ------------------------------------------------------------------------------------------------------------------

def open_commitments(start: date, end: date) -> pd.DataFrame:
    """What ordered purchase orders still owe, by pay date (inside start..end; a pay date already past counts on `start`): each line's estimate times the share of its
    quantity the supplier has not yet INVOICED -- goods still to come and goods received but not yet invoiced both count, until the real invoice (a payable) replaces them.
    A closed-short order counts only what was delivered. Columns: po_id, po_no, supplier, day, amount."""
    cols = ["po_id", "po_no", "supplier", "day", "amount"]
    with get_conn() as conn:
        df = pd.read_sql_query(
            """SELECT p.po_id, p.po_no, c.creditor_name AS supplier, p.expected_pay_date,
                      SUM(l.est_amount * MAX(CASE WHEN p.status = 'RECEIVED' THEN l.qty_received ELSE l.qty END - COALESCE(l.qty_invoiced, l.qty_received), 0) / l.qty) AS amount
               FROM purchase_orders p JOIN creditors c ON c.creditor_id = p.creditor_id JOIN purchase_order_lines l ON l.po_id = p.po_id
               WHERE p.status IN ('ORDERED', 'PARTIAL', 'RECEIVED') AND p.expected_pay_date IS NOT NULL GROUP BY p.po_id""", conn)
    if df.empty:
        return pd.DataFrame(columns=cols)
    blocked = _blocked_days()
    df["day"] = df["expected_pay_date"].map(lambda d: max(next_bank_day(_as_date(d), blocked), start))
    df = df[(df["day"] <= end) & (df["amount"] > 0.004)]
    return df[cols].reset_index(drop=True)


def po_outflow_rows(start: date, end: date) -> list[dict]:
    """The commitments as forecast rows (track 'Purchase order'), or nothing when the setting leaves orders out of the forecast."""
    if not settings_registry.int_value("po_in_forecast"):
        return []
    return [{"day": r.day, "track": "Purchase order", "payee": r.supplier, "reference": r.po_no, "amount": float(r.amount)}
            for r in open_commitments(start, end).itertuples()]


def _cash_context(today: date, horizon_days: int) -> dict | None:
    """The day-by-day forecast (open orders included), {'stale': days} when the bank balance is too old to trust, or None without any balance."""
    from engines.cash_insights_service import cash_position, forecast_fresh_days, forecast_if_shown

    pos = cash_position()
    if pos is None:
        return None
    age = (today - pos["as_of"]).days
    if age > forecast_fresh_days():
        return {"stale": age}
    return forecast_if_shown(min(max(horizon_days, 1), 900), today) or None


def _own_commitment(po_id: int | None, today: date) -> tuple[date, float] | None:
    if not po_id:
        return None
    df = open_commitments(today, today + timedelta(days=2000))
    row = df[df["po_id"] == po_id]
    return (row.iloc[0]["day"], float(row.iloc[0]["amount"])) if not row.empty else None


def _evaluate(daily: pd.DataFrame, pay_day: date, amount: float, buffer: float, own: tuple[date, float] | None, window: int = 60) -> dict:
    """Balance from the pay date on if this order is added (an order being changed has its own old amount taken out first)."""
    d = daily.copy()
    adj = pd.Series(0.0, index=d.index)
    adj[d["day"] >= pay_day] -= amount
    if own:
        adj[d["day"] >= own[0]] += own[1]
    d["after"] = d["balance"] + adj
    span = d[(d["day"] >= pay_day) & (d["day"] <= pay_day + timedelta(days=window))]
    low = span.loc[span["after"].idxmin()]
    on_day = span[span["day"] == pay_day]
    return {"balance_on_pay_date": float(on_day["after"].iloc[0]) if not on_day.empty else float(low["after"]), "lowest": (low["day"], round(float(low["after"]), 2)),
            "shortfall": round(max(buffer - float(low["after"]), 0.0), 2), "ok": float(low["after"]) >= buffer}


def cash_check(creditor_id: int, amount: float, delivery_date, today: date | None = None, exclude_po_id: int | None = None) -> dict:
    """Does the cash on this order's pay date cover it? Counts everything already going out (checks, loans, cards, other open orders) against the
    expected deposits, keeps the cash buffer, and looks 60 days past the pay date for a dip. `exclude_po_id`: an order being changed must not count itself twice.
    Returns {known, ok, pay_date, balance_on_pay_date, lowest: (day, balance), shortfall, buffer, message}; known False (ok None) without a recent bank balance."""
    today = today or manila_today()
    pay = expected_pay_date(creditor_id, delivery_date)
    if pay is None:
        return {"known": False, "ok": None, "pay_date": None, "message": "Enter the expected delivery date to check the cash."}
    pay_day = max(pay, today)
    ctx = _cash_context(today, (pay_day - today).days + 61)
    if not ctx or "daily" not in ctx:
        age = (ctx or {}).get("stale")
        why = ("The cash forecast is switched off (Settings), so the cash is not checked." if not settings_registry.forecast_shown() else
               f"The bank balance is {age} days old, so the cash cannot be checked. Import a newer statement." if age else "No bank balance on file, so the cash cannot be checked.")
        return {"known": False, "ok": None, "pay_date": pay, "message": why}
    res = _evaluate(ctx["daily"], pay_day, float(amount), ctx["buffer"], _own_commitment(exclude_po_id, today))
    return {"known": True, "pay_date": pay, "buffer": ctx["buffer"], "message": "", **res}


def suggest_order_date(creditor_id: int, amount: float, order_date, delivery_date, today: date | None = None, exclude_po_id: int | None = None,
                       max_defer_days: int | None = None) -> dict | None:
    """When the order does not fit: the first later order date (delivery moves with it, same lead time) on which it does, within `max_defer_days`
    (default: the setting). None when it already fits, the cash is unknown, or nothing in reach fits."""
    today = today or manila_today()
    max_defer = settings_registry.int_value("po_max_defer_days") if max_defer_days is None else int(max_defer_days)
    order, delivery = _as_date(order_date), _as_date(delivery_date)
    now = cash_check(creditor_id, amount, delivery, today=today, exclude_po_id=exclude_po_id)
    if not now["known"] or now["ok"]:
        return None
    far = expected_pay_date(creditor_id, delivery + timedelta(days=max_defer))
    ctx = _cash_context(today, (max(far, today) - today).days + 61)
    if not ctx or "daily" not in ctx:
        return None
    own = _own_commitment(exclude_po_id, today)
    for k in range(1, max_defer + 1):
        pay = expected_pay_date(creditor_id, delivery + timedelta(days=k))
        if _evaluate(ctx["daily"], max(pay, today), float(amount), ctx["buffer"], own)["ok"]:
            return {"days_later": k, "order_date": order + timedelta(days=k), "delivery_date": delivery + timedelta(days=k), "pay_date": pay}
    return None


def update_cash_flag(po_id: int) -> None:
    """Sets or clears the 'over the cash plan' flag of an ordered order from a fresh cash check. A draft, a cancelled or a received order carries no flag.
    Changing an order starts a new look, so an earlier acknowledgement is cleared unless the finding is the same."""
    po = get_po(po_id)
    if po is None:
        return
    h = po["header"]
    over, note = 0, None
    if h["status"] in LIVE_STATUSES and h["expected_delivery_date"]:
        chk = cash_check(int(h["creditor_id"]), po["total"], h["expected_delivery_date"], exclude_po_id=po_id)
        if chk["known"] and not chk["ok"]:
            day, bal = chk["lowest"]
            over, note = 1, f"Cash would be short by {chk['shortfall']:,.2f} (lowest {bal:,.2f} on {day:%b %d, %Y}) when this falls due on {chk['pay_date']:%b %d, %Y}."
    with write_tx() as conn:
        was = conn.execute("SELECT over_cash_plan, cash_note FROM purchase_orders WHERE po_id = ?", (po_id,)).fetchone()
        keep_ack = bool(was and was[0] == over and was[1] == note)
        conn.execute("UPDATE purchase_orders SET over_cash_plan = ?, cash_note = ?, cash_ack_at = CASE WHEN ? THEN cash_ack_at ELSE NULL END WHERE po_id = ?",
                     (over, note, 1 if keep_ack else 0, po_id))


def orders_over_plan(include_acknowledged: bool = False) -> pd.DataFrame:
    if not settings_registry.forecast_shown():             # the cash plan is hidden, so nothing is "over" it
        return pd.DataFrame(columns=["po_id", "po_no", "supplier", "expected_pay_date", "cash_note", "cash_ack_at", "created_by", "total"])
    with get_conn() as conn:
        return pd.read_sql_query(
            f"""SELECT p.po_id, p.po_no, c.creditor_name AS supplier, p.expected_pay_date, p.cash_note, p.cash_ack_at, p.created_by,
                       COALESCE((SELECT SUM(est_amount) FROM purchase_order_lines l WHERE l.po_id = p.po_id), 0) AS total
                FROM purchase_orders p JOIN creditors c ON c.creditor_id = p.creditor_id
                WHERE p.over_cash_plan = 1 AND p.status IN ('ORDERED', 'PARTIAL') {'' if include_acknowledged else 'AND p.cash_ack_at IS NULL'}
                ORDER BY p.expected_pay_date""", conn)


def acknowledge_over_plan(po_id: int) -> tuple[bool, str]:
    allowed, why = require_permission("po.ack_over_plan")
    if not allowed:
        return False, why
    with write_tx() as conn:
        row = conn.execute("SELECT po_no FROM purchase_orders WHERE po_id = ? AND over_cash_plan = 1", (po_id,)).fetchone()
        if not row:
            return False, "This order is not over the cash plan."
        conn.execute("UPDATE purchase_orders SET cash_ack_at = CURRENT_TIMESTAMP WHERE po_id = ?", (po_id,))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'PO_OVER_PLAN_SEEN', 'purchase_orders', ?, ?)",
                     (po_id, f"{row[0]} noted by the owner"))
    return True, f"{row[0]} noted."


DEFAULT_LEAD_DAYS = 7
KIND_ORDER = ["Supplier checks", "Lender checks", "Bank loans", "Credit cards", "Purchase orders"]


def default_delivery(creditor_id: int, order_date) -> date:
    """The delivery date a new order starts with: the order date plus the supplier's usual delivery days (7 when not set)."""
    with get_conn() as conn:
        row = conn.execute("SELECT lead_days FROM creditors WHERE creditor_id = ?", (creditor_id,)).fetchone()
    return _as_date(order_date) + timedelta(days=int(row[0]) if row and row[0] is not None else DEFAULT_LEAD_DAYS)


def _week_of(day: date) -> tuple[date, date]:
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


_COMMITTED: dict = {}


def _committed_rows(end: date, exclude_po_id: int | None = None) -> pd.DataFrame:
    """Everything committed from today to `end`, as scheduled_outflows() counts it (a check already past its date sits on today), with a kind label.
    Columns: day, payee, kind, reference, amount. An order being changed is left out."""
    from engines import cash_insights_service as cis
    from engines.payables_due_service import KINDS
    from engines.settings_store import _stamp

    today = manila_today()
    stamp = (cis.DB_PATH, today, _stamp(cis.DB_PATH))
    hit = _COMMITTED.get("rows")
    if hit and hit[0] == stamp and hit[1] >= end:               # the schedule, the 8-week strip and the daily view share one build until something is saved
        out = hit[2]
    else:
        far = max(end, today + timedelta(days=400))
        out = cis.scheduled_outflows(today, far)
        _COMMITTED["rows"] = (stamp, far, out)
    out = out[pd.to_datetime(out["day"]).dt.date <= max(end, today)] if not out.empty else out
    cols = ["day", "payee", "kind", "reference", "amount"]
    if out.empty:
        return pd.DataFrame(columns=cols)
    out = out.assign(kind=out["track"].map(KINDS).fillna(out["track"]), day=pd.to_datetime(out["day"]).dt.date)
    if exclude_po_id:
        po = get_po(exclude_po_id)
        if po:
            out = out[~((out["kind"] == "Purchase orders") & (out["reference"] == po["header"]["po_no"]))]
    return out[cols].reset_index(drop=True)


def day_guard(creditor_id: int, delivery, estimate: float, exclude_po_id: int | None = None) -> dict:
    """The guard rail on the order form (owner, 2026-10-03): would the day this order falls due carry more payables than the daily limit (Settings, 0 = off)? A warning for the user to
    weigh, never a block. Returns {'on', 'limit', 'pay_date', 'already', 'with_order', 'over', 'suggestion'}; the suggestion is the nearest later delivery whose pay day stays within the
    limit ({'delivery_date', 'pay_date', 'with_order'}), or None when no day within three weeks fits (the order alone may be above the limit)."""
    from engines.settings_registry import value
    limit = float(value("po_daily_payables_limit"))
    pay = expected_pay_date(creditor_id, delivery) if delivery else None
    if limit <= 0 or not estimate or estimate <= 0 or pay is None:
        return {"on": False, "limit": limit, "pay_date": pay, "already": 0.0, "with_order": 0.0, "over": False, "suggestion": None}
    rows = _committed_rows(pay + timedelta(days=60), exclude_po_id)
    sums = rows.groupby("day")["amount"].sum() if not rows.empty else pd.Series(dtype=float)
    already = float(sums.get(pay, 0.0))
    out = {"on": True, "limit": limit, "pay_date": pay, "already": already, "with_order": already + float(estimate), "over": already + float(estimate) > limit, "suggestion": None}
    if out["over"]:
        first = _as_date(delivery)
        for k in range(1, 22):
            d = first + timedelta(days=k)
            p = expected_pay_date(creditor_id, d)
            load = float(sums.get(p, 0.0)) + float(estimate)
            if p and load <= limit:
                out["suggestion"] = {"delivery_date": d, "pay_date": p, "with_order": load}
                break
    return out


def note_high_day(po_id: int) -> None:
    """After an order is placed: if its pay day is over the daily limit, a line goes on the Review list (the user chose to order anyway). Never blocks."""
    po = get_po(po_id)
    if not po:
        return
    h = po["header"]
    g = day_guard(int(h["creditor_id"]), h["expected_delivery_date"], float(po["total"]), exclude_po_id=po_id)
    if g["over"]:
        from engines.review_flags import flag
        with write_tx() as conn:
            flag(conn, "PO_HIGH_DAY", "purchase_orders", po_id,
                 f"{h['po_no']}: {po['total']:,.2f} falls due {g['pay_date']:%b %d, %Y}, a day that would carry {g['with_order']:,.2f} against the limit of {g['limit']:,.2f}")


def week_schedule(pay_date, exclude_po_id: int | None = None) -> dict | None:
    """The Monday-to-Sunday week of `pay_date`, day by day: {'week_start', 'week_end', 'rows' (day, payee, kind, reference, amount), 'total'}."""
    day = _as_date(pay_date)
    if day is None:
        return None
    start, end = _week_of(day)
    rows = _committed_rows(end, exclude_po_id)
    rows = rows[(rows["day"] >= start) & (rows["day"] <= end)].sort_values(["day", "amount"], ascending=[True, False]).reset_index(drop=True)
    return {"week_start": start, "week_end": end, "rows": rows, "total": float(rows["amount"].sum())}


def weeks_strip(pay_date, before: int = 3, after: int = 4, exclude_po_id: int | None = None) -> pd.DataFrame:
    """Eight weeks around the week the order would fall due (never starting before this week): week_start, week_end, total, one column per kind,
    selected (the pay week), lightest (the week with the least already committed)."""
    day = _as_date(pay_date) or manila_today()
    this_week = _week_of(manila_today())[0]
    first = max(_week_of(day)[0] - timedelta(weeks=before), this_week)
    count = before + after + 1
    starts = [first + timedelta(weeks=i) for i in range(count)]
    rows = _committed_rows(starts[-1] + timedelta(days=6), exclude_po_id)
    table = pd.DataFrame({"week_start": starts, "week_end": [w + timedelta(days=6) for w in starts]})
    for kind in KIND_ORDER:
        table[kind] = 0.0
    if not rows.empty:
        wk = rows["day"].map(lambda d: d - timedelta(days=d.weekday()))
        sums = rows.assign(week=wk).pivot_table(index="week", columns="kind", values="amount", aggfunc="sum", fill_value=0.0)
        for kind in KIND_ORDER:
            if kind in sums.columns:
                table[kind] = table["week_start"].map(sums[kind]).fillna(0.0)
    table["total"] = table[KIND_ORDER].sum(axis=1)
    table["selected"] = table["week_start"] == _week_of(day)[0]
    table["lightest"] = table["total"] == table["total"].min()
    if table["lightest"].sum() > 1:                               # ties: mark the earliest only
        first_idx = table[table["lightest"]].index[0]
        table["lightest"] = table.index == first_idx
    return table


def days_strip(pay_date, before: int = 10, after: int = 20, exclude_po_id: int | None = None) -> pd.DataFrame:
    """The daily view of what is committed: one row per day from `before` days ahead of the pay day (never earlier than today) to `after` days after it:
    day, total, selected (the pay day), lightest (the weekday with the least committed; weekends are not bank days, so never 'lightest')."""
    day = _as_date(pay_date) or manila_today()
    first = max(day - timedelta(days=before), manila_today())
    days = [first + timedelta(days=i) for i in range(((day + timedelta(days=after)) - first).days + 1)]
    rows = _committed_rows(days[-1], exclude_po_id)
    sums = rows.groupby("day")["amount"].sum() if not rows.empty else pd.Series(dtype=float)
    table = pd.DataFrame({"day": days})
    table["total"] = table["day"].map(sums).fillna(0.0)
    table["selected"] = table["day"] == day
    weekdays = table[table["day"].map(lambda d: d.weekday() < 5)]
    table["lightest"] = False
    if not weekdays.empty:
        table.loc[weekdays["total"].idxmin(), "lightest"] = True
    return table


def delivery_for_week(creditor_id: int, week_start, order_date) -> date | None:
    """The earliest delivery date (on or after the order date) whose expected pay date, by the supplier's terms and on a bank day, falls in the week starting
    `week_start`. None when that week cannot be reached (it is before the order could be paid)."""
    wk = _as_date(week_start)
    order = _as_date(order_date)
    if wk is None or order is None:
        return None
    end = wk + timedelta(days=6)
    with get_conn() as conn:
        terms = (conn.execute("SELECT terms FROM creditors WHERE creditor_id = ?", (creditor_id,)).fetchone() or [None])[0]
    blocked = _blocked_days()
    for k in range(0, 900):
        d = order + timedelta(days=k)
        pay = next_bank_day(resolve_due_date(d, terms), blocked)
        if wk <= pay <= end:
            return d
        if pay > end:
            return None
    return None


def committed_load(pay_date, new_amount: float = 0.0, exclude_po_id: int | None = None) -> dict | None:
    """What is already committed to fall due in the week of `pay_date` (see week_schedule), as one summary: week_start, week_end, checks, loans_cards, orders,
    total, adds. An order being changed does not count itself."""
    sched = week_schedule(pay_date, exclude_po_id)
    if sched is None:
        return None
    by = sched["rows"].groupby("kind")["amount"].sum().to_dict()
    checks = float(by.get("Supplier checks", 0.0) + by.get("Lender checks", 0.0))
    loans_cards = float(by.get("Bank loans", 0.0) + by.get("Credit cards", 0.0))
    orders = float(by.get("Purchase orders", 0.0))
    return {"week_start": sched["week_start"], "week_end": sched["week_end"], "checks": checks, "loans_cards": loans_cards, "orders": orders,
            "total": checks + loans_cards + orders, "adds": float(new_amount)}


# --- receiving: matching the supplier's invoice to its order (stage B4) -------------------------------------------------------------------------

def receivable_lines(creditor_id: int) -> pd.DataFrame:
    """The supplier's order lines that were sent to the supplier and are not fully received yet. Columns: po_id, po_no, line_id, item_name, unit, qty,
    qty_received, remaining, est_unit_price, expected_delivery_date."""
    cols = ["po_id", "po_no", "line_id", "item_name", "unit", "qty", "qty_received", "remaining", "est_unit_price", "expected_delivery_date"]
    with get_conn() as conn:
        df = pd.read_sql_query(
            """SELECT p.po_id, p.po_no, l.line_id, l.item_name, l.unit, l.qty, l.qty_received, l.qty - l.qty_received AS remaining, l.est_unit_price,
                      p.expected_delivery_date
               FROM purchase_orders p JOIN purchase_order_lines l ON l.po_id = p.po_id
               WHERE p.creditor_id = ? AND p.status IN ('ORDERED', 'PARTIAL') AND l.qty - l.qty_received > 0.0000001
               ORDER BY p.po_id, l.line_id""", conn, params=(creditor_id,))
    return df[cols] if not df.empty else pd.DataFrame(columns=cols)


def receive_goods(creditor_id: int, picks: list[dict], received_date=None, ref: str = "", doc_type: str = "delivery receipt", book_stock: bool = True) -> tuple[bool, str, None]:
    """Step 1 of receiving (Purchasing): the goods that arrived. picks: [{'line_id', 'qty'}]. Records the quantity on the order lines and moves the order to Partly
    received / Received; no invoice and no batch -- the supplier's invoice is matched later (receive_into_batch with goods_received=True). Received-but-not-invoiced goods
    keep counting as owed at the order price. `ref` = the number of the paper that came with the goods: a delivery receipt (stored as "DR <no>") or, for a supplier who gives
    no delivery receipt, their invoice (doc_type="invoice": stored as the invoice number, and offered again when Payables matches the invoice). Everything is checked first; a refusal writes nothing."""
    allowed, why = require_permission("po.receive_goods")
    if not allowed:
        return False, why, None
    if not picks:
        return False, "Tick at least one line that arrived.", None
    rows = []
    with get_conn() as conn:
        for n, pk in enumerate(picks, start=1):
            ln = conn.execute(
                """SELECT l.line_id, l.po_id, l.item_name, l.qty, l.qty_received, l.est_unit_price, p.status, p.creditor_id, p.po_no, l.item_id
                   FROM purchase_order_lines l JOIN purchase_orders p ON p.po_id = l.po_id WHERE l.line_id = ?""", (int(pk["line_id"]),)).fetchone()
            if not ln:
                return False, f"Line {n}: order line not found.", None
            if int(ln[7]) != int(creditor_id):
                return False, f"Line {n} ({ln[2]}): that order is for another supplier.", None
            if ln[6] not in LIVE_STATUSES:
                return False, f"Line {n} ({ln[2]}): {ln[8]} has not been sent to the supplier or is closed, so it cannot be received.", None
            try:
                qty = float(pk["qty"])
            except (TypeError, ValueError):
                return False, f"Line {n} ({ln[2]}): enter the quantity that arrived.", None
            if qty <= 0:
                return False, f"Line {n} ({ln[2]}): the quantity that arrived must be more than zero.", None
            remaining = float(ln[3]) - float(ln[4])
            if qty > remaining + 1e-6:
                return False, f"Line {n} ({ln[2]}): {qty:g} is more than is still to come ({remaining:g}).", None
            rows.append({"line_id": ln[0], "po_id": ln[1], "item": ln[2], "qty": qty, "prior": float(ln[4]), "est_price": float(ln[5]), "item_id": ln[9]})
    received = _as_date(received_date) or manila_today()
    ref = " ".join(str(ref or "").split())
    is_invoice = str(doc_type).lower().startswith("inv")
    stored = (ref if is_invoice or ref.upper().startswith("DR") else f"DR {ref}") if ref else None          # a delivery receipt number already written "DR-77" is kept as typed
    with write_tx() as conn:
        from engines.stock_ledger import stock_in
        for r in rows:
            conn.execute("UPDATE purchase_order_lines SET qty_invoiced = COALESCE(qty_invoiced, qty_received), qty_received = ? WHERE line_id = ?", (r["prior"] + r["qty"], r["line_id"]))
            if book_stock:                                       # the parked Django site books its own stock in (book_stock=False), so it is never counted twice
                stock_in(conn, r["item_id"], r["qty"], r["est_price"], "purchase_order_lines", r["line_id"], received, note="received, counted at the order's net price")
        for po_id in sorted({r["po_id"] for r in rows}):
            mine = [r for r in rows if r["po_id"] == po_id]
            est = round(sum(r["qty"] * r["est_price"] for r in mine), 2)
            conn.execute("INSERT INTO po_receipts (po_id, received_date, supplier_invoice_no, actual_amount, est_amount, invoice_id) VALUES (?, ?, ?, ?, ?, NULL)",
                         (po_id, str(received), stored, est, est))
            left = conn.execute("SELECT COUNT(*) FROM purchase_order_lines WHERE po_id = ? AND qty - qty_received > 0.0000001", (po_id,)).fetchone()[0]
            conn.execute("UPDATE purchase_orders SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE po_id = ?", ("PARTIAL" if left else "RECEIVED", po_id))
            po_no = conn.execute("SELECT po_no FROM purchase_orders WHERE po_id = ?", (po_id,)).fetchone()[0]
            conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'PO_GOODS_RECEIVED', 'purchase_orders', ?, ?)",
                         (po_id, f"{po_no}: " + ", ".join(f"{r['qty']:g} x {r['item']}" for r in mine) + (f" ({'supplier invoice' if is_invoice else 'delivery receipt'} {ref})" if ref else "") + ", waiting for the supplier's invoice"))
    from engines.review_flags import flag, sod_flag
    with write_tx() as conn:                                             # controls flag, they do not block: ordering and receiving the same order goes on the Review list
        for po_id in sorted({r["po_id"] for r in rows}):
            made_by, po_no = conn.execute("SELECT created_by, po_no FROM purchase_orders WHERE po_id = ?", (po_id,)).fetchone()
            note = sod_flag("SOD_ORDERED_RECEIVED", made_by, po_no)
            if note:
                flag(conn, "SOD_ORDERED_RECEIVED", "purchase_orders", po_id, note)
    for po_id in sorted({r["po_id"] for r in rows}):
        update_cash_flag(po_id)
    return True, f"Received {sum(r['qty'] for r in rows):g} piece(s) on {len({r['po_id'] for r in rows})} order(s). Payables matches the supplier's invoice when it comes.", None


def invoiceable_lines(creditor_id: int) -> pd.DataFrame:
    """Goods received and not yet covered by a supplier invoice, for this supplier. Columns: po_id, po_no, line_id, item_name, unit, qty_received, qty_invoiced,
    remaining (to invoice), est_unit_price, received_date, received_ref (the DR no. or the supplier invoice no. that came with the goods)."""
    cols = ["po_id", "po_no", "line_id", "item_name", "unit", "qty_received", "qty_invoiced", "remaining", "est_unit_price", "received_date", "received_ref"]
    with get_conn() as conn:
        df = pd.read_sql_query(
            """SELECT p.po_id, p.po_no, l.line_id, l.item_name, l.unit, l.qty_received, COALESCE(l.qty_invoiced, l.qty_received) AS qty_invoiced,
                      l.qty_received - COALESCE(l.qty_invoiced, l.qty_received) AS remaining, l.est_unit_price,
                      (SELECT MAX(r.received_date) FROM po_receipts r WHERE r.po_id = p.po_id) AS received_date,
                      (SELECT r.supplier_invoice_no FROM po_receipts r WHERE r.po_id = p.po_id AND r.invoice_id IS NULL AND r.supplier_invoice_no IS NOT NULL
                        ORDER BY r.receipt_id DESC LIMIT 1) AS received_ref
               FROM purchase_orders p JOIN purchase_order_lines l ON l.po_id = p.po_id
               WHERE p.creditor_id = ? AND p.status IN ('ORDERED', 'PARTIAL', 'RECEIVED') AND l.qty_received - COALESCE(l.qty_invoiced, l.qty_received) > 0.0000001
               ORDER BY p.po_id, l.line_id""", conn, params=(creditor_id,))
    return df[cols] if not df.empty else pd.DataFrame(columns=cols)


def receive_into_batch(creditor_id: int, statement_id: int, doc_ref: str, invoice_date, picks: list[dict], update_prices: bool = False,
                       received_date=None, gross_override: float | None = None, goods_received: bool = False, book_stock: bool = True) -> tuple[bool, str, int | None]:
    """Matches the supplier's invoice to one or more orders and puts it in the batch. picks: [{'line_id', 'qty', 'actual_unit_price'}]. The ACTUAL price
    prevails: the invoice is made at what the supplier charged (qty x actual price, or `gross_override` when the invoice total is typed), the order line keeps both
    prices, and the received quantity leaves the commitments. Everything is checked first; a refusal writes nothing. update_prices: add the actual price to the
    price list (starting on the invoice date) wherever it differs from the estimate. Returns (ok, message, invoice_id).
    goods_received=True (step 2, Payables): the lines were already recorded as received (receive_goods); the invoice covers goods received and not yet invoiced, and the
    order's received quantity is left alone. False: receiving and invoicing in one step, as before."""
    allowed, why = require_permission("po.receive")
    if not allowed:
        return False, why, None
    from engines.statement_rules import add_invoice, delete_invoice

    doc = " ".join(str(doc_ref or "").split())
    inv_date = _as_date(invoice_date)
    if not doc:
        return False, "Enter the supplier's invoice number.", None
    if inv_date is None:
        return False, "Enter the invoice date.", None
    if not picks:
        return False, "Tick at least one delivered line.", None
    with get_conn() as conn:
        batch = conn.execute("SELECT creditor_id, status, is_locked FROM order_statements WHERE statement_id = ?", (statement_id,)).fetchone()
        creditor = conn.execute("SELECT terms FROM creditors WHERE creditor_id = ?", (creditor_id,)).fetchone()
        if not batch or not creditor:
            return False, "Batch or supplier not found.", None
        if int(batch[0]) != int(creditor_id):
            return False, "That batch belongs to another supplier.", None
        if batch[1] == "Cancelled":
            return False, "That batch is cancelled.", None
        if batch[2]:
            return False, "That batch is locked (closed: its checks cover it in full). Put the invoice in a new batch.", None
        if conn.execute("SELECT 1 FROM invoices WHERE creditor_id = ? AND doc_ref = ?", (creditor_id, doc)).fetchone():
            return False, f"Invoice {doc} already exists for this supplier.", None
        rows = []
        for n, pk in enumerate(picks, start=1):
            ln = conn.execute(
                """SELECT l.line_id, l.po_id, l.item_name, l.unit, l.qty, l.qty_received, l.est_unit_price, l.actual_amount, p.status, p.creditor_id, p.po_no,
                          l.list_price, l.discount_chain, l.qty_invoiced, l.item_id
                   FROM purchase_order_lines l JOIN purchase_orders p ON p.po_id = l.po_id WHERE l.line_id = ?""", (int(pk["line_id"]),)).fetchone()
            if not ln:
                return False, f"Line {n}: order line not found.", None
            if int(ln[9]) != int(creditor_id):
                return False, f"Line {n} ({ln[2]}): that order is for another supplier.", None
            if ln[8] not in (LIVE_STATUSES + ("RECEIVED",) if goods_received else LIVE_STATUSES):
                return False, f"Line {n} ({ln[2]}): {ln[10]} has not been sent to the supplier or is closed, so it cannot be received.", None
            try:
                qty, price = float(pk["qty"]), float(pk["actual_unit_price"])
            except (TypeError, ValueError):
                return False, f"Line {n} ({ln[2]}): enter the quantity and the price.", None
            if qty <= 0:
                return False, f"Line {n} ({ln[2]}): the quantity received must be more than zero.", None
            if price < 0:
                return False, f"Line {n} ({ln[2]}): the price cannot be negative.", None
            invoiced_before = float(ln[13]) if ln[13] is not None else float(ln[5])
            remaining = (float(ln[5]) - invoiced_before) if goods_received else (float(ln[4]) - float(ln[5]))
            if qty > remaining + 1e-6:
                return False, (f"Line {n} ({ln[2]}): {qty:g} is more than the received goods still waiting for an invoice ({remaining:g})." if goods_received
                               else f"Line {n} ({ln[2]}): {qty:g} is more than is still to come ({remaining:g})."), None
            rows.append({"line_id": ln[0], "po_id": ln[1], "item": ln[2], "unit": ln[3], "qty": qty, "price": price, "est_price": float(ln[6]),
                         "amount": round(qty * price, 2), "est": round(qty * float(ln[6]), 2), "prior_actual": float(ln[7] or 0.0), "prior_qty": float(ln[5]),
                         "prior_invoiced": invoiced_before, "list_price": ln[11], "chain": ln[12] or "", "item_id": ln[14]})
    lines_total = round(sum(r["amount"] for r in rows), 2)
    gross = lines_total if gross_override is None else round(float(gross_override), 2)
    if gross <= 0:
        return False, "The invoice amount must be more than zero.", None
    scale = gross / lines_total if lines_total else 1.0           # a typed invoice total (freight, rounding) is what each order's receipt records, shared by amount
    due = resolve_due_date(inv_date, creditor[0])
    add_invoice(creditor_id, doc, inv_date, due, gross, [], statement_id)              # the invoice is the payable from here on
    with get_conn() as conn:
        inv_id = conn.execute("SELECT invoice_id FROM invoices WHERE creditor_id = ? AND doc_ref = ?", (creditor_id, doc)).fetchone()[0]
    received = _as_date(received_date) or manila_today()
    try:
        with write_tx() as conn:
            from engines.stock_ledger import correct_cost, stock_in
            for r in rows:
                if not book_stock:
                    pass
                elif goods_received:                                                         # the goods were counted at the order's price: the invoice says what they really cost
                    correct_cost(conn, r["item_id"], r["qty"], r["est_price"], r["price"], "purchase_order_lines", r["line_id"], received, note=f"invoice {doc}")
                else:                                                                        # received and invoiced in one step: counted at the price charged
                    stock_in(conn, r["item_id"], r["qty"], r["price"], "purchase_order_lines", r["line_id"], received, note=f"received on invoice {doc}")
                new_received = r["prior_qty"] + (0 if goods_received else r["qty"])         # step 2 leaves the received quantity alone
                new_invoiced = r["prior_invoiced"] + r["qty"]
                new_actual = round(r["prior_actual"] + r["amount"], 2)
                conn.execute("UPDATE purchase_order_lines SET qty_received = ?, qty_invoiced = ?, actual_amount = ?, actual_unit_price = ? WHERE line_id = ?",
                             (new_received, new_invoiced, new_actual, round(new_actual / new_invoiced, 4), r["line_id"]))
            for po_id in sorted({r["po_id"] for r in rows}):
                mine = [r for r in rows if r["po_id"] == po_id]
                conn.execute(
                    """INSERT INTO po_receipts (po_id, received_date, supplier_invoice_no, supplier_invoice_date, actual_amount, est_amount, invoice_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""", (po_id, str(received), doc, str(inv_date), round(sum(r["amount"] for r in mine) * scale, 2), sum(r["est"] for r in mine), inv_id))
                if not goods_received:
                    left = conn.execute("SELECT COUNT(*) FROM purchase_order_lines WHERE po_id = ? AND qty - qty_received > 0.0000001", (po_id,)).fetchone()[0]
                    conn.execute("UPDATE purchase_orders SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE po_id = ?", ("PARTIAL" if left else "RECEIVED", po_id))
                po_no = conn.execute("SELECT po_no FROM purchase_orders WHERE po_id = ?", (po_id,)).fetchone()[0]
                conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), ?, 'purchase_orders', ?, ?)",
                             ("PO_INVOICED" if goods_received else "PO_RECEIVED", po_id, f"{po_no} {'invoiced' if goods_received else 'received'} on invoice {doc}: actual {sum(r['amount'] for r in mine):,.2f} against estimate {sum(r['est'] for r in mine):,.2f}"))
    except Exception:
        delete_invoice(inv_id)                                                            # nothing half-done: take the invoice back out
        raise
    price_notes = []
    if update_prices:                                                                     # part of receiving: the receiver may update the list from the invoice
        from engines.discount_rules import parse_chain
        for r in rows:
            if abs(r["price"] - r["est_price"]) > 0.005:
                factor = 1.0
                for d in (parse_chain(r["chain"]) or []) if r["list_price"] is not None else []:
                    factor *= 1 - d / 100
                new_list = round(r["price"] / factor, 2) if factor else r["price"]       # the invoice price is NET: the list gets it back before the discount
                ok_p, msg_p = add_price(creditor_id, r["item"], r["unit"] or "", new_list, inv_date, trusted=True)
                price_notes.append(f"{r['item']}: list price now {new_list:,.2f}" if ok_p else msg_p)
    from engines.review_flags import PRICE_OVER_PCT, flag, sod_flag
    with write_tx() as conn:                                             # controls flag, they do not block: duty overlaps and prices far above the order go on the Review list
        for po_id in sorted({r["po_id"] for r in rows}):
            po_no = conn.execute("SELECT po_no FROM purchase_orders WHERE po_id = ?", (po_id,)).fetchone()[0]
            if goods_received:
                got = conn.execute("SELECT user_email FROM audit_logs WHERE entity_name = 'purchase_orders' AND entity_id = ? AND user_action = 'PO_GOODS_RECEIVED' "
                                   "ORDER BY rowid DESC LIMIT 1", (po_id,)).fetchone()
                note = sod_flag("SOD_RECEIVED_MATCHED", got[0] if got else None, f"{po_no}, invoice {doc}")
                if note:
                    flag(conn, "SOD_RECEIVED_MATCHED", "purchase_orders", po_id, note)
        for r in rows:
            if r["est_price"] > 0 and r["price"] > r["est_price"] * (1 + PRICE_OVER_PCT / 100):
                flag(conn, "PRICE_OVER_PO", "invoices", inv_id, f"{r['item']} on invoice {doc}: {r['price']:,.2f} against {r['est_price']:,.2f} on the order "
                                                                  f"(+{(r['price'] / r['est_price'] - 1) * 100:.0f}%)")
    for po_id in sorted({r["po_id"] for r in rows}):
        update_cash_flag(po_id)
    changed = sum(1 for r in rows if abs(r["price"] - r["est_price"]) > 0.005)
    return True, (f"Invoice {doc} for {gross:,.2f} added to the batch from {len({r['po_id'] for r in rows})} order(s)" + (f"; {changed} price(s) differ from the estimate." if changed else ".")
                  + (" Price list: " + "; ".join(price_notes) + "." if price_notes else "")), int(inv_id)


def po_links_for_batch(statement_id: int) -> pd.DataFrame:
    """The batch's invoices that were matched to purchase orders: doc_ref, po_nos, est_amount, actual_amount, difference (actual - estimate), matches (no difference)."""
    cols = ["invoice_id", "doc_ref", "po_nos", "est_amount", "actual_amount", "difference", "matches"]
    with get_conn() as conn:
        df = pd.read_sql_query(
            """SELECT i.invoice_id, i.doc_ref, GROUP_CONCAT(DISTINCT p.po_no) AS po_nos, SUM(r.est_amount) AS est_amount, SUM(r.actual_amount) AS actual_amount
               FROM invoices i JOIN po_receipts r ON r.invoice_id = i.invoice_id JOIN purchase_orders p ON p.po_id = r.po_id
               WHERE i.statement_id = ? GROUP BY i.invoice_id ORDER BY i.invoice_id""", conn, params=(statement_id,))
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["difference"] = (df["actual_amount"] - df["est_amount"]).round(2)
    df["matches"] = df["difference"].abs() < 0.005
    return df[cols]


def price_differences(creditor_id: int | None = None, since=None) -> pd.DataFrame:
    """Received order lines with what was estimated and what was charged: po_no, supplier, item_name, qty_received, est_unit_price, actual_unit_price,
    difference (actual - estimate, times the quantity received), difference_pct. Newest first."""
    where, params = ["l.qty_received > 0", "l.actual_unit_price IS NOT NULL"], []
    if creditor_id:
        where.append("p.creditor_id = ?")
        params.append(creditor_id)
    if since:
        where.append("p.updated_at >= ?")
        params.append(str(_as_date(since)))
    with get_conn() as conn:
        df = pd.read_sql_query(
            f"""SELECT p.po_no, c.creditor_name AS supplier, l.item_name, l.qty_received, l.est_unit_price, l.actual_unit_price
                FROM purchase_order_lines l JOIN purchase_orders p ON p.po_id = l.po_id JOIN creditors c ON c.creditor_id = p.creditor_id
                WHERE {' AND '.join(where)} ORDER BY p.updated_at DESC, l.line_id DESC""", conn, params=params)
    df["difference"] = ((df["actual_unit_price"] - df["est_unit_price"]) * df["qty_received"]).round(2)
    df["difference_pct"] = ((df["actual_unit_price"] / df["est_unit_price"] - 1) * 100).where(df["est_unit_price"] > 0)
    return df
