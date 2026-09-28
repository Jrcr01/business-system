"""SANDBOX ONLY: one test user per role with random passwords, written to erp/.sandbox_logins.txt (gitignored, never shown in chat).
Refuses unless the database file name contains _TEST. Existing test users get new passwords."""
import secrets
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import Role, User
from engines.acting_user import OWNER_EMAIL

TEST_USERS = [
    (OWNER_EMAIL, "JRCR (sandbox)", Role.OWNER),
    ("sales.test@example.com", "Sales tester", Role.SALES),
    ("viewer.test@example.com", "Viewer tester", Role.VIEWER),
]


class Command(BaseCommand):
    help = "Sandbox only: create / refresh one test user per role (passwords saved to erp/.sandbox_logins.txt)."

    def handle(self, *args, **options):
        db_name = Path(settings.DATABASES["default"]["NAME"]).name
        if "_TEST" not in db_name:
            raise CommandError(f"Refusing: {db_name} is not a sandbox database.")
        lines = [f"Sandbox sign-ins for {db_name} (laptop test data only):"]
        for email, name, role in TEST_USERS:
            password = secrets.token_urlsafe(12)
            user = User.objects.filter(email=email).first() or User(email=email, name=name, role=role)
            user.role, user.is_active, user.must_change_password = role, True, False
            user.set_password(password)
            user.save()
            lines.append(f"  {role.label:7} {email}  {password}")
        out = Path(settings.ERP_DIR) / ".sandbox_logins.txt"
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.stdout.write(f"{len(TEST_USERS)} sandbox users ready; sign-ins saved to {out}")
