"""Stock as a ledger (2026-10-03): the first step toward the inventory module. Every movement is a row in stock_ledger; items.on_hand and items.avg_cost (the weighted average of what
was actually paid) follow from the rows and are never typed over. Receiving goods writes IN at the order's net price; matching the supplier's invoice corrects the cost to the price
actually charged ("the actual price prevails"). Callers pass their open write_tx() connection, so a movement is saved or refused together with the receipt that caused it."""
from __future__ import annotations

from engines.clock import manila_today


def stock_in(conn, item_id: int | None, qty: float, unit_cost: float, ref_type: str, ref_id: int, moved_on=None, note: str = "") -> bool:
    """Pieces arrived at `unit_cost` each: the average becomes (on hand x average + qty x cost) / (on hand + qty). A line with no catalog item (a typed, one-off item) has no stock."""
    if not item_id or qty <= 0:
        return False
    on_hand, avg = conn.execute("SELECT on_hand, avg_cost FROM items WHERE id = ?", (int(item_id),)).fetchone()
    on_hand, avg = float(on_hand or 0), float(avg or 0)
    new_on_hand = on_hand + float(qty)
    new_avg = round((max(on_hand, 0) * avg + float(qty) * float(unit_cost)) / (max(on_hand, 0) + float(qty)), 4)
    conn.execute("UPDATE items SET on_hand = ?, avg_cost = ? WHERE id = ?", (new_on_hand, new_avg, int(item_id)))
    conn.execute("INSERT INTO stock_ledger (item_id, kind, qty, unit_cost, on_hand_after, avg_after, ref_type, ref_id, moved_on, note, created_by) VALUES (?, 'IN', ?, ?, ?, ?, ?, ?, ?, ?, acting_user())",
                 (int(item_id), float(qty), float(unit_cost), new_on_hand, new_avg, ref_type, int(ref_id), str(moved_on or manila_today()), note))
    return True


def correct_cost(conn, item_id: int | None, qty: float, estimated_cost: float, actual_cost: float, ref_type: str, ref_id: int, moved_on=None, note: str = "") -> bool:
    """The invoice came at `actual_cost` where the goods were counted at `estimated_cost`: the average moves by the difference on those pieces (spread over what is on hand now).
    A row with qty 0 records it, so the ledger shows the correction and who made it."""
    if not item_id or qty <= 0 or abs(float(actual_cost) - float(estimated_cost)) < 0.00005:
        return False
    on_hand, avg = conn.execute("SELECT on_hand, avg_cost FROM items WHERE id = ?", (int(item_id),)).fetchone()
    on_hand, avg = float(on_hand or 0), float(avg or 0)
    if on_hand <= 0:
        return False
    new_avg = round(max(avg + (float(actual_cost) - float(estimated_cost)) * min(float(qty), on_hand) / on_hand, 0), 4)
    conn.execute("UPDATE items SET avg_cost = ? WHERE id = ?", (new_avg, int(item_id)))
    conn.execute("INSERT INTO stock_ledger (item_id, kind, qty, unit_cost, on_hand_after, avg_after, ref_type, ref_id, moved_on, note, created_by) VALUES (?, 'COST', 0, ?, ?, ?, ?, ?, ?, ?, acting_user())",
                 (int(item_id), float(actual_cost), on_hand, new_avg, ref_type, int(ref_id), str(moved_on or manila_today()), note or f"cost corrected from {estimated_cost:,.2f} to {actual_cost:,.2f}"))
    return True


def moves(item_id: int):
    import pandas as pd
    from engines.common import get_conn
    with get_conn() as conn:
        return pd.read_sql_query("SELECT move_id, kind, qty, unit_cost, on_hand_after, avg_after, ref_type, ref_id, moved_on, note, created_by FROM stock_ledger WHERE item_id = ? ORDER BY move_id",
                                 conn, params=(int(item_id),))
