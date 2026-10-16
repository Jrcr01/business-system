from django.urls import path

from buying import orders, views, snip

urlpatterns = [
    path("price-lists/", views.price_lists, name="price_lists"),
    path("price-lists/template/", views.template, name="price_template"),
    path("price-lists/snip/upload/", snip.upload, name="price_snip_upload"),
    path("price-lists/snip/source/<int:snip_id>/", snip.source, name="price_snip_source"),
    path("price-lists/snip/<str:token>/page/<int:page>.jpg", snip.page_image, name="price_snip_page"),
    path("price-lists/snip/<str:token>/read/", snip.read, name="price_snip_read"),
    path("price-lists/snip/<str:token>/check/", snip.check, name="price_snip_check"),
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
