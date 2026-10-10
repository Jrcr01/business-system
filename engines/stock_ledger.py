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


def stock_back(conn, item_id: int | None, qty: float, unit_cost: float, ref_type: str, ref_id: int, moved_on=None, note: str = "") -> bool:
    """A delivery recorded by mistake is undone (owner, 2026-10-11): the pieces leave again and the average goes back to what it was without them, as far as
    the pieces on hand allow. An ADJ row with a negative quantity records it."""
    if not item_id or qty <= 0:
        return False
    on_hand, avg = conn.execute("SELECT on_hand, avg_cost FROM items WHERE id = ?", (int(item_id),)).fetchone()
    on_hand, avg = float(on_hand or 0), float(avg or 0)
    new_on_hand = on_hand - float(qty)
    new_avg = round(max((on_hand * avg - float(qty) * float(unit_cost)) / new_on_hand, 0), 4) if new_on_hand > 0 and on_hand > 0 else avg
    conn.execute("UPDATE items SET on_hand = ?, avg_cost = ? WHERE id = ?", (new_on_hand, new_avg, int(item_id)))
    conn.execute("INSERT INTO stock_ledger (item_id, kind, qty, unit_cost, on_hand_after, avg_after, ref_type, ref_id, moved_on, note, created_by) VALUES (?, 'ADJ', ?, ?, ?, ?, ?, ?, ?, ?, acting_user())",
                 (int(item_id), -float(qty), float(unit_cost), new_on_hand, new_avg, ref_type, int(ref_id), str(moved_on or manila_today()), note))
    return True


def _flag_negative(conn, item_id: int, on_hand: float, what: str) -> None:
    """Stock below zero is allowed (a sale is never refused) and goes on the Review list: something was not received, or not counted."""
    if on_hand >= -1e-9:
        return
    from engines.review_flags import flag
    name = conn.execute("SELECT name FROM items WHERE id = ?", (int(item_id),)).fetchone()[0]
    flag(conn, "STOCK_NEGATIVE", "items", int(item_id), f"{name}: on hand reads {on_hand:g} after {what}. Count it, or receive what came.")


def stock_out(conn, item_id: int | None, qty: float, ref_type: str, ref_id: int, moved_on=None, note: str = "") -> float:
    """Pieces left (a sale, a lend): on hand goes down, the average cost stays. Never refused -- below zero is flagged. Returns the cost each piece left at."""
    if not item_id or qty <= 0:
        return 0.0
    on_hand, avg = conn.execute("SELECT on_hand, avg_cost FROM items WHERE id = ?", (int(item_id),)).fetchone()
    on_hand, avg = float(on_hand or 0), float(avg or 0)
    new_on_hand = on_hand - float(qty)
    conn.execute("UPDATE items SET on_hand = ?, oldest_dot = CASE WHEN ? <= 0 THEN NULL ELSE oldest_dot END WHERE id = ?", (new_on_hand, new_on_hand, int(item_id)))
    conn.execute("INSERT INTO stock_ledger (item_id, kind, qty, unit_cost, on_hand_after, avg_after, ref_type, ref_id, moved_on, note, created_by) VALUES (?, 'OUT', ?, ?, ?, ?, ?, ?, ?, ?, acting_user())",
                 (int(item_id), -float(qty), avg, new_on_hand, avg, ref_type, int(ref_id), str(moved_on or manila_today()), note))
    _flag_negative(conn, int(item_id), new_on_hand, note or "a movement")
    return avg


def stock_return(conn, item_id: int | None, qty: float, unit_cost: float, ref_type: str, ref_id: int, moved_on=None, note: str = "") -> bool:
    """Pieces came back (a customer's return, a void, an affiliate returning what was lent) at the cost they left at."""
    if not item_id or qty <= 0:
        return False
    on_hand, avg = conn.execute("SELECT on_hand, avg_cost FROM items WHERE id = ?", (int(item_id),)).fetchone()
    on_hand, avg = float(on_hand or 0), float(avg or 0)
    new_on_hand = on_hand + float(qty)
    new_avg = round((max(on_hand, 0) * avg + float(qty) * float(unit_cost)) / (max(on_hand, 0) + float(qty)), 4) if max(on_hand, 0) + float(qty) > 0 else avg
    conn.execute("UPDATE items SET on_hand = ?, avg_cost = ? WHERE id = ?", (new_on_hand, new_avg, int(item_id)))
    conn.execute("INSERT INTO stock_ledger (item_id, kind, qty, unit_cost, on_hand_after, avg_after, ref_type, ref_id, moved_on, note, created_by) VALUES (?, 'RETURN', ?, ?, ?, ?, ?, ?, ?, ?, acting_user())",
                 (int(item_id), float(qty), float(unit_cost), new_on_hand, new_avg, ref_type, int(ref_id), str(moved_on or manila_today()), note))
    return True


def stock_set(conn, item_id: int, counted: float, ref_type: str, ref_id: int, moved_on=None, note: str = "") -> float:
    """What is really on hand, from a count or an adjustment: an ADJ row for the difference (the average cost stays). Returns the difference."""
    on_hand, avg = conn.execute("SELECT on_hand, avg_cost FROM items WHERE id = ?", (int(item_id),)).fetchone()
    on_hand, avg = float(on_hand or 0), float(avg or 0)
    diff = float(counted) - on_hand
    if abs(diff) < 1e-9:
        return 0.0
    conn.execute("UPDATE items SET on_hand = ? WHERE id = ?", (float(counted), int(item_id)))
    conn.execute("INSERT INTO stock_ledger (item_id, kind, qty, unit_cost, on_hand_after, avg_after, ref_type, ref_id, moved_on, note, created_by) VALUES (?, 'ADJ', ?, ?, ?, ?, ?, ?, ?, ?, acting_user())",
                 (int(item_id), diff, avg, float(counted), avg, ref_type, int(ref_id), str(moved_on or manila_today()), note))
    return diff


def moves(item_id: int):
    import pandas as pd
    from engines.common import get_conn
    with get_conn() as conn:
        return pd.read_sql_query("SELECT move_id, kind, qty, unit_cost, on_hand_after, avg_after, ref_type, ref_id, moved_on, note, created_by FROM stock_ledger WHERE item_id = ? ORDER BY move_id",
                                 conn, params=(int(item_id),))
