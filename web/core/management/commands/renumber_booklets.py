"""Once (2026-10-11): the check booklets registered before codes were generated get BK-0001, BK-0002 ... in check-number order
(engines/booklet_rules.renumber_codes). The old code goes in the audit log. Run by deploy/release.sh; a no-op once done."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Give the existing check booklets their generated codes (BK-0001 ...)."

    def handle(self, *args, **opts):
        from engines.booklet_rules import renumber_codes
        self.stdout.write(f"{renumber_codes()} booklet(s) given their BK code.")
