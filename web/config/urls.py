from django.urls import include, path

urlpatterns = [                            # the built stages first: core's last pattern catches every page not built yet
    path("", include("masters.urls")),     # D1
    path("", include("buying.urls")),      # D2
    path("", include("core.urls")),
]
