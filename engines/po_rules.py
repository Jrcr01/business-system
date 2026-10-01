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
from engines.price_list_service import price_on
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
        unit, price_id, price = str(ln.get("unit") or "").strip(), None, ln.get("est_unit_price")
        if price is None or price == "":
            listed = price_on(creditor_id, name, order_date)              # not typed: use the price in force on the order date, if the supplier has one
            if listed:
                price, price_id = listed["unit_price"], listed["price_id"]
                unit = unit or listed["unit"]
                name = listed["item_name"]                                # keep the list's spelling so the item is one item everywhere
            else:
                price = 0.0
        price = float(price)
        if price < 0:
            return None, f"Line {n} ({name}): the price cannot be negative."
        clean.append({"price_id": price_id, "item_name": name, "unit": unit or None, "qty": qty, "est_unit_price": price, "est_amount": round(qty * price, 2)})
    if not clean:
        return None, "Add at least one item."
    return clean, ""


def _has_receipt(conn, po_id: int) -> bool:
    return bool(conn.execute("SELECT 1 FROM po_receipts WHERE po_id = ? LIMIT 1", (po_id,)).fetchone())


def save_po(po_id: int | None, creditor_id: int, order_date, expected_delivery, lines: list[dict], notes: str = "") -> tuple[bool, str, int | None]:
    """Creates a draft order, or changes an open one (Draft / Ordered with nothing received yet): the lines are replaced and the estimate and pay date
    worked out again. Each line: item_name, qty, optional unit and est_unit_price (left out = the supplier's list price on the order date)."""
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
            conn.execute("INSERT INTO purchase_order_lines (po_id, price_id, item_name, unit, qty, est_unit_price, est_amount) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (po_id, ln["price_id"], ln["item_name"], ln["unit"], ln["qty"], ln["est_unit_price"], ln["est_amount"]))
        total = sum(ln["est_amount"] for ln in clean)
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), ?, 'purchase_orders', ?, ?)",
                     (action, po_id, f"{len(clean)} line(s), estimated {total:,.2f}, pay date {pay}"))
    update_cash_flag(int(po_id))
    return True, "Order saved.", int(po_id)


def order_po(po_id: int) -> tuple[bool, str]:
    """Marks a draft as ordered and gives it its permanent number 'YYYY - PO NNNN' (the year of the order date). Already ordered: nothing changes."""
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
        year = int(str(order_date)[:4])
        prefix = f"{year} - PO "
        used = [int(r[0][len(prefix):]) for r in conn.execute("SELECT po_no FROM purchase_orders WHERE po_no LIKE ?", (prefix + "%",)) if r[0][len(prefix):].strip().isdigit()]
        number = f"{prefix}{next_doc_number(conn, 'PO', year, max(used, default=0)):04d}"
        conn.execute("UPDATE purchase_orders SET po_no = ?, status = 'ORDERED', updated_at = CURRENT_TIMESTAMP WHERE po_id = ?", (number, po_id))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'PO_ORDERED', 'purchase_orders', ?, ?)",
                     (po_id, f"{number}, estimated {total:,.2f}"))
    update_cash_flag(po_id)
    return True, f"Ordered as {number}."


def cancel_po(po_id: int, reason: str = "") -> tuple[bool, str]:
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
    """What ordered purchase orders still owe, by pay date (inside start..end; a pay date already past counts on `start`). The unreceived part only:
    each line's estimate times the share of its quantity not yet received. Columns: po_id, po_no, supplier, day, amount."""
    cols = ["po_id", "po_no", "supplier", "day", "amount"]
    with get_conn() as conn:
        df = pd.read_sql_query(
            """SELECT p.po_id, p.po_no, c.creditor_name AS supplier, p.expected_pay_date,
                      SUM(l.est_amount * MAX(l.qty - l.qty_received, 0) / l.qty) AS amount
               FROM purchase_orders p JOIN creditors c ON c.creditor_id = p.creditor_id JOIN purchase_order_lines l ON l.po_id = p.po_id
               WHERE p.status IN ('ORDERED', 'PARTIAL') AND p.expected_pay_date IS NOT NULL GROUP BY p.po_id""", conn)
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
    from engines.cash_insights_service import cash_position, forecast, forecast_fresh_days

    pos = cash_position()
    if pos is None:
        return None
    age = (today - pos["as_of"]).days
    if age > forecast_fresh_days():
        return {"stale": age}
    return forecast(min(max(horizon_days, 1), 900), today) or None


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
        return {"known": False, "ok": None, "pay_date": pay,
                "message": f"The bank balance is {age} days old, so the cash cannot be checked. Import a newer statement." if age else "No bank balance on file, so the cash cannot be checked."}
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
    with get_conn() as conn:
        return pd.read_sql_query(
            f"""SELECT p.po_id, p.po_no, c.creditor_name AS supplier, p.expected_pay_date, p.cash_note, p.cash_ack_at, p.created_by,
                       COALESCE((SELECT SUM(est_amount) FROM purchase_order_lines l WHERE l.po_id = p.po_id), 0) AS total
                FROM purchase_orders p JOIN creditors c ON c.creditor_id = p.creditor_id
                WHERE p.over_cash_plan = 1 AND p.status IN ('ORDERED', 'PARTIAL') {'' if include_acknowledged else 'AND p.cash_ack_at IS NULL'}
                ORDER BY p.expected_pay_date""", conn)


def acknowledge_over_plan(po_id: int) -> tuple[bool, str]:
    with write_tx() as conn:
        row = conn.execute("SELECT po_no FROM purchase_orders WHERE po_id = ? AND over_cash_plan = 1", (po_id,)).fetchone()
        if not row:
            return False, "This order is not over the cash plan."
        conn.execute("UPDATE purchase_orders SET cash_ack_at = CURRENT_TIMESTAMP WHERE po_id = ?", (po_id,))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'PO_OVER_PLAN_SEEN', 'purchase_orders', ?, ?)",
                     (po_id, f"{row[0]} noted by the owner"))
    return True, f"{row[0]} noted."
