"""Once (2026-10-09): the new switches "Mark payables paid" and "Card statements" start as Bank and Loans for every person. Run by deploy/release.sh;
a no-op once done."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Start the two new switches as the switches they were split from."

    def handle(self, *args, **opts):
        from engines.auth_service import split_new_switches
        self.stdout.write(f"{split_new_switches()} switch(es) carried over.")
