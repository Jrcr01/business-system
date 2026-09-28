from django.urls import path

from sales import views

urlpatterns = [
    path("sales/", views.sales_list, name="sales_list"),
    path("sales/new/", views.sale_new, name="sales_new"),
    path("sales/<int:pk>/", views.sale_detail, name="sales_sale"),
    path("sales/day-sheet/", views.daysheet_upload, name="sales_daysheet"),
    path("sales/day-sheet/template/", views.daysheet_template, name="sales_daysheet_template"),
    path("sales/booklets/", views.booklets, name="sales_booklets"),
    path("sales/booklets/register/", views.booklet_register, name="sales_booklet_register"),
    path("sales/booklets/<int:pk>/close/", views.booklet_close, name="sales_booklet_close"),
    path("sales/booklets/cancel-number/", views.number_cancel, name="sales_number_cancel"),
    path("sales/below-cost/", views.below_cost, name="sales_below_cost"),
    path("sales/below-cost/<int:pk>/reviewed/", views.below_cost_reviewed, name="sales_below_cost_reviewed"),
]
