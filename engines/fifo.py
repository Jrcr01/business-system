"""Cost by FIFO, by delivery date (owner, 2026-10-17: "Yes by delivery date"; mock-up docs/mockups/al_fifo_cost.html, answers 1 A, 2 A).

Every delivery (and the opening count) is a batch in stock_layers: the pieces that came in, how many are left, the cost each. A sale, a lend or a count
shortage takes its pieces from the oldest batch first; each piece taken is a row in stock_layer_uses, so the cost of a sale is exactly what its pieces
cost. A piece sold before its delivery is recorded (stock below zero) is a "pending" use at the last price paid; when the delivery comes, the use takes
that delivery's cost and the sale is corrected (answer 1 A). Returns go back at the cost they left at, placed first, so they sell next.

items.avg_cost stays the average of what is left (value / pieces), so every stock value in the system is right; items.next_cost is the cost the next
sale will take (the oldest batch's), shown on the Items list (answer 2 A). Only engines/stock_ledger.py calls in here, inside its write_tx()."""
from __future__ import annotations

from engines.clock import manila_today

EPS = 1e-9


def _open(conn, item_id: int):
    return conn.execute("""SELECT layer_id, qty_left, unit_cost FROM stock_layers WHERE item_id = ? AND qty_left > ?
                           ORDER BY prio, received_on, layer_id""", (int(item_id), EPS)).fetchall()


def last_cost(conn, item_id: int) -> float:
    """The last price paid: the newest batch's cost, else the item's average."""
    row = conn.execute("SELECT unit_cost FROM stock_layers WHERE item_id = ? AND prio = 1 ORDER BY received_on DESC, layer_id DESC LIMIT 1", (int(item_id),)).fetchone()
    if row and float(row[0]) > 0:
        return float(row[0])
    row = conn.execute("SELECT COALESCE(avg_cost, 0), COALESCE(next_cost, 0) FROM items WHERE id = ?", (int(item_id),)).fetchone()
    return float(row[0] or row[1] or 0) if row else 0.0


def refresh(conn, item_id: int) -> float:
    """items.avg_cost = the average of what is left (kept as it was when nothing is left); items.next_cost = the oldest batch's cost. Returns avg_cost."""
    left, value = conn.execute("SELECT COALESCE(SUM(qty_left), 0), COALESCE(SUM(qty_left * unit_cost), 0) FROM stock_layers WHERE item_id = ? AND qty_left > ?",
                               (int(item_id), EPS)).fetchone()
    first = _open(conn, item_id)
    nxt = float(first[0][2]) if first else last_cost(conn, item_id)
    if float(left) > EPS:
        avg = round(float(value) / float(left), 4)
        conn.execute("UPDATE items SET avg_cost = ?, next_cost = ? WHERE id = ?", (avg, round(nxt, 4), int(item_id)))
        return avg
    conn.execute("UPDATE items SET next_cost = ? WHERE id = ?", (round(nxt, 4), int(item_id)))
    return float(conn.execute("SELECT COALESCE(avg_cost, 0) FROM items WHERE id = ?", (int(item_id),)).fetchone()[0])


def recost(conn, ref_type: str | None, ref_id, item_id: int) -> None:
    """A document's cost follows the pieces it took: its stock_ledger rows, and for a sale its lines and cost_total."""
    if not ref_type or ref_id is None:
        return
    rows = conn.execute("""SELECT move_id, SUM(qty), SUM(qty * unit_cost) FROM stock_layer_uses WHERE ref_type = ? AND ref_id = ? AND item_id = ?
                           GROUP BY move_id""", (ref_type, int(ref_id), int(item_id))).fetchall()
    if not rows:
        return
    q = sum(float(r[1]) for r in rows)
    c = sum(float(r[2]) for r in rows)
    for move_id, mq, mc in rows:
        if move_id is not None and float(mq) > EPS:
            conn.execute("UPDATE stock_ledger SET unit_cost = ? WHERE move_id = ?", (round(float(mc) / float(mq), 4), int(move_id)))
    if ref_type == "sales" and q > EPS:
        unit = round(c / q, 4)
        conn.execute("UPDATE sale_lines SET unit_cost = ? WHERE sale_id = ? AND item_id = ?", (unit, int(ref_id), int(item_id)))
        conn.execute("""UPDATE sales SET cost_total = (SELECT ROUND(COALESCE(SUM(qty * unit_cost), 0), 2) FROM sale_lines WHERE sale_id = ? AND item_id IS NOT NULL)
                        WHERE sale_id = ? AND COALESCE(no_stock_out, 0) = 0""", (int(ref_id), int(ref_id)))


