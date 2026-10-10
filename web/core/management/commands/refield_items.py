"""Standard fields per type (owner, 2026-10-10): tires saved with their ply / terrain / tube as free text get them in their own fields
(engines/product_catalog.refield_tires). Without --apply it only reports. Run by deploy/release.sh with --apply; a no-op once done. Items that would
become the same item are listed and never merged."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Move tire ply / terrain / tube / type from the remark to their own fields."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **opts):
        from engines.product_catalog import refield_tires
        res = refield_tires(apply=opts["apply"])
        self.stdout.write(f"{res['changed']} tire(s) {'moved' if opts['apply'] else 'to move'} to the standard fields; {len(res['duplicates'])} possible duplicate group(s).")
        for line in res["examples"]:
            self.stdout.write("  " + line)
        for g in res["duplicates"]:
            self.stdout.write("  DUPLICATE: " + " | ".join(f"{code} {name}" for _, code, name in g))
