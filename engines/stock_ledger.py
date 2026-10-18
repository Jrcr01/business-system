"""Stock as a ledger (2026-10-03; FIFO cost since 2026-10-17, see engines/fifo.py): the first step toward the inventory module. Every movement is a row in stock_ledger; items.on_hand and items.avg_cost (the weighted average of what
was actually paid) follow from the rows and are never typed over. Receiving goods writes IN at the order's net price; matching the supplier's invoice corrects the cost to the price
actually charged ("the actual price prevails"). Callers pass their open write_tx() connection, so a movement is saved or refused together with the receipt that caused it."""
from __future__ import annotations

from engines.clock import manila_today


from engines import fifo


def _ledger(conn, item_id, kind, qty, unit_cost, on_hand_after, avg_after, ref_type, ref_id, moved_on, note) -> int:
    return conn.execute("""INSERT INTO stock_ledger (item_id, kind, qty, unit_cost, on_hand_after, avg_after, ref_type, ref_id, moved_on, note, created_by)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, acting_user()) RETURNING move_id""",
                        (int(item_id), kind, float(qty), float(unit_cost), float(on_hand_after), float(avg_after), ref_type, int(ref_id), str(moved_on or manila_today()), note)).fetchone()[0]


def _on_hand(conn, item_id) -> float:
    return float(conn.execute("SELECT COALESCE(on_hand, 0) FROM items WHERE id = ?", (int(item_id),)).fetchone()[0])


def stock_in(conn, item_id: int | None, qty: float, unit_cost: float, ref_type: str, ref_id: int, moved_on=None, note: str = "") -> bool:
    """Pieces arrived at `unit_cost` each: a new FIFO batch dated the day received. A delivery at the order's price waits for the invoice's price.
    A line with no catalog item (a typed, one-off item) has no stock."""
    if not item_id or qty <= 0:
        return False
    fifo.sync(conn, item_id)
    new_on_hand = _on_hand(conn, item_id) + float(qty)
    conn.execute("UPDATE items SET on_hand = ? WHERE id = ?", (new_on_hand, int(item_id)))
    fifo.add(conn, item_id, qty, unit_cost, moved_on, ref_type, ref_id, est=(ref_type == "purchase_order_lines" and "order's net price" in (note or "")))
    avg = fifo.refresh(conn, item_id)
    _ledger(conn, item_id, "IN", qty, unit_cost, new_on_hand, avg, ref_type, ref_id, moved_on, note)
    return True


def correct_cost(conn, item_id: int | None, qty: float, estimated_cost: float, actual_cost: float, ref_type: str, ref_id: int, moved_on=None, note: str = "") -> bool:
    """The invoice came at `actual_cost` where the goods were counted at `estimated_cost`: that delivery's batch takes the invoice price, and so do the
    sales that already took its pieces. A row with qty 0 records it, so the ledger shows the correction and who made it."""
    if not item_id or qty <= 0 or abs(float(actual_cost) - float(estimated_cost)) < 0.00005:
        return False
    fifo.sync(conn, item_id)
    if not fifo.correct(conn, item_id, actual_cost, ref_type, ref_id):
        return False
    avg = fifo.refresh(conn, item_id)
    _ledger(conn, item_id, "COST", 0, actual_cost, _on_hand(conn, item_id), avg, ref_type, ref_id, moved_on,
            note or f"cost corrected from {estimated_cost:,.2f} to {actual_cost:,.2f}")
    return True


def stock_back(conn, item_id: int | None, qty: float, unit_cost: float, ref_type: str, ref_id: int, moved_on=None, note: str = "") -> bool:
    """A delivery recorded by mistake is undone (owner, 2026-10-11): its batch loses the pieces; pieces of it already sold move to the next batches.
    An ADJ row with a negative quantity records it."""
    if not item_id or qty <= 0:
        return False
    fifo.sync(conn, item_id)
    new_on_hand = _on_hand(conn, item_id) - float(qty)
    gone = fifo.remove_ref(conn, item_id, qty, ref_type, ref_id)
    conn.execute("UPDATE items SET on_hand = ? WHERE id = ?", (new_on_hand, int(item_id)))
    fifo.sync(conn, item_id)                                     # a delivery from before FIFO had no batch to remove: the oldest pieces leave instead
    avg = fifo.refresh(conn, item_id)
    _ledger(conn, item_id, "ADJ", -float(qty), (gone / float(qty)) if gone else unit_cost, new_on_hand, avg, ref_type, ref_id, moved_on, note)
    return True


