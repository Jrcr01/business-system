"""Record a sale on the phone (owner, 2026-10-12: "The input amount is cash receipts just workback the profit"): the invoice is still written by
hand; the phone records it whenever -- at the counter, idle, or at night -- from the cash receipt, not a price per item. The receipt, less any
service typed, is spread over the scanned lines in proportion to their cost (evenly when every cost is 0), so the sale, the day sheet, the item
history and the income statement work exactly as for an invoice typed on the laptop. Surplus and trade-in tires (owner: "catchall name surplus
tire, rim, estimate cost") come in through take_in at an estimated cost and sell the same way; the whole receipt less the estimate is profit."""
from __future__ import annotations

from engines.common import get_conn


def _num(text, default=0.0) -> float:
    try:
        return float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def spread(lines: list[dict], receipt: float, services_total: float = 0.0) -> list[dict]:
    """[{'item_id', 'qty', 'cost'}] + the receipt -> the same lines with 'price' each, the receipt less services spread by cost (evenly when the
    costs are all 0); the last line takes the rounding so the lines add up to the centavo."""
    goods = round(float(receipt) - float(services_total), 2)
    live = [ln for ln in lines if _num(ln.get("qty")) > 0]
    if not live:
        return []
    weights = [_num(ln.get("qty")) * _num(ln.get("cost")) for ln in live]
    if sum(weights) <= 0:
        weights = [_num(ln.get("qty")) for ln in live]
    total_w = sum(weights)
    out, given = [], 0.0
    for k, (ln, w) in enumerate(zip(live, weights)):
        qty = _num(ln["qty"])
        amount = round(goods - given, 2) if k == len(live) - 1 else round(goods * w / total_w, 2)
        price = int(amount / qty * 100) / 100 if qty else 0.0          # whole centavos, never above the share: the lines never add up to MORE than the receipt
        given = round(given + round(price * qty, 2), 2)
        out.append({**ln, "price": f"{price:.2f}"})
    return out


def rounding_left(priced: list[dict], goods: float) -> float:
    """What the whole-centavo prices leave of the goods figure (3 pieces for 1,000.00 -> 333.33 each -> 0.01 left): recorded as a 'Rounding' service so
    the sale adds up to the receipt exactly and the payment is accepted."""
    return round(float(goods) - sum(round(_num(ln["price"]) * _num(ln["qty"]), 2) for ln in priced), 2)


def _with_rounding(svc: list[dict], priced: list[dict], goods: float) -> list[dict]:
    left = rounding_left(priced, goods)
    return svc + ([{"description": "Rounding", "amount": f"{left:.2f}"}] if left >= 0.01 else [])


def costs_of(item_ids: list[int]) -> dict[int, float]:
    if not item_ids:
        return {}
    marks = ",".join("?" for _ in item_ids)
    with get_conn() as conn:
        got = {int(r[0]): float(r[1] or 0) for r in conn.execute(f"SELECT id, COALESCE(NULLIF(next_cost, 0), avg_cost) FROM items WHERE id IN ({marks})", [int(i) for i in item_ids])}
    missing = [i for i, c in got.items() if c <= 0]
    if missing:                                                  # never delivered: the price list's net (owner, 2026-10-18)
        from engines.list_cost import list_costs
        got.update(list_costs(missing))
    return got


