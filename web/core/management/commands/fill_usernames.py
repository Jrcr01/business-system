"""Sign in with a username (owner, 2026-10-08): every account without one gets it from its email (the part before @). Run by deploy/release.sh; a no-op
once every account has one. The System admin can change any of them on the person's page."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Give every account without a username one made from its email."

    def handle(self, *args, **opts):
        from engines.auth_service import fill_missing_usernames
        done = fill_missing_usernames()
        self.stdout.write(f"{len(done)} username(s) set." + "".join(f"\n  {email} signs in as {name}" for email, name in done))
