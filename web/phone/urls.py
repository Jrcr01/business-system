from django.urls import path

from phone import views

urlpatterns = [
    path("", views.home, name="phone_home"),
    path("prices/", views.prices, name="phone_prices"),
    path("stock/", views.stock, name="phone_stock"),
    path("in/", views.stock_in, name="phone_in"),
    path("out/", views.stock_out, name="phone_out"),
    path("manifest.webmanifest", views.manifest, name="phone_manifest"),
    path("manifest-ops.webmanifest", views.manifest_ops, name="phone_manifest_ops"),
]