def add(conn, item_id: int, qty: float, cost: float, day=None, ref_type=None, ref_id=None, est: bool = False, prio: int = 1, source: str = "") -> int:
    """A batch comes in. Pieces already sold before it came (pending uses) take it first, at its cost, and their sales are corrected."""
    qty, cost = float(qty), float(cost)
    lid = conn.execute("""INSERT INTO stock_layers (item_id, received_on, prio, qty_in, qty_left, unit_cost, est, ref_type, ref_id, source)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING layer_id""",
                       (int(item_id), str(day or manila_today()), int(prio), qty, qty, cost, 1 if est else 0, ref_type, ref_id, source or None)).fetchone()[0]
    room, touched = qty, set()
    for use_id, uq, rt, rid in conn.execute("SELECT use_id, qty, ref_type, ref_id FROM stock_layer_uses WHERE item_id = ? AND layer_id IS NULL ORDER BY use_id",
                                            (int(item_id),)).fetchall():
        if room <= EPS:
            break
        m = min(float(uq), room)
        if m < float(uq) - EPS:                               # part of this use is still waiting for stock: split it
            conn.execute("UPDATE stock_layer_uses SET qty = ? WHERE use_id = ?", (float(uq) - m, use_id))
            conn.execute("""INSERT INTO stock_layer_uses (item_id, layer_id, qty, unit_cost, ref_type, ref_id, move_id, used_on)
                            SELECT item_id, ?, ?, ?, ref_type, ref_id, move_id, used_on FROM stock_layer_uses WHERE use_id = ?""", (lid, m, cost, use_id))
        else:
            conn.execute("UPDATE stock_layer_uses SET layer_id = ?, unit_cost = ? WHERE use_id = ?", (lid, cost, use_id))
        room -= m
        touched.add((rt, rid))
    conn.execute("UPDATE stock_layers SET qty_left = ? WHERE layer_id = ?", (max(room, 0.0), lid))
    for rt, rid in touched:
        recost(conn, rt, rid, item_id)
    return int(lid)


def take(conn, item_id: int, qty: float, ref_type=None, ref_id=None, day=None) -> tuple[float, list[int]]:
    """Pieces leave from the oldest batch first. Returns (cost of all the pieces, the use rows made). What no batch covers is pending at the last price."""
    need, total, uses = float(qty), 0.0, []
    for lid, left, cost in _open(conn, item_id):
        if need <= EPS:
            break
        m = min(float(left), need)
        conn.execute("UPDATE stock_layers SET qty_left = qty_left - ? WHERE layer_id = ?", (m, lid))
        uses.append(conn.execute("""INSERT INTO stock_layer_uses (item_id, layer_id, qty, unit_cost, ref_type, ref_id, used_on) VALUES (?, ?, ?, ?, ?, ?, ?)
                                    RETURNING use_id""", (int(item_id), lid, m, float(cost), ref_type, ref_id, str(day or manila_today()))).fetchone()[0])
        total += m * float(cost)
        need -= m
    if need > EPS:
        cost = last_cost(conn, item_id)
        uses.append(conn.execute("""INSERT INTO stock_layer_uses (item_id, layer_id, qty, unit_cost, ref_type, ref_id, used_on) VALUES (?, NULL, ?, ?, ?, ?, ?)
                                    RETURNING use_id""", (int(item_id), need, cost, ref_type, ref_id, str(day or manila_today()))).fetchone()[0])
        total += need * cost
    return total, uses


