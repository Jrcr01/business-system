"""Sales rules (S2). Every save is ONE locked step (transaction.atomic with SQLite's IMMEDIATE lock) and is audited. Nothing is deleted.

Booklets: the oldest booklet that is not finished is the one IN USE; only its numbers can be used (one booklet at a time -- owner).
A booklet finishes by itself when every number is used or cancelled; the owner can close it early (its unused numbers are then
cancelled with the owner's note). A GAP = a number below the highest one used in a booklet that is neither used nor cancelled
(flagged for the owner, never blocking -- owner). A sale takes its pieces out of stock at the item's average cost (inventory/stock.py);
stock may go below zero (allowed and flagged -- owner)."""
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import IntegrityError, transaction

from core import audit
from core.clock import now_aware
from core.models import Party
from engines.acting_user import acting_user
from engines.clock import manila_today
from inventory import stock as inv
from inventory.models import Item, MoveKind
from sales.models import CancelledNumber, DayUpload, InvoiceBooklet, PaymentType, Sale, SaleLine, SaleSource

TWO = Decimal("0.01")


class SaleError(ValueError):
    """A rule was broken; the message is shown to the person as it is."""


# ---- booklets -----------------------------------------------------------------------------------------------------------------------

def current_booklet() -> InvoiceBooklet | None:
    return InvoiceBooklet.objects.filter(finished_at__isnull=True).order_by("first_number").first()


def booklet_of(number: int) -> InvoiceBooklet | None:
    return InvoiceBooklet.objects.filter(first_number__lte=number, last_number__gte=number).first()


def used_numbers(booklet: InvoiceBooklet) -> set[int]:
    return set(booklet.sales.values_list("number", flat=True))


def cancelled_numbers(booklet: InvoiceBooklet) -> set[int]:
    return set(booklet.cancelled.values_list("number", flat=True))


def unused_numbers(booklet: InvoiceBooklet) -> list[int]:
    taken = used_numbers(booklet) | cancelled_numbers(booklet)
    return [n for n in range(booklet.first_number, booklet.last_number + 1) if n not in taken]


def gaps(booklet: InvoiceBooklet | None = None) -> list[tuple[InvoiceBooklet, int]]:
    """Numbers skipped: below the highest used number of their booklet, neither used nor cancelled."""
    out = []
    for b in [booklet] if booklet else InvoiceBooklet.objects.all():
        used = used_numbers(b)
        if not used:
            continue
        taken = used | cancelled_numbers(b)
        out += [(b, n) for n in range(b.first_number, max(used)) if n not in taken]
    return out


def _finish_if_complete(b: InvoiceBooklet) -> None:
    if b.finished_at is None and not unused_numbers(b):
        b.finished_at = now_aware()
        b.finished_note = b.finished_note or "every number used or cancelled"
        b.save(update_fields=["finished_at", "finished_note"])
        audit.log("SALES_BOOKLET_FINISHED", "sales_invoicebooklet", b.pk, new=f"{b.label}: all numbers accounted for")


def register_booklet(label: str, first, last, width=None) -> InvoiceBooklet:
    label = " ".join((label or "").split())
    try:
        first_n, last_n = int(str(first).strip()), int(str(last).strip())
    except ValueError:
        raise SaleError("The first and last numbers must be numbers, e.g. 0301 and 0350.")
    if not label:
        raise SaleError("Give the booklet a label, e.g. SI-07 (as written on it).")
    if first_n <= 0 or last_n < first_n:
        raise SaleError("The last number must be the same as or after the first number.")
    if last_n - first_n + 1 > 500:
        raise SaleError("That is more than 500 invoices -- check the numbers (a booklet usually has 50).")
    width = int(width or max(len(str(first).strip()), len(str(last).strip()), 1))
    with transaction.atomic():
        clash = InvoiceBooklet.objects.filter(first_number__lte=last_n, last_number__gte=first_n).first()
        if clash:
            raise SaleError(f"Those numbers overlap {clash}.")
        if Sale.objects.filter(number__gte=first_n, number__lte=last_n).exists():
            raise SaleError("Some of those numbers are already on recorded sales.")
        try:
            b = InvoiceBooklet.objects.create(label=label, first_number=first_n, last_number=last_n, width=width,
                                              registered_by=acting_user())
        except IntegrityError:
            raise SaleError(f"A booklet called {label} is already registered.")
        audit.log("SALES_BOOKLET_REGISTERED", "sales_invoicebooklet", b.pk, new=f"{b} ({b.size} invoices)")
    return b


