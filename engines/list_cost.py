"""The price-list cost of an item that has no delivery cost yet (owner, 2026-10-18: "if you can detect the cost from pricelist then use it"): the supplier's
list price in force today less the standing discount chain. With several suppliers, the lowest net. Read in one go for the item picker."""
from __future__ import annotations

from datetime import date

from engines.clock import manila_today
from engines.common import get_conn


def list_costs(item_ids: list[int] | None = None, on: date | None = None) -> dict[int, float]:
    """{item_id: net price} for the items that have an active list price on the date (all items when item_ids is None)."""
    from engines.discount_rules import chain_from, discount_rows, net_price
    day = str(on or manila_today())
    where, args = "", [day]
    if item_ids is not None:
        ids = [int(i) for i in item_ids if i]
        if not ids:
            return {}
        where = f" AND p.item_id IN ({','.join('?' for _ in ids)})"
        args += ids
    with get_conn() as conn:
        rows = conn.execute(f"""SELECT p.creditor_id, p.item_id, p.item_name, p.unit_price FROM supplier_price_list p
                                 WHERE p.is_active = 1 AND p.item_id IS NOT NULL AND p.effective_date <= ?{where}
                                 ORDER BY p.creditor_id, p.item_id, p.effective_date DESC, p.price_id DESC""", args).fetchall()
    newest, seen = [], set()
    for r in rows:                                               # the price in force: the newest row per supplier and item
        if (r[0], r[1]) not in seen:
            seen.add((r[0], r[1]))
            newest.append(r)
    rows = newest
    chains: dict[int, list] = {}
    out: dict[int, float] = {}
    for cid, iid, name, price in rows:
        if cid not in chains:
            chains[cid] = discount_rows(cid, on)
        net = round(net_price(float(price or 0), chain_from(chains[cid], name)), 2)
        if net > 0 and (iid not in out or net < out[iid]):
            out[int(iid)] = net
    return out


def list_cost(item_id: int, on: date | None = None) -> float:
    return list_costs([item_id], on).get(int(item_id), 0.0)
