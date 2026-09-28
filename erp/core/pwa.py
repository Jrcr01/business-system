"""Home-screen app (server step F3.1, owner 2026-09-28: "responsive + home-screen app"; Samsung phones). Two small files make the ERP
installable ("Install app" in Chrome, or later an APK built around the same site):
  * /manifest.webmanifest -- the app's name, icon, colours, and that it opens full screen;
  * /sw.js -- a service worker that ONLY keeps the ERP's own look (CSS, icons) and a "no connection" page on the phone; it always
    asks the server first (so a new look arrives at once) and uses the stored copy only without signal.
    Business pages are never stored on the phone (privacy: a lost phone holds no records) -- they always come from the server.
Both open without signing in (the phone asks for them before anyone signs in)."""
from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_safe

APP_NAME = "Slicks ERP"
NAVY = "#042c53"
SW_VERSION = "2"            # raise when the list below changes, so phones drop the old copies


@login_not_required
@require_safe
@cache_control(max_age=3600)
def manifest(request):
    label = f" ({settings.SITE_LABEL})" if settings.SITE_LABEL else ""
    return JsonResponse({
        "name": APP_NAME + label,
        "short_name": "Slicks" + (" TEST" if settings.SITE_LABEL else ""),
        "description": "Slick's Tire Supply — Slicks ERP",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#f4f6fa",
        "theme_color": NAVY,
        "icons": [
            {"src": static("core/icons/icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": static("core/icons/icon-512.png"), "sizes": "512x512", "type": "image/png"},
            {"src": static("core/icons/icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }, content_type="application/manifest+json", json_dumps_params={"ensure_ascii": False})


@login_not_required
@require_safe
@cache_control(no_cache=True)
def service_worker(request):
    keep = [static("core/erp.css"), static("core/icons/icon-192.png"), "/offline/"]
    js = f"""// Slicks ERP service worker v{SW_VERSION}: keeps only the look and the "no connection" page -- never business pages.
const CACHE = "slicks-erp-v{SW_VERSION}";
const KEEP = {keep!r};
self.addEventListener("install", e => {{ e.waitUntil(caches.open(CACHE).then(c => c.addAll(KEEP))); self.skipWaiting(); }});
self.addEventListener("activate", e => {{
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))));
  self.clients.claim();
}});
self.addEventListener("fetch", e => {{
  const req = e.request;
  if (req.method !== "GET") return;
  if (req.mode === "navigate") {{            // pages: always from the server; the offline page only when there is no signal
    e.respondWith(fetch(req).catch(() => caches.match("/offline/")));
    return;
  }}
  const url = new URL(req.url);
  if (url.origin === location.origin && url.pathname.startsWith("/static/")) {{   // latest look first; the stored copy when offline
    e.respondWith(fetch(req).then(resp => {{ const copy = resp.clone(); caches.open(CACHE).then(c => c.put(req, copy)); return resp; }})
                            .catch(() => caches.match(req)));
  }}
}});
"""
    resp = HttpResponse(js, content_type="application/javascript")
    resp["Service-Worker-Allowed"] = "/"
    return resp


@login_not_required
@require_safe
def offline(request):
    return render(request, "core/offline.html")
