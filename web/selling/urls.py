from django.urls import path

from selling import quotes, collect, counts, sales, stock

urlpatterns = [
    path("stock/", stock.stock, name="stock"),
    path("stock/labels/", stock.labels, name="stock_labels"),
    path("stock/items.json", stock.picker_items, name="stock_items"),
    path("stock/learn/", stock.learn, name="stock_learn"),
    path("stock/<int:item_id>/", stock.stock_item, name="stock_item"),
    path("stock-counts/", counts.counts, name="stock_counts"),
    path("stock-counts/<int:count_id>/", counts.count, name="stock_count"),
    path("affiliates/", sales.affiliates, name="affiliates"),
    path("affiliates/<int:customer_id>/", sales.affiliate, name="affiliate"),
    path("sales/", sales.sales, name="sales"),
    path("sales/<int:sale_id>/", sales.sale, name="sale"),
    path("customers/", sales.customers, name="customers"),
    path("customers/<int:customer_id>/", quotes.customer_page, name="customer_page"),
    path("quotations/", quotes.quotations, name="quotations"),
    path("quotations/new/", quotes.quotation_new, name="quotation_new"),
    path("quotations/data/", quotes.quotation_data, name="quotation_data"),
    path("quotations/<int:qt_id>/", quotes.quotation, name="quotation"),
    path("follow-ups/", quotes.follow_ups, name="follow_ups"),
    path("si-booklets/", sales.si_booklets, name="si_booklets"),
    path("collect/", collect.collect, name="collect"),
]
