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
        price = round(amount / qty, 2) if qty else 0.0
        given = round(given + round(price * qty, 2), 2)
        out.append({**ln, "price": f"{price:.2f}"})
    return out


def costs_of(item_ids: list[int]) -> dict[int, float]:
    if not item_ids:
        return {}
    marks = ",".join("?" for _ in item_ids)
    with get_conn() as conn:
        return {int(r[0]): float(r[1] or 0) for r in conn.execute(f"SELECT id, avg_cost FROM items WHERE id IN ({marks})", [int(i) for i in item_ids])}


def record(si_no, sale_date, customer_id, customer_name: str, lines: list[dict], receipt, services: list[dict], payment: dict,
           release_id: int | None = None, below_cost_reason: str = "") -> tuple[bool, str, int | None]:
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
        ok, msg = sr.invoice_release(int(release_id), si_no, sale_date, {ln["line_id"]: ln["price"] for ln in priced}, svc, pay, below_cost_reason)
        return ok, msg, int(release_id) if ok else None
    clean = [{"item_id": int(ln["item_id"]), "qty": _num(ln.get("qty"))} for ln in lines if str(ln.get("item_id", "")).isdigit() and _num(ln.get("qty")) > 0]
    if not clean:
        return False, "Scan or pick what was sold.", None
    costs = costs_of([ln["item_id"] for ln in clean])
    priced = spread([{**ln, "cost": costs.get(ln["item_id"], 0.0)} for ln in clean], rec, svc_total)
    return sr.save_sale(si_no, sale_date, customer_id, customer_name, [{"item_id": ln["item_id"], "qty": ln["qty"], "price": ln["price"]} for ln in priced],
                        svc, pay, below_cost_reason)