def cancel_number(number, reason: str) -> CancelledNumber:
    """A spoiled / cancelled leaf of the booklet in use (Sales or Owner), with a reason."""
    reason = (reason or "").strip()
    if not reason:
        raise SaleError("Say why the invoice number is cancelled, e.g. 'spoiled, wrong amount written'.")
    n = _number(number)
    with transaction.atomic():
        b = _booklet_in_use_for(n)
        if Sale.objects.filter(number=n).exists():
            raise SaleError(f"{b.fmt(n)} is on a recorded sale -- cancelling a sale needs the owner's approval (S3).")
        if CancelledNumber.objects.filter(number=n).exists():
            raise SaleError(f"{b.fmt(n)} is already cancelled.")
        c = CancelledNumber.objects.create(booklet=b, number=n, reason=reason, cancelled_by=acting_user())
        audit.log("SALES_NUMBER_CANCELLED", "sales_invoicebooklet", b.pk, new=f"{b.fmt(n)}: {reason}")
        _finish_if_complete(b)
    return c


def close_booklet(b: InvoiceBooklet, note: str) -> int:
    """Owner: finish a booklet early; its unused numbers are cancelled with the note. Returns how many were cancelled."""
    note = (note or "").strip()
    if not note:
        raise SaleError("Say why the booklet is closed early.")
    with transaction.atomic():
        b = InvoiceBooklet.objects.get(pk=b.pk)
        if b.finished_at:
            raise SaleError(f"{b.label} is already finished.")
        left = unused_numbers(b)
        for n in left:
            CancelledNumber.objects.create(booklet=b, number=n, reason=f"booklet closed: {note}", cancelled_by=acting_user())
        b.finished_at, b.finished_note = now_aware(), f"closed early: {note}"
        b.save(update_fields=["finished_at", "finished_note"])
        audit.log("SALES_BOOKLET_CLOSED", "sales_invoicebooklet", b.pk, new=f"{b.label}: {len(left)} unused number(s) cancelled -- {note}")
    return len(left)


def _number(v) -> int:
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        raise SaleError(f"{v!r} is not an invoice number.")
    if n <= 0:
        raise SaleError(f"{v!r} is not an invoice number.")
    return n


def _booklet_in_use_for(n: int) -> InvoiceBooklet:
    b = booklet_of(n)
    if b is None:
        raise SaleError(f"Invoice {n} is not in any registered booklet -- the owner registers each booklet first.")
    cur = current_booklet()
    if b.finished_at:
        raise SaleError(f"Invoice {b.fmt(n)} is in {b.label}, which is finished.")
    if cur and b.pk != cur.pk:
        raise SaleError(f"Invoice {b.fmt(n)} is in {b.label}, but {cur.label} is the booklet in use -- finish it (or the owner closes it) first.")
    return b


# ---- a sale -------------------------------------------------------------------------------------------------------------------------

@dataclass
class SaleLineInput:
    item: Item
    qty: object
    price: object
    below_cost_reason: str = ""


@dataclass
class SaleInput:
    number: object
    sale_date: date
    customer: Party
    payment_type: str
    lines: list = field(default_factory=list)
    customer_note: str = ""
    check_bank: str = ""
    check_number: str = ""
    check_date: date | None = None
    payment_ref: str = ""


