from django.urls import path

from payables import views

urlpatterns = [
    path("order-statements/", views.statements, name="statements"),
    path("order-statements/new/", views.statement_new, name="statement_new"),
    path("order-statements/<int:sid>/", views.statement, name="statement"),
    path("order-statements/<int:sid>/<str:kind>.pdf", views.statement_pdf, name="statement_pdf"),
    path("add-data/", views.add_data, name="add_data"),
    path("payables-due/", views.payables_due, name="payables_due"),
]
