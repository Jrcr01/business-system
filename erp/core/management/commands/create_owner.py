"""The first owner account (no users exist yet, so nobody can add one on screen). The owner runs it and types the password:
    .venv\\Scripts\\python erp\\manage.py create_owner --email you@gmail.com --name "Your name"
Refuses when an active owner already exists (then use the Users page)."""
import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from core import audit
from core.models import Role, User
from engines.acting_user import acting_as


class Command(BaseCommand):
    help = "Create the first owner account (asks for the password)."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", required=True)

    def handle(self, *args, email, name, **options):
        email = email.strip().lower()
        if User.objects.filter(role=Role.OWNER, is_active=True).exists():
            raise CommandError("An active owner already exists -- add or change users on the Users page.")
        if User.objects.filter(email=email).exists():
            raise CommandError(f"{email} already has a user.")
        password = getpass.getpass("Password (at least 10 characters): ")
        if password != getpass.getpass("Type it again: "):
            raise CommandError("The two passwords don't match.")
        try:
            validate_password(password, User(email=email, name=name))
        except ValidationError as e:
            raise CommandError(" ".join(e.messages))
        user = User.objects.create_user(email, name, Role.OWNER, password, must_change_password=False)
        with acting_as(email):
            audit.log("USER_ADDED", "core_user", user.pk, new=f"{email} as Owner (first owner, create_owner)")
        self.stdout.write(self.style.SUCCESS(f"Owner {email} created. Sign in at the ERP address."))
