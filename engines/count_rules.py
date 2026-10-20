"""Stock counts (the revenue cycle, stage A; owner, 2026-10-11: "stock keeps getting lost"; an employee counts, with no sign-in of their own).

A count (CT-0001) is a BLIND sheet: the items to count, and nothing about what the system expects -- so the count is honest. No shutdown: a count is for
one type, one brand, the items not counted lately, or whatever a search finds, and selling goes on meanwhile.

  * start_count / save_counted / finish / cancel   -- the sheet: started and typed in by a signed-in person; "counted by" is the employee's name
  * differences / approve / recount / approve_matching -- the System admin's part: stock changes ONLY when a difference is approved, with a reason
  * sheet_rows / listing / get_count               -- for the screens and the printed blank sheet

What the system expected is frozen when the count is finished. Approving applies the DIFFERENCE to what is on hand at that moment, so a sale made
between the count and the approval is not undone. Every approved difference is a ledger row, an audit line and a Review line (COUNT_DIFFERENCE)."""
from __future__ import annotations

from engines.clock import manila_today
from engines.common import get_conn, write_tx
from engines.db_engine import next_doc_number
from engines.permissions import require as require_permission

EPS = 1e-9
STATUS = {"COUNTING": "Counting", "WAITING": "Waiting for approval", "CLOSED": "Closed", "CANCELLED": "Cancelled"}


def _scope_items(conn, category: str = "", brand: str = "", uncounted: bool = False, search: str = "", rim: str = "") -> tuple[list[int], str]:
    """The items a new count covers, and the count's scope in words. `rim`: tires of that rim size, as the stock notebook is tabbed (2026-10-17)."""
    from engines.rim_stock import rim_of
    from engines.stock_rules import CATEGORY_LABEL, stock_list
    rows = stock_list(search, category if category in CATEGORY_LABEL else "")
    words = []
    if category in CATEGORY_LABEL:
        words.append(CATEGORY_LABEL[category])
    if str(rim or "").strip():
        rim = str(rim).strip()
        on_rim = {int(i) for i, size in conn.execute("SELECT id, size_model FROM items WHERE category = 'tire'") if rim_of(size) == rim}
        rows = [r for r in rows if r["id"] in on_rim]
        words.append(f"Rim {rim}")
    if brand:
        brands = {int(r[0]) for r in conn.execute("SELECT id FROM items WHERE LOWER(brand) = LOWER(?)", (brand,))}
        rows = [r for r in rows if r["id"] in brands]
        words.append(brand)
    if uncounted:
        rows = [r for r in rows if r["uncounted"]]
        words.append("not counted in 90 days")
    if search.strip():
        words.append(f'"{" ".join(search.split())}"')
    return [r["id"] for r in rows], " · ".join(words) or "Everything"


def _choices(category, brand, uncounted, search, rim) -> str:
    import json
    return json.dumps({"category": category or "", "brand": brand or "", "uncounted": bool(uncounted), "search": " ".join(str(search or "").split()),
                       "rim": str(rim or "").strip()})


def start_count(category: str = "", brand: str = "", uncounted: bool = False, search: str = "", counted_by: str = "", rim: str = "") -> tuple[bool, str, int | None]:
    """A new count sheet for the items chosen. Nothing about stock changes."""
    allowed, why = require_permission("inventory.use")
    if not allowed:
        return False, why, None
    with write_tx() as conn:
        ids, scope = _scope_items(conn, category, brand, uncounted, search, rim)
        if not ids:
            return False, "No item matches that choice, so there is nothing to count.", None
        if len(ids) > 600:
            return False, f"That is {len(ids)} items in one count. Choose a type or a brand: a count is done a part at a time.", None
        from engines.company import number as _co_number, number_prefix as _co_prefix, seq_of as _co_seq
        used = [n for r in conn.execute("SELECT count_no FROM stock_counts WHERE count_no LIKE ?", (_co_prefix("num_ct") + "%",)) if (n := _co_seq("num_ct", r[0])) is not None]
        number = _co_number("num_ct", next_doc_number(conn, 'CT', 0, max(used, default=0)))
        cid = conn.execute("INSERT INTO stock_counts (count_no, scope, counted_by, started_by, scope_json) VALUES (?, ?, ?, acting_user(), ?) RETURNING count_id",
                           (number, scope, " ".join(str(counted_by or "").split()) or None, _choices(category, brand, uncounted, search, rim))).fetchone()[0]
        conn.executemany("INSERT INTO stock_count_lines (count_id, item_id) VALUES (?, ?)", [(cid, i) for i in ids])
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'COUNT_STARTED', 'stock_counts', ?, ?)",
                     (cid, f"{number}: {scope}, {len(ids)} item(s)"))
    return True, f"{number} started: {len(ids)} item(s) to count.", int(cid)


