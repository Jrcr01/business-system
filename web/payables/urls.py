from django.urls import path

from payables import bulk, views

urlpatterns = [
    path("order-statements/", views.statements, name="statements"),
    path("order-statements/new/", views.statement_new, name="statement_new"),
    path("order-statements/import-many/", bulk.import_many, name="import_many"),
    path("order-statements/entry-workbook.xlsm", bulk.entry_workbook, name="entry_workbook"),
    path("order-statements/invoice-template.csv", bulk.invoice_template_csv, name="invoice_template_csv"),
    path("order-statements/print-many/", bulk.print_many, name="print_many"),
    path("order-statements/soa/", bulk.soa, name="soa"),
    path("order-statements/<int:sid>/", views.statement, name="statement"),
    path("order-statements/<int:sid>/import/", bulk.invoice_import, name="invoice_import"),
    path("order-statements/<int:sid>/<str:kind>.pdf", views.statement_pdf, name="statement_pdf"),
    path("add-data/", views.add_data, name="add_data"),
    path("payables-due/", views.payables_due, name="payables_due"),
    path("reports/", views.reports, name="reports"),
    path("checks/", views.checks, name="checks"),
]
