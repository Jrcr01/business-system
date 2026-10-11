"""The starting expense categories (owner, 2026-10-11) on a database that existed before migration 092 -- the migration tool adds tables, never
rows. Idempotent: a name already on the list is left alone; the owner renames and adds freely afterwards."""
from django.core.management.base import BaseCommand

SEED = ["Electricity", "Water", "Rent", "Wages & helpers", "Meals", "Shop supplies", "Fuel & delivery", "Repairs", "Permits & taxes", "Bank charges", "Others"]


class Command(BaseCommand):
    help = "Seed the expense categories once (idempotent)."

    def handle(self, *args, **opts):
        from engines.common import write_tx
        added = 0
        with write_tx() as conn:
            for i, name in enumerate(SEED, start=1):
                added += conn.execute("INSERT OR IGNORE INTO expense_categories (name, sort) VALUES (?, ?)", (name, i)).rowcount
        self.stdout.write(f"{added} expense categorie(s) seeded.")