def change_count(count_id: int, category: str = "", brand: str = "", uncounted: bool = False, search: str = "", counted_by: str = "",
                 rim: str = "") -> tuple[bool, str]:
    """A count still being counted is changed (owner, 2026-10-17: "make this editable until closed or finished"): who counts, and what it covers.
    Items the new choice adds come onto the sheet; items it drops leave it only when nothing was typed for them -- a figure typed is never lost.
    An opening count has no choice to change (its items are added as found): only who counts."""
    allowed, why = require_permission("inventory.use")
    if not allowed:
        return False, why
    who = " ".join(str(counted_by or "").split()) or None
    with write_tx() as conn:
        row = conn.execute("SELECT status, count_no, opening FROM stock_counts WHERE count_id = ?", (int(count_id),)).fetchone()
        if not row:
            return False, "Count not found."
        if row[0] != "COUNTING":
            return False, f"{row[1]} is already finished: it can no longer be changed."
        if row[2]:
            conn.execute("UPDATE stock_counts SET counted_by = ? WHERE count_id = ?", (who, int(count_id)))
            return True, f"{row[1]}: counted by {who or 'nobody named'}."
        ids, scope = _scope_items(conn, category, brand, uncounted, search, rim)
        if not ids:
            return False, "No item matches that choice: the count was not changed."
        if len(ids) > 600:
            return False, f"That is {len(ids)} items in one count. Choose a type, a brand or a size: the count was not changed."
        lines = {int(i): (c, (n or "").strip()) for i, c, n in conn.execute("SELECT item_id, counted, note FROM stock_count_lines WHERE count_id = ?", (int(count_id),))}
        want = set(ids)
        add = [i for i in ids if i not in lines]
        drop = [i for i, (c, n) in lines.items() if i not in want and c is None and not n]
        kept = [i for i, (c, n) in lines.items() if i not in want and (c is not None or n)]
        conn.executemany("INSERT INTO stock_count_lines (count_id, item_id) VALUES (?, ?)", [(int(count_id), i) for i in add])
        conn.executemany("DELETE FROM stock_count_lines WHERE count_id = ? AND item_id = ? AND counted IS NULL", [(int(count_id), i) for i in drop])
        conn.execute("UPDATE stock_counts SET scope = ?, scope_json = ?, counted_by = ? WHERE count_id = ?",
                     (scope, _choices(category, brand, uncounted, search, rim), who, int(count_id)))
        msg = f"{row[1]} now covers {scope}: {len(add)} item(s) added, {len(drop)} removed" + (f", {len(kept)} already counted kept on the sheet" if kept else "") + "."
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'COUNT_CHANGED', 'stock_counts', ?, ?)",
                     (int(count_id), msg + (f" Counted by {who}." if who else "")))
    return True, msg


def start_opening(counted_by: str = "") -> tuple[bool, str, int | None]:
    """An opening count (owner, 2026-10-13: stock the system does not know yet, "we have inventory notebook"): an EMPTY sheet. Each tire is found
    or added as the rack is walked (add_line, add_new). Approved like any count; nothing about stock changes until then."""
    allowed, why = require_permission("inventory.use")
    if not allowed:
        return False, why, None
    with write_tx() as conn:
        used = [int(r[0][3:]) for r in conn.execute("SELECT count_no FROM stock_counts WHERE count_no LIKE 'CT-%'") if r[0][3:].isdigit()]
        number = f"CT-{next_doc_number(conn, 'CT', 0, max(used, default=0)):04d}"
        cid = conn.execute("INSERT INTO stock_counts (count_no, scope, counted_by, started_by, opening) VALUES (?, 'Opening count', ?, acting_user(), 1) RETURNING count_id",
                           (number, " ".join(str(counted_by or "").split()) or None)).fetchone()[0]
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'COUNT_STARTED', 'stock_counts', ?, ?)",
                     (cid, f"{number}: opening count, items added as counted"))
    return True, f"{number} started. Find or add each item as you reach it.", int(cid)


