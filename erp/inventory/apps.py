from django.apps import AppConfig


class InventoryConfig(AppConfig):
    """Items and stock (Sales step S1, 2026-09-29). Grows into the Inventory module later (counts with approval, affiliates, PO)."""
    name = "inventory"
    verbose_name = "Items and stock"

    def ready(self):
        from core import numbering

        numbering.register("RR", "Receiving report (stock in)")