def tag_move(conn, uses: list[int], move_id: int) -> None:
    if uses:
        conn.execute(f"UPDATE stock_layer_uses SET move_id = ? WHERE use_id IN ({','.join('?' for _ in uses)})", [int(move_id), *[int(u) for u in uses]])


def sync(conn, item_id: int) -> None:
    """The batches agree with items.on_hand before every movement. An item with stock but no batches yet (stock from before FIFO) gets one batch at its
    average cost; any other gap (a direct correction elsewhere) is closed the same way, or taken from the oldest batch."""
    row = conn.execute("SELECT COALESCE(on_hand, 0), COALESCE(avg_cost, 0) FROM items WHERE id = ?", (int(item_id),)).fetchone()
    if not row:
        return
    on_hand, avg = float(row[0]), float(row[1])
    left = float(conn.execute("SELECT COALESCE(SUM(qty_left), 0) FROM stock_layers WHERE item_id = ?", (int(item_id),)).fetchone()[0])
    pending = float(conn.execute("SELECT COALESCE(SUM(qty), 0) FROM stock_layer_uses WHERE item_id = ? AND layer_id IS NULL", (int(item_id),)).fetchone()[0])
    gap = on_hand - (left - pending)
    if gap > 1e-6:
        before = conn.execute("SELECT 1 FROM stock_layers WHERE item_id = ? LIMIT 1", (int(item_id),)).fetchone()
        add(conn, item_id, gap, avg or last_cost(conn, item_id), ref_type="sync", source="stock on hand before FIFO" if not before else "brought in line with on hand")
    elif gap < -1e-6:
        take(conn, item_id, -gap, "sync", None)


def remove_ref(conn, item_id: int, qty: float, ref_type: str, ref_id: int) -> float:
    """A delivery undone: its batch loses the pieces. Pieces of it already sold move to the next batches, and their sales' cost follows.
    Returns the cost of the pieces removed (at the batch's cost)."""
    need, gone = float(qty), 0.0
    layers = conn.execute("SELECT layer_id, qty_in, qty_left, unit_cost FROM stock_layers WHERE item_id = ? AND ref_type = ? AND ref_id = ? ORDER BY layer_id DESC",
                          (int(item_id), ref_type, int(ref_id))).fetchall()
    for lid, qin, left, cost in layers:
        if need <= EPS:
            break
        m = min(float(left), need)
        if m > EPS:
            conn.execute("UPDATE stock_layers SET qty_in = qty_in - ?, qty_left = qty_left - ? WHERE layer_id = ?", (m, m, lid))
            need -= m
            gone += m * float(cost)
    for lid, qin, left, cost in layers:                          # some were sold already: those sales take their pieces from other batches
        if need <= EPS:
            break
        for use_id, uq, rt, rid, move_id, day in conn.execute("SELECT use_id, qty, ref_type, ref_id, move_id, used_on FROM stock_layer_uses WHERE layer_id = ? ORDER BY use_id DESC",
                                                             (lid,)).fetchall():
            if need <= EPS:
                break
            m = min(float(uq), need)
            if m < float(uq) - EPS:
                conn.execute("UPDATE stock_layer_uses SET qty = qty - ? WHERE use_id = ?", (m, use_id))
            else:
                conn.execute("DELETE FROM stock_layer_uses WHERE use_id = ?", (use_id,))
            conn.execute("UPDATE stock_layers SET qty_in = qty_in - ? WHERE layer_id = ?", (m, lid))
            gone += m * float(cost)
            _, new = take(conn, item_id, m, rt, rid, day)
            if move_id is not None:
                tag_move(conn, new, move_id)
            recost(conn, rt, rid, item_id)
            need -= m
    conn.execute("DELETE FROM stock_layers WHERE item_id = ? AND qty_in <= ? AND NOT EXISTS (SELECT 1 FROM stock_layer_uses u WHERE u.layer_id = stock_layers.layer_id)",
                 (int(item_id), EPS))
    return gone


