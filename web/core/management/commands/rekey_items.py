"""The one-time re-key of the item list (owner, 2026-10-08): every spec now tells two products apart. Prints the items that would share one key (never merged here);
with --apply, saves the other recomputed keys. Run by deploy/release.sh after the database is brought up to date (safe to run again)."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Recompute item keys with every spec; report possible duplicates."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **opts):
        from engines.product_catalog import rekey_items
        res = rekey_items(apply=opts["apply"])
        self.stdout.write(f"{res['changed']} item key(s) {'recomputed' if opts['apply'] else 'would change'}; {len(res['duplicates'])} possible duplicate group(s).")
        for g in res["duplicates"]:
            self.stdout.write("  same product? " + " | ".join(f"{code} {name} (id {i})" for i, code, name in g))
