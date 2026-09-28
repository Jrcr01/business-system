"""core models, step C1 (2026-09-28): users with one role each, and the sign-in attempts that drive the lockout.

Owner's choices: roles Owner / Sales / Viewer; password sign-in on the laptop now, Google sign-in (2-step) at the server step;
users are never deleted (deactivated instead); every change goes to the shared audit_logs table (core/audit.py)."""
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models

from core.clock import now_aware


class Role(models.TextChoices):
    OWNER = "owner", "Owner"          # everything: approvals, family / personal records, users, audit log
    SALES = "sales", "Sales"          # Sales and AR pages only
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
