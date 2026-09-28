from django.urls import include, path

from core import pwa

urlpatterns = [
    path("manifest.webmanifest", pwa.manifest, name="manifest"),
    path("sw.js", pwa.service_worker, name="service_worker"),
    path("offline/", pwa.offline, name="offline"),
    path("", include("core.urls")),
]
