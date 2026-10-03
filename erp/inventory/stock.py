"""Stock rules (Sales step S1). Every change is ONE locked save (transaction.atomic with SQLite's IMMEDIATE lock) that adds a StockMove
and updates the item's running totals, and writes an audit row. Nothing edits or deletes a movement.

Weighted-average cost (owner's choice):
  * stock in / opening:   new avg = (on hand x avg + qty x cost) / (on hand + qty); when nothing (or less than nothing) is on hand,
                          the new avg is simply the delivery's cost;
  * a stock in reversed:  the pieces leave at THEIR cost: (on hand x avg - qty x cost) / (on hand - qty) while something is left;
  * sales / adjustments:  pieces leave or return at the current average -- the average does not change."""
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db import connection, transaction

from core import audit
from core.clock import now_aware
from core.numbering import next_number
from engines import product_catalog as catalog
from engines.acting_user import acting_user
from engines.clock import manila_today
from inventory.models import CODE_PREFIX, Category, ImportStatus, Item, MoveKind, OpeningImport, StockIn, StockMove

FOUR = Decimal("0.0001")
TWO = Decimal("0.01")


class StockError(ValueError):
    """A rule was broken; the message is shown to the person as it is."""


def item_key(category: str, brand: str, size_model: str, remark: str = "") -> str:
    """The item's identity (engines/product_catalog.product_key): category, brand, the headline field (size, or the model of a battery) and the remark."""
    label = catalog.label_of(category)
    return catalog.product_key(label, brand, remark, **{catalog.HEADLINE[label]: size_model})


def _money(v) -> Decimal:
    try:
        d = Decimal(str(v).replace(",", "").strip())
    except Exception:
        raise StockError(f"{v!r} is not an amount.")
    if d < 0:
        raise StockError("An amount can't be negative.")
    return d.quantize(FOUR, ROUND_HALF_UP)


def _qty(v) -> int:
    try:
        q = Decimal(str(v).replace(",", "").strip())
    except Exception:
        raise StockError(f"{v!r} is not a quantity.")
    if q != q.to_integral_value():
        raise StockError("Quantities are whole pieces.")
    return int(q)


def add_item(category: str, brand: str, size_model: str, remark: str = "", reorder_level: int = 2, **fields) -> Item:
    """Add one item through the shared item rules (engines/product_catalog.get_or_create_product: the code, the identity key, the standard name). `fields` = the
    category's other standard fields (pattern, terrain, pcd ...); a plain add (brand + size or model) works as before."""
    if category not in Category.values:
        raise StockError("Pick Tire, Battery, Mags or Rims.")
    brand, size_model, remark = (" ".join((v or "").split()) for v in (brand, size_model, remark))
    if not brand or not size_model:
        raise StockError("Brand and size (or model) are both needed.")
    with transaction.atomic():
        item_id, _, created = catalog.get_or_create_product(category, brand, conn=connection.connection, remark=remark, size_model=size_model,
                                                          reorder_level=reorder_level, **fields)       # on Django's own connection, inside its locked save
        item = Item.objects.get(pk=item_id)
        if not created:
            raise StockError(f"{item.code} {item.description} is already on the list. Add a remark if this is a different lot.")
    return item


def _new_average(item: Item, qty: int, cost: Decimal) -> Decimal:
    if qty > 0:
        if item.on_hand <= 0:
            return cost
        return ((item.on_hand * item.avg_cost + qty * cost) / (item.on_hand + qty)).quantize(FOUR, ROUND_HALF_UP)
    left = item.on_hand + qty
    if left > 0:
        avg = ((item.on_hand * item.avg_cost + qty * cost) / left).quantize(FOUR, ROUND_HALF_UP)
        return avg if avg >= 0 else item.avg_cost
    return item.avg_cost


def _move(item: Item, kind: str, qty: int, unit_cost: Decimal, moved_on: date, stock_in=None, reason: str = "",
          changes_average: bool = False) -> StockMove:
    """Add one movement and update the item's totals. Call inside transaction.atomic()."""
    item.refresh_from_db()
    avg = _new_average(item, qty, unit_cost) if changes_average else item.avg_cost
    item.on_hand += qty
    item.avg_cost = avg
    item.save(update_fields=["on_hand", "avg_cost"])
    return StockMove.objects.create(item=item, kind=kind, qty=qty, unit_cost=unit_cost, on_hand_after=item.on_hand, avg_after=avg,
                                    moved_on=moved_on, stock_in=stock_in, reason=reason, created_by=acting_user())


# ---- stock in -------------------------------------------------------------------------------------------------------------------

def check_stock_in(supplier, received_on: date, lines: list[tuple[Item, object, object]]) -> list[dict]:
    """Validate a delivery and show, per line, on hand and average before -> after (nothing saved)."""
    if supplier is None:
        raise StockError("Pick the supplier.")
    if received_on is None:
        raise StockError("Give the date the delivery came in.")
    if received_on > manila_today():
        raise StockError("The date received can't be in the future.")
    if not lines:
        raise StockError("Add at least one item.")
    seen, preview = set(), []
    for item, qty, cost in lines:
        if item.pk in seen:
            raise StockError(f"{item.code} is on two lines -- put the total on one line.")
        seen.add(item.pk)
        if not item.is_active:
            raise StockError(f"{item.code} is deactivated.")
        q, c = _qty(qty), _money(cost)
        if q <= 0:
            raise StockError(f"{item.code}: the quantity must be at least 1.")
        preview.append({"item": item, "qty": q, "cost": c, "amount": (q * c).quantize(TWO, ROUND_HALF_UP),
                        "on_hand": item.on_hand, "on_hand_after": item.on_hand + q,
                        "avg": item.avg_cost, "avg_after": _new_average(item, q, c)})
    return preview


