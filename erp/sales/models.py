"""Sales (module 2), step S2 (2026-09-29). Owner's choices (S0, blueprint section 9): the ERP RECORDS the BIR sales invoices written
from pre-printed booklets (50 per booklet, triplicate) -- it never prints them; one booklet in use at a time; every number used
once; a spoiled / cancelled paper invoice is logged with a reason; a daily gap check lists numbers skipped and not accounted for
(flagged, never blocking); prices are agreed per sale and recorded against the cost (average cost at the moment of the sale);
below-cost sales are allowed and go to the owner's review; check payments need bank, check no. and check date.
S2 defaults (owner said "continue as far as you can", listed for confirmation): one payment type per invoice; booklets registered
and closed by the owner; Sales marks a spoiled leaf cancelled with a reason."""
from django.db import models

from core.clock import now_aware
from core.models import Party
from inventory.models import Item, StockMove


class BookletStatus(models.TextChoices):
    WAITING = "waiting", "Registered"
    IN_USE = "in_use", "In use"
    FINISHED = "finished", "Finished"


class InvoiceBooklet(models.Model):
    """One pre-printed BIR booklet: its number range. The oldest booklet that is not finished is the one in use."""
    label = models.CharField(max_length=30, unique=True)                # what is written on it, e.g. SI-07
    first_number = models.PositiveIntegerField()
    last_number = models.PositiveIntegerField()
    width = models.PositiveSmallIntegerField(default=4)                 # digits as printed, e.g. 4 -> 0301
    finished_at = models.DateTimeField(null=True, blank=True)
    finished_note = models.CharField(max_length=200, blank=True)
    registered_at = models.DateTimeField(default=now_aware)
    registered_by = models.CharField(max_length=254)

    class Meta:
        ordering = ["first_number"]
        constraints = [models.CheckConstraint(condition=models.Q(last_number__gte=models.F("first_number")), name="sales_booklet_range")]

    def __str__(self):
        return f"{self.label} ({self.fmt(self.first_number)}–{self.fmt(self.last_number)})"

    def fmt(self, n: int) -> str:
        return str(n).zfill(self.width)

    @property
    def first_text(self) -> str:
        return self.fmt(self.first_number)

    @property
    def last_text(self) -> str:
        return self.fmt(self.last_number)

    @property
    def size(self) -> int:
        return self.last_number - self.first_number + 1


class CancelledNumber(models.Model):
    """A booklet leaf that will never carry a sale: spoiled, cancelled before use, or left when a booklet was closed."""
    booklet = models.ForeignKey(InvoiceBooklet, on_delete=models.PROTECT, related_name="cancelled")
    number = models.PositiveIntegerField(unique=True)
    reason = models.CharField(max_length=200)
    cancelled_at = models.DateTimeField(default=now_aware)
    cancelled_by = models.CharField(max_length=254)


class PaymentType(models.TextChoices):
    CASH = "cash", "Cash"
    CHECK = "check", "Check"
    EWALLET = "ewallet", "GCash / Maya"
    TRANSFER = "transfer", "Bank transfer"
    CARD = "card", "Card"
    CHARGE = "charge", "Charge (credit)"


class SaleSource(models.TextChoices):
    SCREEN = "screen", "Quick sale screen"
    UPLOAD = "upload", "Excel day sheet"


class DayUpload(models.Model):
    """One accepted Excel day sheet (all its sales were saved together)."""
    file_name = models.CharField(max_length=200)
    sale_date = models.DateField()
    sales_count = models.PositiveIntegerField()
    total = models.DecimalField(max_digits=14, decimal_places=2)
    profit = models.DecimalField(max_digits=14, decimal_places=2)
    uploaded_at = models.DateTimeField(default=now_aware)
    uploaded_by = models.CharField(max_length=254)


class Sale(models.Model):
    """One sales invoice from the booklet. Never deleted; cancellations and returns come in S3 (with the owner's approval)."""
    booklet = models.ForeignKey(InvoiceBooklet, on_delete=models.PROTECT, related_name="sales")
    number = models.PositiveIntegerField(unique=True)                   # the pre-printed invoice number
    sale_date = models.DateField()
    customer = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="sales")
    customer_note = models.CharField(max_length=120, blank=True)        # a walk-in's name if given
    payment_type = models.CharField(max_length=10, choices=PaymentType.choices)
    check_bank = models.CharField(max_length=40, blank=True)
    check_number = models.CharField(max_length=30, blank=True)
    check_date = models.DateField(null=True, blank=True)
    payment_ref = models.CharField(max_length=60, blank=True)           # e-wallet / transfer reference, card approval code
    due_date = models.DateField(null=True, blank=True)                  # charge sales: sale date + the customer's terms
    total = models.DecimalField(max_digits=14, decimal_places=2)
    cost_total = models.DecimalField(max_digits=14, decimal_places=2)
    profit = models.DecimalField(max_digits=14, decimal_places=2)
    source = models.CharField(max_length=10, choices=SaleSource.choices)
    upload = models.ForeignKey(DayUpload, on_delete=models.PROTECT, null=True, blank=True, related_name="sales")
    created_at = models.DateTimeField(default=now_aware)
    created_by = models.CharField(max_length=254)

    class Meta:
        ordering = ["-sale_date", "-number"]

    @property
    def number_text(self) -> str:
        return self.booklet.fmt(self.number)


class SaleLine(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="sale_lines")
    qty = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=14, decimal_places=2)        # agreed price per piece
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4)    # the item's average cost at the moment of the sale
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    profit = models.DecimalField(max_digits=14, decimal_places=2)
    below_cost = models.BooleanField(default=False)
    below_cost_reason = models.CharField(max_length=200, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)           # owner looked at a below-cost line
    reviewed_by = models.CharField(max_length=254, blank=True)
    stock_move = models.OneToOneField(StockMove, on_delete=models.PROTECT, related_name="sale_line")

    class Meta:
        ordering = ["id"]
