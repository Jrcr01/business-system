from django.urls import path

from core import views

urlpatterns = [
    path("", views.home, name="home"),
    path("home/", views.home),
    path("health/", views.health, name="health"),
    path("setup/", views.setup, name="setup"),
    path("login/", views.login, name="login"),
    path("logout/", views.logout, name="logout"),
    path("2fa/", views.code, name="code"),
    path("2fa/setup/", views.code_setup, name="code_setup"),
    path("password/", views.change_password, name="password"),
    path("whoami/", views.whoami, name="whoami"),
    path("search/", views.search, name="search"),
    path("related/<str:kind>/<int:doc_id>/", views.related, name="related"),
    path("<slug:page_id>/", views.page, name="page"),
]
