"""Credit card reminders (owner, 2026-10-13): run every hour by the server's timer. Sends only at the two hours set in Settings > Numbers >
Notifications (8 AM and 2 PM Manila), and never the same reminder twice. --slot am|pm sends that slot now, whatever the hour (for a check)."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Send the credit card reminders that are due this hour."

    def add_arguments(self, parser):
        parser.add_argument("--slot", choices=["am", "pm"], default=None)

    def handle(self, *args, **opts):
        from engines.acting_user import acting_as
        from engines.card_reminders import run
        with acting_as("system@slicks", "admin"):
            self.stdout.write(run(slot=opts["slot"]))