def correct(conn, item_id: int, actual_cost: float, ref_type: str, ref_id: int) -> bool:
    """The supplier's invoice price prevails: the delivery's batch (still at the order's price) takes it, and so do the sales that took its pieces."""
    rows = conn.execute("SELECT layer_id FROM stock_layers WHERE item_id = ? AND ref_type = ? AND ref_id = ? AND est = 1 ORDER BY layer_id",
                        (int(item_id), ref_type, int(ref_id))).fetchall()
    if not rows:
        return False
    for (lid,) in rows:
        conn.execute("UPDATE stock_layers SET unit_cost = ?, est = 0 WHERE layer_id = ?", (float(actual_cost), lid))
        conn.execute("UPDATE stock_layer_uses SET unit_cost = ? WHERE layer_id = ?", (float(actual_cost), lid))
        for rt, rid in conn.execute("SELECT DISTINCT ref_type, ref_id FROM stock_layer_uses WHERE layer_id = ?", (lid,)).fetchall():
            recost(conn, rt, rid, item_id)
    return True


def reprice(conn, item_id: int, cost: float, ref_type: str, ref_id: int, only_zero: bool = False) -> int:
    """A batch's cost set after it came in (a cost typed after the count): the batch, its uses and their documents follow. Returns batches changed."""
    rows = conn.execute("SELECT layer_id FROM stock_layers WHERE item_id = ? AND ref_type = ? AND ref_id = ?" + (" AND unit_cost <= 0" if only_zero else ""),
                        (int(item_id), ref_type, int(ref_id))).fetchall()
    for (lid,) in rows:
        conn.execute("UPDATE stock_layers SET unit_cost = ? WHERE layer_id = ?", (float(cost), lid))
        conn.execute("UPDATE stock_layer_uses SET unit_cost = ? WHERE layer_id = ?", (float(cost), lid))
        for rt, rid in conn.execute("SELECT DISTINCT ref_type, ref_id FROM stock_layer_uses WHERE layer_id = ?", (lid,)).fetchall():
            recost(conn, rt, rid, item_id)
    return len(rows)


def batches(item_id: int) -> list[dict]:
    """The item card's box: every batch, oldest first (the order the pieces leave in), with where it came from."""
    from engines.common import get_conn
    with get_conn() as conn:
        rows = conn.execute("""SELECT l.layer_id, l.received_on, l.prio, l.qty_in, l.qty_left, l.unit_cost, l.est, l.ref_type, l.ref_id, l.source,
                                      po.po_no, c.creditor_name, sc.count_no
                               FROM stock_layers l
                               LEFT JOIN purchase_order_lines pl ON l.ref_type = 'purchase_order_lines' AND pl.line_id = l.ref_id
                               LEFT JOIN purchase_orders po ON po.po_id = pl.po_id
                               LEFT JOIN creditors c ON c.creditor_id = po.creditor_id
                               LEFT JOIN stock_counts sc ON l.ref_type = 'stock_counts' AND sc.count_id = l.ref_id
                               WHERE l.item_id = ? AND l.qty_in > ? ORDER BY l.prio, l.received_on, l.layer_id""", (int(item_id), EPS)).fetchall()
    out, first = [], True
    for lid, day, prio, qin, left, cost, est, rt, rid, source, po, supplier, count_no in rows:
        if rt == "purchase_order_lines":
            frm, doc = supplier or "Supplier", po or ""
        elif rt == "stock_counts":
            frm, doc = "Stock count", count_no or ""
        elif rt == "sales":
            frm, doc = "Returned by a customer", ""
        elif rt == "take_in":
            frm, doc = "Taken in", ""
        elif rt == "affiliate_moves":
            frm, doc = "Affiliate", ""
        else:
            frm, doc = source or "Stock on hand", ""
        frm = frm[:1].upper() + frm[1:]
        live = float(left) > EPS
        out.append({"id": lid, "day": day, "from": frm, "doc": doc, "came": float(qin), "left": float(left), "cost": float(cost),
                    "value": round(float(left) * float(cost), 2), "est": bool(est), "returned": prio == 0, "next": live and first, "used_up": not live})
        if live:
            first = False
    return out
