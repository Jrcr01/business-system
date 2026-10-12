from django.urls import path

from phone import views

urlpatterns = [
    path("", views.home, name="phone_home"),
    path("prices/", views.prices, name="phone_prices"),
    path("stock/", views.stock, name="phone_stock"),
    path("in/", views.stock_in, name="phone_in"),
    path("out/", views.stock_out, name="phone_out"),
    path("sale/", views.sale, name="phone_sale"),
    path("count/", views.count, name="phone_count"),
    path("count/<int:count_id>/", views.count_sheet, name="phone_count_sheet"),
    path("day/", views.day, name="phone_day"),
    path("manifest.webmanifest", views.manifest, name="phone_manifest"),
    path("manifest-ops.webmanifest", views.manifest_ops, name="phone_manifest_ops"),
]