def _amount(text, what: str) -> tuple[float | None, str]:
    """A typed quantity or cost: (number or None when empty, '' or what is wrong)."""
    raw = str(text if text is not None else "").replace(",", "").strip()
    if raw == "":
        return None, ""
    try:
        n = float(raw)
    except ValueError:
        return None, f"'{raw}' is not {what}."
    return (n, "") if n >= 0 else (None, f"{what.capitalize()} cannot be below zero.")


def _put_line(conn, count_id: int, item_id: int, qty: float, cost: float | None, dot, is_new: bool) -> float:
    """An item onto an opening sheet with what was counted. Already on it (the same tire on a second rack): the quantity is ADDED. Returns the line's total.
    The cost is kept only while the item has none of its own: typed, or else the supplier's price."""
    from engines.stock_rules import note_dot
    has_cost = float(conn.execute("SELECT COALESCE(avg_cost, 0) FROM items WHERE id = ?", (int(item_id),)).fetchone()[0]) > 0
    if has_cost:
        cost = None
    elif not cost:
        cost = _list_cost(int(item_id)) or None
    row = conn.execute("SELECT line_id, counted FROM stock_count_lines WHERE count_id = ? AND item_id = ?", (int(count_id), int(item_id))).fetchone()
    if row:
        total = float(row[1] or 0) + float(qty)
        conn.execute("UPDATE stock_count_lines SET counted = ?, cost_typed = COALESCE(?, cost_typed) WHERE line_id = ?", (total, cost, row[0]))
    else:
        total = float(qty)
        conn.execute("INSERT INTO stock_count_lines (count_id, item_id, counted, cost_typed, is_new) VALUES (?, ?, ?, ?, ?)", (int(count_id), int(item_id), total, cost, 1 if is_new else 0))
    note_dot(conn, int(item_id), dot)
    return total


def _opening_sheet(conn, count_id: int) -> str:
    """'' when items may be added to this count, or why not."""
    row = conn.execute("SELECT status, opening FROM stock_counts WHERE count_id = ?", (int(count_id),)).fetchone()
    if not row:
        return "Count not found."
    if not row[1]:
        return "Items are added only to an opening count."
    return "" if row[0] == "COUNTING" else "This count is finished: nothing more can be added."


def add_line(count_id: int, item_id: int, qty, cost=None, dot="") -> tuple[bool, str]:
    """An item the system already has, onto an opening sheet with how many are on the rack."""
    allowed, why = require_permission("inventory.use")
    if not allowed:
        return False, why
    n, bad = _amount(qty, "a quantity")
    c, bad2 = _amount(cost, "a cost")
    if bad or bad2 or n is None:
        return False, bad or bad2 or "Type how many are on the rack."
    with write_tx() as conn:
        why = _opening_sheet(conn, count_id)
        if why:
            return False, why
        it = conn.execute("SELECT name FROM items WHERE id = ? AND is_active = 1", (int(item_id),)).fetchone()
        if not it:
            return False, "Item not found."
        total = _put_line(conn, count_id, int(item_id), n, c, dot, False)
    return True, f"{it[0]}: {total:g} counted."