def _flag_negative(conn, item_id: int, on_hand: float, what: str) -> None:
    """Stock below zero is allowed (a sale is never refused) and goes on the Review list: something was not received, or not counted."""
    if on_hand >= -1e-9:
        return
    from engines.settings_registry import stock_not_counted
    if stock_not_counted():                                     # before the opening count: not a warning yet (owner, 2026-10-18)
        return
    from engines.review_flags import flag
    name = conn.execute("SELECT name FROM items WHERE id = ?", (int(item_id),)).fetchone()[0]
    flag(conn, "STOCK_NEGATIVE", "items", int(item_id), f"{name}: on hand reads {on_hand:g} after {what}. Count it, or receive what came.")


def stock_out(conn, item_id: int | None, qty: float, ref_type: str, ref_id: int, moved_on=None, note: str = "") -> float:
    """Pieces left (a sale, a lend): taken from the oldest batches first. Never refused -- below zero is flagged, and those pieces take the last price
    paid until their delivery is recorded. Returns the cost each piece left at (the average over the batches it took)."""
    if not item_id or qty <= 0:
        return 0.0
    fifo.sync(conn, item_id)
    new_on_hand = _on_hand(conn, item_id) - float(qty)
    total, uses = fifo.take(conn, item_id, qty, ref_type, ref_id, moved_on)
    conn.execute("UPDATE items SET on_hand = ?, oldest_dot = CASE WHEN ? <= 0 THEN NULL ELSE oldest_dot END WHERE id = ?", (new_on_hand, new_on_hand, int(item_id)))
    avg = fifo.refresh(conn, item_id)
    each = round(total / float(qty), 4)
    move_id = _ledger(conn, item_id, "OUT", -float(qty), each, new_on_hand, avg, ref_type, ref_id, moved_on, note)
    fifo.tag_move(conn, uses, move_id)
    _flag_negative(conn, int(item_id), new_on_hand, note or "a movement")
    return each


def stock_return(conn, item_id: int | None, qty: float, unit_cost: float, ref_type: str, ref_id: int, moved_on=None, note: str = "") -> bool:
    """Pieces came back (a customer's return, a void, an affiliate returning what was lent) at the cost they left at, placed first so they sell next."""
    if not item_id or qty <= 0:
        return False
    fifo.sync(conn, item_id)
    new_on_hand = _on_hand(conn, item_id) + float(qty)
    conn.execute("UPDATE items SET on_hand = ? WHERE id = ?", (new_on_hand, int(item_id)))
    fifo.add(conn, item_id, qty, unit_cost, moved_on, ref_type, ref_id, prio=0, source="returned")
    avg = fifo.refresh(conn, item_id)
    _ledger(conn, item_id, "RETURN", qty, unit_cost, new_on_hand, avg, ref_type, ref_id, moved_on, note)
    return True


def stock_set(conn, item_id: int, counted: float, ref_type: str, ref_id: int, moved_on=None, note: str = "") -> float:
    """What is really on hand, from a count or an adjustment: an ADJ row for the difference. Fewer: the missing pieces leave from the oldest batch.
    More: a batch for the extra pieces -- at the item's cost when it has no delivery yet (the cost typed at the opening count), else at the last
    price paid. Returns the difference."""
    fifo.sync(conn, item_id)
    on_hand = _on_hand(conn, item_id)
    diff = float(counted) - on_hand
    if abs(diff) < 1e-9:
        return 0.0
    conn.execute("UPDATE items SET on_hand = ? WHERE id = ?", (float(counted), int(item_id)))
    uses = []
    if diff > 0:
        has = conn.execute("SELECT 1 FROM stock_layers WHERE item_id = ? AND prio = 1 LIMIT 1", (int(item_id),)).fetchone()
        avg_now = float(conn.execute("SELECT COALESCE(avg_cost, 0) FROM items WHERE id = ?", (int(item_id),)).fetchone()[0])
        each = (fifo.last_cost(conn, item_id) if has else avg_now) or avg_now
        fifo.add(conn, item_id, diff, each, moved_on, ref_type, ref_id, source="found at a count" if has else "opening count")
    else:
        total, uses = fifo.take(conn, item_id, -diff, ref_type, ref_id, moved_on)
        each = round(total / -diff, 4)
    avg = fifo.refresh(conn, item_id)
    move_id = _ledger(conn, item_id, "ADJ", diff, each, float(counted), avg, ref_type, ref_id, moved_on, note)
    fifo.tag_move(conn, uses, move_id)
    return diff


def moves(item_id: int):
    import pandas as pd
    from engines.common import get_conn
    with get_conn() as conn:
        return pd.read_sql_query("SELECT move_id, kind, qty, unit_cost, on_hand_after, avg_after, ref_type, ref_id, moved_on, note, created_by FROM stock_ledger WHERE item_id = ? ORDER BY move_id",
                                 conn, params=(int(item_id),))
