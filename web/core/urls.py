from django.urls import path

from core import views

urlpatterns = [
    path("", views.home, name="home"),
    path("home/", views.home),
    path("health/", views.health, name="health"),
    path("setup/", views.setup, name="setup"),
    path("login/", views.login, name="login"),
    path("logout/", views.logout, name="logout"),
    path("password/", views.change_password, name="password"),
    path("whoami/", views.whoami, name="whoami"),
    path("<slug:page_id>/", views.page, name="page"),
]