def record(si_no, sale_date, customer_id, customer_name: str, lines: list[dict], receipt, services: list[dict], payment: dict,
           release_id: int | None = None, below_cost_reason: str = "", surplus: list[dict] | None = None) -> tuple[bool, str, int | None]:
    """The phone's sale: lines [{'item_id', 'qty'}] (priced here from the receipt), one payment {'method', 'reference', 'bank', 'check_no',
    'check_date'} for the whole receipt. With release_id, the goods already left on that slip: its lines are invoiced instead (stock not taken twice)."""
    from engines import sales_rules as sr
    rec = _num(receipt)
    if rec <= 0:
        return False, "Type the amount received (the invoice total).", None
    svc = [s for s in services if str(s.get("description", "")).strip() and _num(s.get("amount")) > 0]
    svc_total = round(sum(_num(s["amount"]) for s in svc), 2)
    if svc_total > rec + 0.005:
        return False, f"The services ({svc_total:,.2f}) are more than the amount received ({rec:,.2f}).", None
    pay = [{"method": payment.get("method", "CASH"), "amount": f"{rec:.2f}", "bank": payment.get("bank", ""), "check_no": payment.get("check_no", ""),
            "check_date": payment.get("check_date", ""), "reference": payment.get("reference", "")}]
    if release_id:
        head = sr.get_sale(int(release_id))
        if not head or head["status"] != "RELEASED":
            return False, "That slip is already invoiced or cancelled.", None
        slip_lines = [{"line_id": ln["line_id"], "item_id": ln["item_id"], "qty": ln["qty"], "cost": ln.get("unit_cost", 0)} for ln in head["lines"]]
        priced = spread(slip_lines, rec, svc_total)
        ok, msg = sr.invoice_release(int(release_id), si_no, sale_date, {ln["line_id"]: ln["price"] for ln in priced}, _with_rounding(svc, priced, rec - svc_total), pay,
                                     below_cost_reason)
        return ok, msg, int(release_id) if ok else None
    # an item with no cost yet carries its own price and profit (owner, 2026-10-18, until the opening count); the rest share what the receipt leaves
    clean = [{"item_id": int(ln["item_id"]), "qty": _num(ln.get("qty")), "own": _num(ln.get("price")) if str(ln.get("price") or "").strip() else None,
              "margin": ln.get("margin", ""), "typed_cost": ln.get("typed_cost", ""), "cost_changed": ln.get("cost_changed", True)} for ln in lines if str(ln.get("item_id", "")).isdigit() and _num(ln.get("qty")) > 0]
    extra = [{"item_id": int(s["item_id"]), "qty": _num(s.get("qty")), "cost": _num(s.get("cost"))} for s in (surplus or [])
             if str(s.get("item_id", "")).isdigit() and _num(s.get("qty")) > 0]
    if not clean and not extra:
        return False, "Scan or pick what was sold.", None
    with get_conn() as conn:                                     # the number is checked before anything is written (a used or blank SI refuses here)
        n, problem = sr._check_si(conn, si_no)
    if problem:
        return False, problem, None
    before = {}
    for s in extra:                                              # a surplus tire sold on the spot: taken in at the cost typed, then sold (owner, 2026-10-12)
        if s["cost"] < 0:
            return False, "A surplus item's cost cannot be below zero.", None
        from engines.stock_rules import take_in
        with get_conn() as conn:
            before[s["item_id"]] = conn.execute("SELECT on_hand, avg_cost FROM items WHERE id = ?", (s["item_id"],)).fetchone()
        ok, why = take_in(s["item_id"], s["qty"], s["cost"], f"sold on SI {n:06d}, cost as typed at the sale")
        if not ok:
            _undo_take_ins(before, "the sale was not saved")
            return False, why, None
    costs = costs_of([ln["item_id"] for ln in clean])
    for s in extra:
        same = next((ln for ln in clean if ln["item_id"] == s["item_id"]), None)
        if same:
            same["qty"] += s["qty"]
        else:
            clean.append({"item_id": s["item_id"], "qty": s["qty"]})
        costs[s["item_id"]] = s["cost"]
    own = [ln for ln in clean if ln.get("own") is not None]
    own_total = round(sum(ln["qty"] * ln["own"] for ln in own), 2)
    rest = [ln for ln in clean if ln.get("own") is None]
    if rest and own_total > rec - svc_total + 0.005:
        return False, f"The items priced one by one (₱{own_total:,.2f}) are more than the amount received less services (₱{rec - svc_total:,.2f}).", None
    if not rest and own_total > 0 and abs(rec - svc_total - own_total) > 0.005:
        # every item priced, and the amount received typed over (a discount on the whole receipt, answer B): the prices follow it in proportion, each
        # cost worked back from a profit stays the same peso figure
        f = (rec - svc_total) / own_total
        for ln in own:
            old = ln["own"]
            ln["own"] = int(old * f * 100) / 100
            if str(ln.get("margin") or "").strip():
                try:
                    ln["margin"] = f"{ln['own'] - (old - float(str(ln['margin']).replace(',', ''))):.2f}"
                except ValueError:
                    pass
        own_total = round(sum(ln["qty"] * ln["own"] for ln in own), 2)
    priced_rest = spread([{**ln, "cost": costs.get(ln["item_id"], 0.0)} for ln in rest], rec, svc_total + own_total)
    priced = [{**ln, "price": f"{ln['own']:.2f}"} for ln in own] + priced_rest
    ok, msg, sale_id = sr.save_sale(si_no, sale_date, customer_id, customer_name,
                                    [{"item_id": ln["item_id"], "qty": ln["qty"], "price": ln["price"], "margin": ln.get("margin", ""),
                                      "typed_cost": ln.get("typed_cost", ""), "cost_changed": ln.get("cost_changed", True)} for ln in priced],
                                    _with_rounding(svc, priced_rest if rest else priced, (rec - svc_total - own_total) if rest else rec - svc_total), pay, below_cost_reason)
    if not ok and before:
        _undo_take_ins(before, msg)                              # the surplus never came in: no ghost stock, and a retry does not double it
    return ok, msg, sale_id


def _undo_take_ins(before: dict, why: str) -> None:
    """The surplus items go back to what they were before this sale's take-ins (on hand and average cost), with a ledger row and an audit line."""
    from engines.clock import manila_today
    from engines.common import write_tx
    with write_tx() as conn:
        for item_id, row in before.items():
            if not row:
                continue
            now = conn.execute("SELECT on_hand, avg_cost FROM items WHERE id = ?", (item_id,)).fetchone()
            diff = float(now[0] or 0) - float(row[0] or 0)
            if abs(diff) < 1e-9:
                continue
            from engines.stock_ledger import stock_back
            if diff > 0:                                     # the take-in's batch leaves again (FIFO, 2026-10-17)
                stock_back(conn, item_id, diff, 0, "take_in", item_id, manila_today(), note=f"take-in undone: {why}")
            else:
                conn.execute("UPDATE items SET on_hand = ? WHERE id = ?", (row[0], item_id))
            conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'STOCK_TAKE_IN_UNDONE', 'items', ?, ?)",
                         (item_id, f"{diff:g} taken in for a sale that was not saved, put back: {why}"))