def record_stock_in(supplier, received_on: date, supplier_ref: str, lines) -> StockIn:
    with transaction.atomic():
        fresh = [(Item.objects.get(pk=i.pk), q, c) for i, q, c in lines]        # re-read inside the lock
        preview = check_stock_in(supplier, received_on, fresh)
        total = sum((p["amount"] for p in preview), Decimal("0"))
        doc = StockIn.objects.create(number=next_number("RR", received_on.year), supplier=supplier, received_on=received_on,
                                     supplier_ref=(supplier_ref or "").strip(), total=total, created_by=acting_user())
        for p in preview:
            _move(p["item"], MoveKind.STOCK_IN, p["qty"], p["cost"], received_on, stock_in=doc, changes_average=True)
        audit.log("INV_STOCK_IN", "inventory_stockin", doc.pk,
                  new=f"{doc.number} from {supplier.name} ({doc.supplier_ref or 'no ref'}): {len(preview)} line(s), PHP {total:,.2f}")
    return doc


def reverse_stock_in(doc: StockIn, reason: str) -> None:
    """Owner: a stock in entered by mistake is reversed (never edited or deleted); each line leaves at its own cost."""
    reason = (reason or "").strip()
    if not reason:
        raise StockError("Say why the stock in is reversed.")
    with transaction.atomic():
        doc = StockIn.objects.get(pk=doc.pk)
        if doc.reversed_at:
            raise StockError(f"{doc.number} is already reversed.")
        for m in doc.moves.filter(kind=MoveKind.STOCK_IN).select_related("item"):
            _move(m.item, MoveKind.STOCK_IN_REVERSAL, -m.qty, m.unit_cost, manila_today(), stock_in=doc, reason=reason,
                  changes_average=True)
        doc.reversed_at, doc.reversed_by, doc.reverse_reason = now_aware(), acting_user(), reason
        doc.save(update_fields=["reversed_at", "reversed_by", "reverse_reason"])
        audit.log("INV_STOCK_IN_REVERSED", "inventory_stockin", doc.pk, old=f"{doc.number} PHP {doc.total:,.2f}", new=reason)


# ---- owner adjustment -----------------------------------------------------------------------------------------------------------

def adjust_to_count(item: Item, counted, reason: str) -> StockMove | None:
    """Owner: set the stock to the counted quantity; the difference is recorded at the current average (the average stays)."""
    reason = (reason or "").strip()
    if not reason:
        raise StockError("Give a reason, e.g. 'count Sep 30' or 'damaged'.")
    c = _qty(counted)
    if c < 0:
        raise StockError("A count can't be below zero.")
    with transaction.atomic():
        item = Item.objects.get(pk=item.pk)
        diff = c - item.on_hand
        if diff == 0:
            return None
        before = item.on_hand
        move = _move(item, MoveKind.ADJUSTMENT, diff, item.avg_cost, manila_today(), reason=reason)
        audit.log("INV_ADJUSTED", "inventory_item", item.pk, old=f"{item.code} on hand {before}",
                  new=f"counted {c} ({diff:+d}): {reason}")
    return move


# ---- opening list ---------------------------------------------------------------------------------------------------------------

def confirm_opening(imp: OpeningImport) -> dict:
    """Owner: the checked opening list becomes items + opening stock. Items that already have movements are left alone (listed)."""
    with transaction.atomic():
        imp = OpeningImport.objects.get(pk=imp.pk)
        if imp.status != ImportStatus.PENDING:
            raise StockError("This list was already decided.")
        created, opened, skipped = 0, 0, []
        today = manila_today()
        for r in imp.rows:
            key = item_key(r["category"], r["brand"], r["size_model"], r.get("remark", ""))
            item = Item.objects.filter(key=key).first()
            if item is None:
                item_id, _, _ = catalog.get_or_create_product(r["category"], r["brand"], conn=connection.connection, remark=r.get("remark", ""), size_model=r["size_model"])
                item = Item.objects.get(pk=item_id)
                created += 1
            elif item.moves.exists():
                skipped.append(f"{item.code} {item.description}")
                continue
            if int(r["qty"]) != 0:
                _move(item, MoveKind.OPENING, int(r["qty"]), Decimal(r["cost"]), today, reason="opening list", changes_average=True)
            else:
                item.avg_cost = Decimal(r["cost"])
                item.save(update_fields=["avg_cost"])
            opened += 1
        imp.status, imp.decided_by, imp.decided_at = ImportStatus.CONFIRMED, acting_user(), now_aware()
        imp.note = f"{created} new item(s), {opened} opened" + (f", {len(skipped)} already had stock (left alone)" if skipped else "")
        imp.save(update_fields=["status", "decided_by", "decided_at", "note"])
        audit.log("INV_OPENING_CONFIRMED", "inventory_openingimport", imp.pk,
                  new=f"{imp.file_name}: {imp.items_count} rows, {imp.pieces} pcs, PHP {imp.value:,.2f}; {imp.note}")
    return {"created": created, "opened": opened, "skipped": skipped}


def discard_opening(imp: OpeningImport, reason: str = "") -> None:
    with transaction.atomic():
        imp = OpeningImport.objects.get(pk=imp.pk)
        if imp.status != ImportStatus.PENDING:
            raise StockError("This list was already decided.")
        imp.status, imp.decided_by, imp.decided_at, imp.note = ImportStatus.DISCARDED, acting_user(), now_aware(), (reason or "")[:300]
        imp.save(update_fields=["status", "decided_by", "decided_at", "note"])
        audit.log("INV_OPENING_DISCARDED", "inventory_openingimport", imp.pk, new=f"{imp.file_name}: {reason}")
