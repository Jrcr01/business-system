/* Slick's on the phone (owner, 2026-10-12): the service worker Chrome wants before it installs the site as an app. It keeps nothing offline on
   purpose -- every page comes from the server, so figures are never stale -- it only has to exist. */
self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener("fetch", function (e) { e.respondWith(fetch(e.request)); });