def add_new(count_id: int, category: str, brand: str, values: dict, qty, cost=None, dot="") -> tuple[bool, str]:
    """An item the system does not have: made (with its code) and counted in one step. The same fields as the catalog; an item that turns out to
    exist already is simply counted."""
    from engines.product_catalog import CATEGORY_FIELDS, get_or_create_product, label_of
    allowed, why = require_permission("inventory.use")
    if not allowed:
        return False, why
    category, brand = label_of(category), " ".join(str(brand or "").split())
    if category not in CATEGORY_FIELDS:
        return False, "Choose the type: tire, battery, mags or rims."
    if not brand:
        return False, "Type the brand."
    keys = {f["key"] for f in CATEGORY_FIELDS[category]}
    values = {k: " ".join(str(v or "").split()) for k, v in values.items() if k in keys}
    missing = [f["label"] for f in CATEGORY_FIELDS[category] if f["required"] and not values.get(f["key"])]
    if missing:
        return False, "Still needed: " + ", ".join(missing) + "."
    n, bad = _amount(qty, "a quantity")
    c, bad2 = _amount(cost, "a cost")
    if bad or bad2 or n is None:
        return False, bad or bad2 or "Type how many are on the rack."
    with write_tx() as conn:
        why = _opening_sheet(conn, count_id)
        if why:
            return False, why
        old = conn.execute("SELECT brand FROM items WHERE lower(brand) = lower(?) LIMIT 1", (brand,)).fetchone()
        item_id, name, created = get_or_create_product(category, old[0] if old else brand, conn=conn, **values)      # one spelling per brand
        if not item_id:
            return False, "That item could not be added. Check the fields."
        conn.execute("UPDATE items SET is_active = 1 WHERE id = ?", (int(item_id),))
        total = _put_line(conn, count_id, int(item_id), n, c, dot, created)
    return True, (f"{name} added and counted: {total:g}." if created else f"{name} was already in the system: {total:g} counted.")


def set_cost(line_id: int, cost) -> tuple[bool, str]:
    """The System admin types the cost of a counted item that has none: before the approval (kept on the line), or after it (the item's cost itself,
    with a ledger row). An item that already has a cost is not touched here."""
    allowed, why = require_permission("inventory.approve")
    if not allowed:
        return False, why
    c, bad = _amount(cost, "a cost")
    if bad or not c:
        return False, bad or "Type the cost each."
    with write_tx() as conn:
        row = conn.execute("""SELECT l.count_id, l.item_id, l.decision, c.count_no, i.name, COALESCE(i.avg_cost, 0), i.on_hand FROM stock_count_lines l
                              JOIN stock_counts c ON c.count_id = l.count_id JOIN items i ON i.id = l.item_id WHERE l.line_id = ?""", (int(line_id),)).fetchone()
        if not row:
            return False, "Line not found."
        count_id, item_id, decision, number, name, avg, on_hand = row
        if float(avg) > 0:
            return False, f"{name} already has a cost (₱{float(avg):,.2f})."
        conn.execute("UPDATE stock_count_lines SET cost_typed = ?, unit_cost = CASE WHEN expected IS NOT NULL THEN ? ELSE unit_cost END WHERE line_id = ?", (c, c, int(line_id)))
        if decision == "APPROVED":
            conn.execute("UPDATE items SET avg_cost = ? WHERE id = ?", (c, int(item_id)))
            from engines import fifo                              # the count's batch had no cost: it takes this one, and so do sales that took its pieces
            fifo.reprice(conn, int(item_id), c, "stock_counts", int(count_id), only_zero=True)
            fifo.refresh(conn, int(item_id))
            conn.execute("""INSERT INTO stock_ledger (item_id, kind, qty, unit_cost, on_hand_after, avg_after, ref_type, ref_id, moved_on, note, created_by)
                            VALUES (?, 'COST', 0, ?, ?, ?, 'stock_counts', ?, ?, ?, acting_user())""",
                         (int(item_id), c, float(on_hand or 0), c, int(count_id), str(manila_today()), f"{number}: cost typed after the count"))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'COUNT_COST_TYPED', 'stock_counts', ?, ?)",
                     (int(count_id), f"{number}: {name} cost {c:,.2f} each"))
    return True, f"{name}: cost ₱{c:,.2f} each."


