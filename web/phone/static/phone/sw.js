/* Slick's on the phone (owner, 2026-10-12): the service worker Chrome wants before it installs the site as an app. It keeps nothing offline on
   purpose -- every page comes from the server, so figures are never stale -- it only has to exist. */
self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener("fetch", function (e) { e.respondWith(fetch(e.request)); });

/* Credit card reminders (owner, 2026-10-13): the server sends a short message; show it, and open its page when it is tapped. */
self.addEventListener("push", function (e) {
  var d = {};
  try { d = e.data ? e.data.json() : {}; } catch (x) { d = {body: e.data ? e.data.text() : ""}; }
  e.waitUntil(self.registration.showNotification(d.title || "Slick's ERP", {body: d.body || "", tag: d.tag || undefined, data: {url: d.url || "/"},
    icon: "/static/phone/icon-erp-192.png", badge: "/static/phone/icon-erp-192.png"}));
});
self.addEventListener("notificationclick", function (e) {
  e.notification.close();
  var url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil(self.clients.matchAll({type: "window", includeUncontrolled: true}).then(function (all) {
    for (var i = 0; i < all.length; i++) { if ("focus" in all[i]) { all[i].navigate(url); return all[i].focus(); } }
    return self.clients.openWindow(url);
  }));
});
