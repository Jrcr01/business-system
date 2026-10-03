from django.apps import AppConfig


class PurchasingConfig(AppConfig):
    """Purchasing (D2, 2026-10-02): supplier price lists with discounts and the Excel template, purchase orders, and receiving a supplier invoice into an AP batch and
    into stock. The rules live in engines/ (po_rules, price_list_service, price_template_service, discount_rules, product_catalog); these are the screens."""
    name = "purchasing"
    verbose_name = "Purchasing"
