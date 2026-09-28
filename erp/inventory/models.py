"""Items and stock (Sales step S1, 2026-09-29). Owner's choices: items by description (category + brand + size or model + an optional
remark; codes made by the system); stock on hand from day one; weighted-average cost recalculated at every stock in; a sale above the
stock is allowed and flagged; corrections only by an owner adjustment with a reason; services are not tracked.

The stock ledger (StockMove) is only ever added to -- nothing edits or deletes a movement; a mistake is reversed by a new movement.
Item.on_hand / Item.avg_cost are the running totals of that ledger, updated in the same locked save (inventory/stock.py)."""
from django.db import models

from core.clock import now_aware
from core.models import Party


class Category(models.TextChoices):
    TIRE = "tire", "Tire"
    BATTERY = "battery", "Battery"
    MAGS = "mags", "Mags"


CODE_PREFIX = {Category.TIRE: "TR", Category.BATTERY: "BA", Category.MAGS: "MG"}
SIZE_LABEL = {Category.TIRE: "Size", Category.BATTERY: "Model", Category.MAGS: "Model"}


class Item(models.Model):
    code = models.CharField(max_length=10, unique=True)                  # TR-0001 / BA-0001 / MG-0001
    category = models.CharField(max_length=10, choices=Category.choices)
    brand = models.CharField(max_length=60)
    size_model = models.CharField(max_length=60)                         # tires: size; batteries / mags: model or type
    remark = models.CharField(max_length=120, blank=True)                # e.g. "old stock 2023" -- also tells two rows apart
    key = models.CharField(max_length=260, unique=True)                  # category|brand|size|remark, lower-case, single spaces
    reorder_level = models.PositiveIntegerField(default=2)               # low stock at or below this (owner's default 2)
    on_hand = models.IntegerField(default=0)                             # may go below zero (a sale is never blocked; flagged)
    avg_cost = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=now_aware)
    created_by = models.CharField(max_length=254)

    class Meta:
        ordering = ["category", "brand", "size_model", "remark"]

    def __str__(self):
        return self.description

    @property
    def description(self) -> str:
        return f"{self.brand} {self.size_model}" + (f" ({self.remark})" if self.remark else "")

    @property
    def is_low(self) -> bool:
        return 0 <= self.on_hand <= self.reorder_level

    @property
    def is_negative(self) -> bool:
        return self.on_hand < 0


class StockIn(models.Model):
    """A delivery received ("receiving report"): which supplier, when, their DR / invoice no., and the lines (StockMove kind stock_in)."""
    number = models.CharField(max_length=20, unique=True)                # 2026 - RR 0001
    supplier = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="stock_ins")
    received_on = models.DateField()
    supplier_ref = models.CharField(max_length=60, blank=True)
    total = models.DecimalField(max_digits=14, decimal_places=2)
    created_at = models.DateTimeField(default=now_aware)
    created_by = models.CharField(max_length=254)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.CharField(max_length=254, blank=True)
    reverse_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-received_on", "-id"]


class MoveKind(models.TextChoices):
    OPENING = "opening", "Opening stock"
    STOCK_IN = "stock_in", "Stock in"
    STOCK_IN_REVERSAL = "stock_in_reversal", "Stock in reversed"
    ADJUSTMENT = "adjustment", "Adjustment (count)"
    SALE = "sale", "Sale"                                                # Sales S2
    SALE_REVERSAL = "sale_reversal", "Sale cancelled / returned"         # Sales S2 / S3


class StockMove(models.Model):
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="moves")
    kind = models.CharField(max_length=20, choices=MoveKind.choices)
    qty = models.IntegerField()                                          # + in, - out
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4)     # cost of the pieces moving
    on_hand_after = models.IntegerField()
    avg_after = models.DecimalField(max_digits=14, decimal_places=4)
    moved_on = models.DateField()                                        # business date (Manila)
    stock_in = models.ForeignKey(StockIn, on_delete=models.PROTECT, null=True, blank=True, related_name="moves")
    reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(default=now_aware)
    created_by = models.CharField(max_length=254)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.CheckConstraint(condition=~models.Q(qty=0), name="inventory_move_qty_not_zero")]


class ImportStatus(models.TextChoices):
    PENDING = "pending", "Waiting for the owner"
    CONFIRMED = "confirmed", "Confirmed"
    DISCARDED = "discarded", "Discarded"


class OpeningImport(models.Model):
    """The opening price list typed into the template by Sales, checked, then confirmed by the owner (owner's choice)."""
    status = models.CharField(max_length=10, choices=ImportStatus.choices, default=ImportStatus.PENDING)
    file_name = models.CharField(max_length=200)
    rows = models.JSONField()                                            # [{category, brand, size_model, remark, qty, cost}]
    items_count = models.PositiveIntegerField()
    pieces = models.IntegerField()
    value = models.DecimalField(max_digits=16, decimal_places=2)
    uploaded_by = models.CharField(max_length=254)
    uploaded_at = models.DateTimeField(default=now_aware)
    decided_by = models.CharField(max_length=254, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-uploaded_at"]