def _money(v) -> Decimal:
    try:
        d = Decimal(str(v).replace(",", "").replace("₱", "").strip())
    except Exception:
        raise SaleError(f"{v!r} is not a price.")
    if d < 0:
        raise SaleError("A price can't be negative.")
    return d.quantize(TWO, ROUND_HALF_UP)


def check_sale(s: SaleInput) -> dict:
    """Validate one sale and work out its lines (nothing saved). Returns {'number', 'booklet', 'lines', 'total', 'profit',
    'due_date', 'warnings'}; raises SaleError for anything that must be fixed."""
    n = _number(s.number)
    b = _booklet_in_use_for(n)
    done = Sale.objects.filter(number=n).first()
    if done:
        raise SaleError(f"Invoice {b.fmt(n)} was already recorded on {done.sale_date:%b %d, %Y}.")
    if CancelledNumber.objects.filter(number=n).exists():
        raise SaleError(f"Invoice {b.fmt(n)} is cancelled -- use another number.")
    if s.sale_date is None:
        raise SaleError(f"Invoice {b.fmt(n)}: give the date.")
    if s.sale_date > manila_today():
        raise SaleError(f"Invoice {b.fmt(n)}: the date can't be in the future.")
    c = s.customer
    if c is None:
        raise SaleError(f"Invoice {b.fmt(n)}: pick the customer (Walk-in customer for cash sales).")
    if not c.is_active or not (c.is_customer or c.is_affiliate):
        raise SaleError(f"Invoice {b.fmt(n)}: {c.name} is not an active customer.")
    if s.payment_type not in PaymentType.values:
        raise SaleError(f"Invoice {b.fmt(n)}: pick how it was paid.")
    due = None
    if s.payment_type == PaymentType.CHARGE:
        if c.is_walk_in or not c.credit_allowed:
            raise SaleError(f"Invoice {b.fmt(n)}: {c.name} is not allowed credit (the owner turns it on).")
        due = s.sale_date + timedelta(days=c.terms_days or 0)
    if s.payment_type == PaymentType.CHECK:
        if not (s.check_bank or "").strip() or not (s.check_number or "").strip() or s.check_date is None:
            raise SaleError(f"Invoice {b.fmt(n)}: a check payment needs the bank, check number and check date.")
    if s.payment_type in (PaymentType.EWALLET, PaymentType.TRANSFER, PaymentType.CARD) and not (s.payment_ref or "").strip():
        what = "approval code" if s.payment_type == PaymentType.CARD else "reference number"
        raise SaleError(f"Invoice {b.fmt(n)}: give the {what}.")
    if not s.lines:
        raise SaleError(f"Invoice {b.fmt(n)}: add at least one item.")
    lines, seen, warnings = [], set(), []
    for ln in s.lines:
        item = ln.item
        if item is None:
            raise SaleError(f"Invoice {b.fmt(n)}: pick each item from the list.")
        if item.pk in seen:
            raise SaleError(f"Invoice {b.fmt(n)}: {item.code} is on two lines -- put the total on one line.")
        seen.add(item.pk)
        if not item.is_active:
            raise SaleError(f"Invoice {b.fmt(n)}: {item.code} is deactivated.")
        try:
            q = Decimal(str(ln.qty).replace(",", "").strip())
        except Exception:
            raise SaleError(f"Invoice {b.fmt(n)}: {item.code} -- the quantity is not a number.")
        if q != q.to_integral_value() or q < 1:
            raise SaleError(f"Invoice {b.fmt(n)}: {item.code} -- the quantity must be whole pieces, at least 1.")
        q = int(q)
        price = _money(ln.price)
        cost = item.avg_cost
        amount = (q * price).quantize(TWO, ROUND_HALF_UP)
        profit = (amount - q * cost).quantize(TWO, ROUND_HALF_UP)
        below = price < cost.quantize(TWO, ROUND_HALF_UP)
        if below:
            warnings.append(f"{b.fmt(n)} {item.code}: PHP {price:,.2f} is below cost PHP {cost:,.2f} -- goes to the owner's review")
        if item.on_hand - q < 0:
            warnings.append(f"{b.fmt(n)} {item.code}: only {item.on_hand} on hand -- stock goes below zero (flagged)")
        lines.append({"item": item, "qty": q, "price": price, "cost": cost, "amount": amount, "profit": profit, "below": below,
                      "reason": (ln.below_cost_reason or "").strip()[:200] if below else "", "on_hand_after": item.on_hand - q})
    total = sum((x["amount"] for x in lines), Decimal("0"))
    return {"number": n, "booklet": b, "lines": lines, "total": total, "profit": sum((x["profit"] for x in lines), Decimal("0")),
            "cost_total": sum(((x["qty"] * x["cost"]).quantize(TWO, ROUND_HALF_UP) for x in lines), Decimal("0")),
            "due_date": due, "warnings": warnings}


