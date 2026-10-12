from django.urls import include, path

urlpatterns = [                            # the built stages first: core's last pattern catches every page not built yet
    path("", include("masters.urls")),     # D1
    path("", include("buying.urls")),      # D2
    path("", include("payables.urls")),    # D3
    path("", include("selling.urls")),     # the revenue cycle, stage A
    path("phone/", include("phone.urls")), # the phone pages (owner, 2026-10-12)
    path("", include("core.urls")),
]
