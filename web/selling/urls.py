from django.urls import path

from selling import collect, counts, sales, stock

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
    path("si-booklets/", sales.si_booklets, name="si_booklets"),
    path("collect/", collect.collect, name="collect"),
]
