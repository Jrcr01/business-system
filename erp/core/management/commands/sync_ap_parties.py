"""Link every AP creditor that has no party yet (also runs by itself when the owner opens Parties)."""
from django.core.management.base import BaseCommand

from core.parties import sync_ap_links


class Command(BaseCommand):
    help = "Link AP creditors (suppliers, lenders, banks, cards) into the shared party list."

    def handle(self, *args, **options):
        n = sync_ap_links()
        self.stdout.write(f"{n} AP record(s) linked.")
