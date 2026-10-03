from django.urls import path

from purchasing import views

urlpatterns = [
    path("price-lists/", views.price_lists, name="pl_home"),
    path("price-lists/template/", views.price_template, name="pl_template"),
    path("purchase-orders/", views.po_list, name="po_list"),
    path("purchase-orders/new/", views.po_form, name="po_new"),
    path("purchase-orders/<int:pk>/edit/", views.po_form, name="po_edit"),
    path("purchase-orders/<int:pk>/", views.po_detail, name="po_detail"),
    path("purchase-orders/<int:pk>/pdf/", views.po_pdf, name="po_pdf"),
    path("receive/", views.receive, name="po_receive"),
]