def get_count(count_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("""SELECT count_id, count_no, scope, status, counted_by, started_by, datetime(started_at, '+8 hours'), finished_by, datetime(finished_at, '+8 hours'), closed_at,
                                     (SELECT COUNT(*) FROM stock_count_lines l WHERE l.count_id = c.count_id),
                                     (SELECT COUNT(*) FROM stock_count_lines l WHERE l.count_id = c.count_id AND l.counted IS NOT NULL), opening, scope_json
                              FROM stock_counts c WHERE count_id = ?""", (int(count_id),)).fetchone()
    if not row:
        return None
    keys = ("count_id", "count_no", "scope", "status", "counted_by", "started_by", "started_at", "finished_by", "finished_at", "closed_at", "items", "typed", "opening",
            "scope_json")
    out = dict(zip(keys, row))
    import json
    try:
        out["choices"] = json.loads(out.pop("scope_json") or "{}")
    except ValueError:
        out["choices"] = {}
    out["status_label"] = STATUS.get(out["status"], out["status"])
    return out


def listing() -> list[dict]:
    with get_conn() as conn:
        ids = [r[0] for r in conn.execute("SELECT count_id FROM stock_counts ORDER BY count_id DESC LIMIT 100")]
    out = []
    for i in ids:
        c = get_count(i)
        if c["status"] in ("WAITING", "CLOSED"):
            d = differences(i)
            c["short"], c["over"] = sum(1 for x in d if x["difference"] < -EPS), sum(1 for x in d if x["difference"] > EPS)
            c["value"] = round(sum(x["value"] for x in d), 2)
            c["open"] = sum(1 for x in d if x["decision"] is None)
        out.append(c)
    return out


def sheet_rows(count_id: int) -> list[dict]:
    """The blind sheet: line_id, item_id, code, name, counted, note, codes (what a scan of this item reads) -- and NOTHING about what is expected.
    Also is_new, cost (the item's own, else the one typed at the count) and own_cost (the item has one: not to be typed). An opening sheet: newest first."""
    with get_conn() as conn:
        opening = (conn.execute("SELECT opening FROM stock_counts WHERE count_id = ?", (int(count_id),)).fetchone() or [0])[0]
        rows = conn.execute("""SELECT l.line_id, i.id, i.code, i.name, l.counted, l.note, l.is_new, l.cost_typed, COALESCE(i.avg_cost, 0) FROM stock_count_lines l
                               JOIN items i ON i.id = l.item_id WHERE l.count_id = ? AND l.decision IS NULL ORDER BY """
                            + ("l.line_id DESC" if opening else "i.category, i.name COLLATE NOCASE"), (int(count_id),)).fetchall()
        codes: dict[int, list[str]] = {}
        for b, iid in conn.execute("SELECT barcode, item_id FROM item_barcodes"):
            codes.setdefault(int(iid), []).append(b)
    return [{"line_id": int(a), "item_id": int(b), "code": c, "name": d, "counted": e, "note": f or "", "codes": [c.upper()] + codes.get(int(b), []),
             "is_new": bool(g), "own_cost": float(k) > 0, "cost": float(k) if float(k) > 0 else h} for a, b, c, d, e, f, g, h, k in rows]


def save_counted(count_id: int, typed: dict, counted_by: str | None = None) -> tuple[bool, str]:
    """What was counted so far: {line_id: (quantity or None, note)} or (quantity, note, cost typed or None). Saved as typed; nothing about stock changes."""
    allowed, why = require_permission("inventory.use")
    if not allowed:
        return False, why
    with write_tx() as conn:
        row = conn.execute("SELECT status FROM stock_counts WHERE count_id = ?", (int(count_id),)).fetchone()
        if not row:
            return False, "Count not found."
        if row[0] != "COUNTING":
            return False, "This count is finished: it can no longer be typed in."
        for line_id, got in typed.items():
            qty, note = got[0], got[1]
            if qty is not None and qty < 0:
                return False, "A counted quantity cannot be below zero."
            if len(got) > 2:
                if got[2] is not None and got[2] < 0:
                    return False, "A cost cannot be below zero."
                conn.execute("UPDATE stock_count_lines SET cost_typed = ? WHERE line_id = ? AND count_id = ? AND decision IS NULL", (got[2] or None, int(line_id), int(count_id)))
            conn.execute("UPDATE stock_count_lines SET counted = ?, note = ? WHERE line_id = ? AND count_id = ? AND decision IS NULL",
                         (qty, " ".join(str(note or "").split()) or None, int(line_id), int(count_id)))
        if counted_by is not None:
            conn.execute("UPDATE stock_counts SET counted_by = ? WHERE count_id = ?", (" ".join(counted_by.split()) or None, int(count_id)))
    return True, "Saved."


def finish(count_id: int) -> tuple[bool, str]:
    """The counting is done: what the system has is frozen beside each counted quantity. Items that match are closed at once; the others wait for the
    System admin. An item left empty was not counted and stays out of this count."""
    allowed, why = require_permission("inventory.use")
    if not allowed:
        return False, why
    with write_tx() as conn:
        row = conn.execute("SELECT status, count_no, counted_by FROM stock_counts WHERE count_id = ?", (int(count_id),)).fetchone()
        if not row:
            return False, "Count not found."
        if row[0] != "COUNTING":
            return False, f"{row[1]} is already finished."
        if not (row[2] or "").strip():
            return False, "Type who counted (\"Counted by\") before finishing."
        lines = conn.execute("""SELECT l.line_id, l.item_id, l.counted, i.on_hand, CASE WHEN COALESCE(i.avg_cost, 0) > 0 THEN i.avg_cost ELSE l.cost_typed END
                                FROM stock_count_lines l JOIN items i ON i.id = l.item_id WHERE l.count_id = ? AND l.decision IS NULL""", (int(count_id),)).fetchall()
        typed = [ln for ln in lines if ln[2] is not None]
        if not typed:
            return False, "Nothing is typed yet. Type what was counted, then finish."
        today, waiting = str(manila_today()), 0
        for line_id, item_id, counted, on_hand, cost in typed:
            same = abs(float(counted) - float(on_hand or 0)) < EPS
            conn.execute("UPDATE stock_count_lines SET expected = ?, unit_cost = ?, decision = ?, decided_at = CASE WHEN ? THEN CURRENT_TIMESTAMP END WHERE line_id = ?",
                         (float(on_hand or 0), float(cost or 0), "MATCHED" if same else None, 1 if same else 0, line_id))
            if same:
                conn.execute("UPDATE items SET last_counted_at = ? WHERE id = ?", (today, item_id))
            else:
                waiting += 1
        conn.execute("DELETE FROM stock_count_lines WHERE count_id = ? AND counted IS NULL AND decision IS NULL", (int(count_id),))      # not counted: not part of this count
        conn.execute("UPDATE stock_counts SET status = ?, finished_by = acting_user(), finished_at = CURRENT_TIMESTAMP, closed_at = CASE WHEN ? = 0 THEN CURRENT_TIMESTAMP END WHERE count_id = ?",
                     ("WAITING" if waiting else "CLOSED", waiting, int(count_id)))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'COUNT_FINISHED', 'stock_counts', ?, ?)",
                     (int(count_id), f"{row[1]}: {len(typed)} item(s) counted by {row[2]}, {waiting} differ, {len(lines) - len(typed)} not counted"))
    left = len(lines) - len(typed)
    return True, (f"{row[1]} finished: {len(typed) - waiting} matched, {waiting} differ and wait for the System admin's approval."
                  + (f" {left} item(s) were left empty and are not part of this count." if left else ""))


