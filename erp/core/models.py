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
