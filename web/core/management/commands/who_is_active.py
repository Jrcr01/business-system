"""Who is using the system right now (2026-10-09): run by deploy/deploy_to_server.sh before a release, so nobody is cut off mid-work.
Prints one line per person active in the last N minutes and exits 3 when anyone is, 0 when nobody is."""
import sys

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "List the people active in the last N minutes (default 10)."

    def add_arguments(self, parser):
        parser.add_argument("--minutes", type=int, default=10)

    def handle(self, *args, **opts):
        from engines.common import get_conn
        with get_conn() as conn:
            rows = conn.execute("""SELECT name, email, CAST((julianday('now') - julianday(last_seen_at)) * 1440 AS INTEGER), COALESCE(last_seen_path, '')
                                   FROM app_users WHERE last_seen_at IS NOT NULL AND julianday('now') - julianday(last_seen_at) <= ? / 1440.0
                                   ORDER BY last_seen_at DESC""", (opts["minutes"],)).fetchall()
        if not rows:
            self.stdout.write(f"Nobody active in the last {opts['minutes']} minutes.")
            return
        for name, email, mins, path in rows:
            self.stdout.write(f"ACTIVE  {name} ({email}): {mins} min ago, on {path or '?'}")
        sys.exit(3)