def cancel(count_id: int) -> tuple[bool, str]:
    allowed, why = require_permission("inventory.use")
    if not allowed:
        return False, why
    with write_tx() as conn:
        row = conn.execute("SELECT status, count_no FROM stock_counts WHERE count_id = ?", (int(count_id),)).fetchone()
        if not row or row[0] != "COUNTING":
            return False, "Only a count still being typed can be cancelled."
        conn.execute("UPDATE stock_counts SET status = 'CANCELLED', closed_at = CURRENT_TIMESTAMP WHERE count_id = ?", (int(count_id),))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'COUNT_CANCELLED', 'stock_counts', ?, ?)",
                     (int(count_id), row[1]))
    return True, f"{row[1]} cancelled. Nothing about stock changed."


def _hints(conn, item_id: int, since) -> list[str]:
    """Where a missing piece may be: lent to an affiliate and not back, released and not invoiced, moved since the count was finished."""
    out = []
    for no, name, qty in conn.execute("""SELECT m.move_no, c.customer_name, l.qty - l.qty_settled FROM affiliate_move_lines l JOIN affiliate_moves m ON m.move_id = l.move_id
                                         JOIN customers c ON c.customer_id = m.customer_id WHERE l.item_id = ? AND m.direction = 'LEND' AND l.qty - l.qty_settled > 0.0000001""", (item_id,)):
        out.append(f"{qty:g} lent to {name} on {no}, not settled")
    for no, qty in conn.execute("""SELECT s.release_no, SUM(l.qty) FROM sale_lines l JOIN sales s ON s.sale_id = l.sale_id
                                   WHERE l.item_id = ? AND s.status = 'RELEASED' GROUP BY s.sale_id""", (item_id,)):
        out.append(f"{qty:g} released on {no}, not invoiced yet")
    moved = conn.execute("SELECT COALESCE(SUM(qty), 0), COUNT(*) FROM stock_ledger WHERE item_id = ? AND created_at > ? AND kind != 'COST'", (item_id, since)).fetchone() if since else (0, 0)
    if moved[1]:
        out.append(f"{moved[0]:+g} moved since the count was finished")
    return out


