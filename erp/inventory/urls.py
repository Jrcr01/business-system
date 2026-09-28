from django.urls import path

from inventory import views

urlpatterns = [
    path("items/", views.items, name="inv_items"),
    path("items/add/", views.item_add, name="inv_item_add"),
    path("items/<int:pk>/", views.item_detail, name="inv_item"),
    path("items/<int:pk>/adjust/", views.item_adjust, name="inv_item_adjust"),
    path("items/<int:pk>/settings/", views.item_settings, name="inv_item_settings"),
    path("stock-in/", views.stock_ins, name="inv_stock_ins"),
    path("stock-in/new/", views.stock_in_new, name="inv_stock_in_new"),
    path("stock-in/<int:pk>/", views.stock_in_detail, name="inv_stock_in"),
    path("stock-in/<int:pk>/reverse/", views.stock_in_reverse, name="inv_stock_in_reverse"),
    path("opening-list/", views.opening_list, name="inv_opening"),
    path("opening-list/template/", views.opening_template, name="inv_opening_template"),
    path("opening-list/<int:pk>/decide/", views.opening_decide, name="inv_opening_decide"),
]
