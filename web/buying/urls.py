from django.urls import path

from buying import orders, views

urlpatterns = [
    path("price-lists/", views.price_lists, name="price_lists"),
    path("price-lists/template/", views.template, name="price_template"),
    path("purchase-orders/", orders.orders, name="purchase_orders"),
    path("purchase-orders/new/", orders.order_new, name="po_new"),
    path("purchase-orders/price/", orders.price_hint, name="po_price"),
    path("purchase-orders/items/", orders.picker_items, name="po_items"),
    path("purchase-orders/receive/", orders.receive, name="po_receive"),
    path("purchase-orders/match/", orders.match, name="po_match"),
    path("purchase-orders/<int:po_id>/", orders.order, name="po"),
    path("purchase-orders/<int:po_id>/edit/", orders.order_edit, name="po_edit"),
    path("purchase-orders/<int:po_id>/split/", orders.order_split, name="po_split"),
    path("purchase-orders/<int:po_id>/pdf/", orders.order_pdf, name="po_pdf"),
    path("purchase-orders/<int:po_id>/request-pdf/", orders.request_pdf, name="po_request_pdf"),
    path("purchase-orders/<int:po_id>/confirm/", orders.order_confirm, name="po_confirm"),
]