def differences(count_id: int) -> list[dict]:
    """Every counted line with what the system had: line_id, item_id, code, name, expected, counted, difference, value, note, decision, reason, decided_by,
    hints. Matched lines included (difference 0)."""
    with get_conn() as conn:
        since = (conn.execute("SELECT finished_at FROM stock_counts WHERE count_id = ?", (int(count_id),)).fetchone() or [None])[0]
        rows = conn.execute("""SELECT l.line_id, i.id, i.code, i.name, l.expected, l.counted, l.unit_cost, l.note, l.decision, l.reason, l.decided_by,
                                      l.is_new, COALESCE(i.avg_cost, 0)
                               FROM stock_count_lines l JOIN items i ON i.id = l.item_id WHERE l.count_id = ? AND l.counted IS NOT NULL AND l.expected IS NOT NULL
                               ORDER BY (l.decision IS NOT NULL), ABS(l.counted - l.expected) * l.unit_cost DESC, i.name COLLATE NOCASE""", (int(count_id),)).fetchall()
        out = []
        for line_id, item_id, code, name, expected, counted, cost, note, decision, reason, who, is_new, avg in rows:
            diff = float(counted) - float(expected)
            out.append({"line_id": int(line_id), "item_id": int(item_id), "code": code, "name": name, "expected": float(expected), "counted": float(counted),
                        "difference": diff, "value": round(diff * float(cost or 0), 2), "note": note or "", "decision": decision, "reason": reason or "", "decided_by": who or "",
                        "is_new": bool(is_new), "cost": float(cost or 0), "no_cost": float(counted) > 0 and float(avg) <= 0 and float(cost or 0) <= 0,
                        "hints": _hints(conn, int(item_id), since) if decision is None and diff < -EPS else []})
    return out


def _close_if_done(conn, count_id: int) -> None:
    left = conn.execute("SELECT COUNT(*) FROM stock_count_lines WHERE count_id = ? AND decision IS NULL", (int(count_id),)).fetchone()[0]
    if not left:
        conn.execute("UPDATE stock_counts SET status = 'CLOSED', closed_at = CURRENT_TIMESTAMP WHERE count_id = ? AND status = 'WAITING'", (int(count_id),))


def _list_cost(item_id: int) -> float:
    """What an item costs when the system has never received it: the lowest supplier net price in force today (0 when it has no price)."""
    try:
        from engines.product_catalog import supplier_prices
        df = supplier_prices(product_id=int(item_id))
        return float(df["net_price"].min()) if not df.empty else 0.0
    except Exception:                                            # noqa: BLE001 -- a price list problem must not stop a count from being approved
        return 0.0


def approve_all(count_id: int, reason: str) -> tuple[bool, str]:
    """The System admin accepts every difference still waiting in a count with one reason (the opening count: "what was on the rack when we started")."""
    allowed, why = require_permission("inventory.approve")
    if not allowed:
        return False, why
    if not " ".join(str(reason or "").split()):
        return False, "Say why they differ: one reason for all of them (e.g. 'opening count')."
    done = 0
    for r in differences(count_id):
        if r["decision"] is None:
            ok, msg = approve(r["line_id"], reason)
            if not ok:
                return False, msg
            done += 1
    return (True, f"{done} difference(s) approved.") if done else (False, "Nothing is waiting for approval in this count.")


