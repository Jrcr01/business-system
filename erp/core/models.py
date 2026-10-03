"""core models, step C1 (2026-09-28): users with one role each, and the sign-in attempts that drive the lockout.

Owner's choices: roles Owner / Sales / Viewer; password sign-in on the laptop now, Google sign-in (2-step) at the server step;
users are never deleted (deactivated instead); every change goes to the shared audit_logs table (core/audit.py)."""
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models

from core.clock import now_aware


class Role(models.TextChoices):
    ADMIN = "admin", "System admin"   # JRCR only (2026-10-03): the AP app's System admin; this paused site does not give it extra pages yet
    OWNER = "owner", "Owner"          # everything: approvals, family / personal records, users, audit log
    SALES = "sales", "Sales"          # Sales and AR pages only
    PAYABLES = "payables", "Payables"  # the payables clerk: invoices, batches, parks checks, receives orders (engines/permissions.ACTIONS)
    PURCHASING = "purchasing", "Purchasing"  # price lists, discounts and their own purchase orders
    RELEASER = "releaser", "Releaser"  # hands the checks to the supplier (marks them released)
    VIEWER = "viewer", "Viewer"       # reads reports, changes nothing


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, name, role=Role.VIEWER, password=None, **extra):
        if not email:
            raise ValueError("An email is required.")
        user = self.model(email=self.normalize_email(email).lower(), name=name, role=role, **extra)
        user.set_password(password)           # None -> unusable password (cannot sign in until one is set)
        user.save(using=self._db)
        return user


class User(AbstractBaseUser):
    """One person. Signs in with their email (the same email Google sign-in will use later, and the one audit rows record)."""
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=100)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.VIEWER)
    is_active = models.BooleanField(default=True)
    must_change_password = models.BooleanField(default=True)     # a temporary password from the owner must be replaced
    created_at = models.DateTimeField(default=now_aware)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} <{self.email}>"

    @property
    def is_owner(self) -> bool:
        return self.role == Role.OWNER

    # Django's own screens (none enabled yet) ask these; only the owner would ever qualify.
    @property
    def is_staff(self) -> bool:
        return self.is_owner

    def has_perm(self, perm, obj=None) -> bool:
        return self.is_active and self.is_owner

    def has_module_perms(self, app_label) -> bool:
        return self.is_active and self.is_owner


class LoginAttempt(models.Model):
    """Every sign-in try, right or wrong: 5 wrong in a row for one email = locked for 15 minutes (owner's choice)."""
    email = models.CharField(max_length=254, db_index=True)       # as typed (lower-cased), even when no such user exists
    succeeded = models.BooleanField()
    ip = models.GenericIPAddressField(null=True, blank=True)
    at = models.DateTimeField(default=now_aware, db_index=True)

    class Meta:
        ordering = ["-at", "-id"]


# ---- parties (core step C3, 2026-09-28) -----------------------------------------------------------------------------------------
# Owner's choices: ONE shared list of everyone Slick's deals with; a party can have several roles; the brother (Sales) adds customers
# and edits contact details, only the owner sets credit / terms / affiliate; details kept: contact number and address; cash sales go
# to one "Walk-in customer"; ALL AP creditors are linked in (read-only here -- AP keeps managing them until AP moves to Django).

class Creditor(models.Model):
    """AP's creditors table, read-only from the ERP (AP owns it; Django never creates, changes or migrates it)."""
    creditor_id = models.AutoField(primary_key=True)
    creditor_name = models.TextField()
    creditor_type = models.TextField()          # SUPPLIER / LENDER / BANK_LOAN / CREDIT_CARD
    contact_number = models.TextField(null=True)
    terms = models.TextField(null=True)
    is_active = models.IntegerField()

    class Meta:
        managed = False
        db_table = "creditors"


class Party(models.Model):
    name = models.CharField(max_length=150)
    name_key = models.CharField(max_length=150, db_index=True)     # lower-case, single spaces: stops the same name being added twice
    is_customer = models.BooleanField(default=False)
    is_affiliate = models.BooleanField(default=False)               # the owner's mother's siblings' businesses (owner only)
    is_walk_in = models.BooleanField(default=False)                 # the one "Walk-in customer" for cash sales
    contact_number = models.CharField(max_length=50, blank=True)
    address = models.CharField(max_length=250, blank=True)
    credit_allowed = models.BooleanField(default=False)             # owner only; no peso limit (alerts instead, owner's choice)
    terms_days = models.PositiveIntegerField(null=True, blank=True) # owner only
    is_active = models.BooleanField(default=True)                   # never deleted
    created_at = models.DateTimeField(default=now_aware)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["is_walk_in"], condition=models.Q(is_walk_in=True), name="core_party_one_walk_in"),
        ]

    def __str__(self):
        return self.name


class PartyAPLink(models.Model):
    """One AP creditor belongs to exactly one party; a party may hold several (e.g. a bank set up for loans AND for cards)."""
    party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="ap_links")
    creditor = models.OneToOneField(Creditor, on_delete=models.PROTECT, db_column="creditor_id", related_name="party_link")
    linked_at = models.DateTimeField(default=now_aware)


# ---- carve-out tags (core step C4, 2026-09-28) ------------------------------------------------------------------------------------
# Owner's choices: three fixed groups (Business / Rental / Family-personal) with the owner's own sub-tags; everything never tagged
# counts as Business; a record has one MAIN tag and may move peso amounts to other tags (the main tag keeps the rest, so the parts
# always add up); sub-tags are renamed or deactivated, never deleted; owner only (family-personal records are the owner's).

class TagGroup(models.TextChoices):
    BUSINESS = "business", "Business"
    RENTAL = "rental", "Rental"
    FAMILY = "family", "Family-personal"


class Tag(models.Model):
    group = models.CharField(max_length=10, choices=TagGroup.choices)
    name = models.CharField(max_length=60)
    name_key = models.CharField(max_length=60)
    is_default = models.BooleanField(default=False)       # the one "Business · General" every untagged record falls under
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=now_aware)

    class Meta:
        ordering = ["group", "name"]
        constraints = [
            models.UniqueConstraint(fields=["group", "name_key"], name="core_tag_unique_name_in_group"),
            models.UniqueConstraint(fields=["is_default"], condition=models.Q(is_default=True), name="core_tag_one_default"),
        ]

    def __str__(self):
        return f"{self.get_group_display()} · {self.name}"


class RecordType(models.TextChoices):
    CC_STATEMENT = "cc_statement", "Card statements"
    LOAN_NOTE = "loan_note", "Lender loans"
    BANK_LOAN = "bank_loan", "Bank loans"
    BATCH = "batch", "Supplier batches"


class Tagging(models.Model):
    """The tag of one money record (AP's records are pointed at by type + id; AP itself is not changed). No row = Business · General."""
    record_type = models.CharField(max_length=20, choices=RecordType.choices)
    record_id = models.IntegerField()
    main_tag = models.ForeignKey(Tag, on_delete=models.PROTECT, related_name="main_of")
    updated_at = models.DateTimeField(default=now_aware)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["record_type", "record_id"], name="core_tagging_one_per_record")]


class TagSplit(models.Model):
    """A peso amount of the record moved from the main tag to another tag."""
    tagging = models.ForeignKey(Tagging, on_delete=models.CASCADE, related_name="splits")
    tag = models.ForeignKey(Tag, on_delete=models.PROTECT, related_name="split_parts")
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tagging", "tag"], name="core_tagsplit_one_per_tag"),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="core_tagsplit_amount_positive"),
        ]