def record_sale(s: SaleInput, source: str = SaleSource.SCREEN, upload: DayUpload | None = None) -> Sale:
    """Save one sale: re-checked inside the lock, lines saved, pieces out of stock at the average cost, audited."""
    with transaction.atomic():
        s.lines = [SaleLineInput(Item.objects.get(pk=ln.item.pk) if ln.item else None, ln.qty, ln.price, ln.below_cost_reason)
                   for ln in s.lines]
        chk = check_sale(s)
        sale = Sale.objects.create(
            booklet=chk["booklet"], number=chk["number"], sale_date=s.sale_date, customer=s.customer,
            customer_note=(s.customer_note or "").strip()[:120], payment_type=s.payment_type,
            check_bank=(s.check_bank or "").strip() if s.payment_type == PaymentType.CHECK else "",
            check_number=(s.check_number or "").strip() if s.payment_type == PaymentType.CHECK else "",
            check_date=s.check_date if s.payment_type == PaymentType.CHECK else None,
            payment_ref=(s.payment_ref or "").strip() if s.payment_type not in (PaymentType.CASH, PaymentType.CHECK, PaymentType.CHARGE) else "",
            due_date=chk["due_date"], total=chk["total"], cost_total=chk["cost_total"], profit=chk["profit"],
            source=source, upload=upload, created_by=acting_user())
        for x in chk["lines"]:
            move = inv._move(x["item"], MoveKind.SALE, -x["qty"], x["cost"], s.sale_date,
                             reason=f"invoice {chk['booklet'].fmt(chk['number'])}")
            SaleLine.objects.create(sale=sale, item=x["item"], qty=x["qty"], price=x["price"], unit_cost=x["cost"], amount=x["amount"],
                                    profit=x["profit"], below_cost=x["below"], below_cost_reason=x["reason"], stock_move=move)
        audit.log("SALE_RECORDED", "sales_sale", sale.pk,
                  new=f"{sale.number_text} {sale.sale_date} {s.customer.name} {sale.get_payment_type_display()} PHP {sale.total:,.2f} "
                      f"({len(chk['lines'])} line(s), profit PHP {sale.profit:,.2f})" + (f" [{source}]" if source else ""))
        _finish_if_complete(chk["booklet"])
    return sale


def mark_reviewed(line: SaleLine) -> None:
    """Owner: a below-cost line was looked at."""
    with transaction.atomic():
        line = SaleLine.objects.select_for_update().get(pk=line.pk)
        if line.reviewed_at:
            return
        line.reviewed_at, line.reviewed_by = now_aware(), acting_user()
        line.save(update_fields=["reviewed_at", "reviewed_by"])
        audit.log("SALE_BELOW_COST_REVIEWED", "sales_sale", line.sale_id,
                  new=f"{line.sale.number_text} {line.item.code} PHP {line.price:,.2f} (cost {line.unit_cost:,.2f})")