def approve(line_id: int, reason: str) -> tuple[bool, str]:
    """The System admin accepts a difference, with a reason: only now does stock change -- by the difference, applied to what is on hand at this moment."""
    allowed, why = require_permission("inventory.approve")
    if not allowed:
        return False, why
    reason = " ".join(str(reason or "").split())
    if not reason:
        return False, "Say why it differs (e.g. 'damaged, scrapped', 'found on another rack')."
    from engines.review_flags import flag
    from engines.stock_ledger import stock_set
    with write_tx() as conn:
        row = conn.execute("""SELECT l.count_id, l.item_id, l.expected, l.counted, l.unit_cost, l.decision, c.count_no, c.status, c.counted_by, i.name, i.on_hand,
                                     COALESCE(i.avg_cost, 0)
                              FROM stock_count_lines l JOIN stock_counts c ON c.count_id = l.count_id JOIN items i ON i.id = l.item_id WHERE l.line_id = ?""", (int(line_id),)).fetchone()
        if not row:
            return False, "Line not found."
        count_id, item_id, expected, counted, cost, decision, number, status, counted_by, name, on_hand, avg = row
        if status != "WAITING" or decision is not None or expected is None:
            return False, "This line is not waiting for approval."
        diff = float(counted) - float(expected)
        if diff > 0 and float(avg) <= 0:                         # counted before it was ever received (the opening count): the cost typed, else the supplier's price
            cost = float(cost or 0) or _list_cost(int(item_id))
            if cost > 0:
                conn.execute("UPDATE items SET avg_cost = ? WHERE id = ? AND COALESCE(avg_cost, 0) <= 0", (cost, int(item_id)))
                conn.execute("UPDATE stock_count_lines SET unit_cost = ? WHERE line_id = ?", (cost, int(line_id)))
        stock_set(conn, int(item_id), float(on_hand or 0) + diff, "stock_counts", int(count_id), note=f"{number}: counted {float(counted):g}, expected {float(expected):g}: {reason}")
        conn.execute("UPDATE stock_count_lines SET decision = 'APPROVED', reason = ?, decided_by = acting_user(), decided_at = CURRENT_TIMESTAMP WHERE line_id = ?", (reason, int(line_id)))
        conn.execute("UPDATE items SET last_counted_at = ? WHERE id = ?", (str(manila_today()), int(item_id)))
        note = (f"{number}: {name} counted {float(counted):g} against {float(expected):g} ({diff:+g}, ₱{diff * float(cost or 0):+,.2f}); counted by {counted_by or 'not named'}. "
                f"Approved: {reason}" + (" NO COST YET: type it on the count's page." if diff > 0 and float(cost or 0) <= 0 else ""))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'COUNT_DIFFERENCE_APPROVED', 'stock_counts', ?, ?)",
                     (int(count_id), note))
        flag(conn, "COUNT_DIFFERENCE", "stock_counts", int(count_id), note)
        _close_if_done(conn, int(count_id))
    return True, f"{name}: {diff:+g} approved. On hand is now {float(on_hand or 0) + diff:g}."


def recount(line_id: int) -> tuple[bool, str]:
    """The System admin sends one item back to be counted again: its count is cleared and the sheet reopens for it. Stock does not change."""
    allowed, why = require_permission("inventory.approve")
    if not allowed:
        return False, why
    with write_tx() as conn:
        row = conn.execute("""SELECT l.count_id, l.decision, c.status, c.count_no, i.name FROM stock_count_lines l JOIN stock_counts c ON c.count_id = l.count_id
                              JOIN items i ON i.id = l.item_id WHERE l.line_id = ?""", (int(line_id),)).fetchone()
        if not row or row[2] != "WAITING" or row[1] is not None:
            return False, "This line is not waiting for approval."
        conn.execute("UPDATE stock_count_lines SET counted = NULL, expected = NULL, unit_cost = NULL, note = NULL WHERE line_id = ?", (int(line_id),))
        conn.execute("UPDATE stock_counts SET status = 'COUNTING', finished_at = NULL, finished_by = NULL WHERE count_id = ?", (row[0],))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'COUNT_AGAIN', 'stock_counts', ?, ?)",
                     (row[0], f"{row[3]}: {row[4]} to be counted again"))
    return True, f"{row[4]} is back on the sheet to be counted again."


def waiting_count() -> int:
    """How many differences wait for the System admin, across every count."""
    with get_conn() as conn:
        return int(conn.execute("""SELECT COUNT(*) FROM stock_count_lines l JOIN stock_counts c ON c.count_id = l.count_id
                                   WHERE c.status = 'WAITING' AND l.decision IS NULL AND l.expected IS NOT NULL""").fetchone()[0])
