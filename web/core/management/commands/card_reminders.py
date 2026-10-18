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
            try:                                                 # the Yesterday notification rides on the same hourly timer (owner, 2026-10-19)
                from engines.yesterday_push import run as yesterday
                self.stdout.write(yesterday())
            except Exception as e:                               # noqa: BLE001 -- never take the card reminders down with it
                self.stdout.write(f"Yesterday: {e}")
            try:                                                 # open loans: next month's interest row before it is due (owner, 2026-10-14)
                from engines.open_loans import ensure_interest
                self.stdout.write(f"{ensure_interest()} open-loan interest month(s) added.")
            except Exception as e:                               # noqa: BLE001
                self.stdout.write(f"Open loans: {e}")
            try:                                                 # the 8 AM email rides on the same hourly timer (owner, 2026-10-14)
                from django.conf import settings
                from engines import morning_email
                host = next((h for h in settings.ALLOWED_HOSTS if "." in h and h[0].isalpha()), "")
                self.stdout.write(morning_email.run(site=f"https://{host}" if host else ""))
            except Exception as e:                               # noqa: BLE001 -- never take the card reminders down with it
                self.stdout.write(f"Morning email: {e}")
