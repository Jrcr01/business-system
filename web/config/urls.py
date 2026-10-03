from django.urls import include, path

urlpatterns = [
    path("", include("masters.urls")),     # D1 pages first: core's last pattern catches every page not built yet
    path("", include("core.urls")),
]
