"""For a lost phone when the System admin can't sign in to reset it themselves (owner, 2026-10-08). Run on the server, over SSH:
    set -a; . /etc/slicks-erp/env; set +a; /srv/slicks/venv/bin/python /srv/slicks/app/current/web/manage.py reset_2fa someone@example.com
The person sets up a new authenticator at their next sign-in, within 24 hours (the command is the System admin's go-ahead; for anyone else the
admin presses "Allow authenticator set-up" on their page). Recorded in the audit log."""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Reset a person's 6-digit sign-in code (lost phone)."

    def add_arguments(self, parser):
        parser.add_argument("email")

    def handle(self, *args, **opts):
        from engines.common import get_conn
        from engines.auth_service import reset_totp
        with get_conn() as conn:
            row = conn.execute("SELECT user_id FROM app_users WHERE lower(email) = ?", (opts["email"].strip().lower(),)).fetchone()
        if row is None:
            raise CommandError(f"No account for {opts['email']}.")
        ok, msg = reset_totp(int(row[0]), by_command=True)
        if not ok:
            raise CommandError(msg)
        self.stdout.write(msg)
