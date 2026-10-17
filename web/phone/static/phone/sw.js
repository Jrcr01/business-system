/* Slick's on the phone (owner, 2026-10-12): the service worker Chrome wants before it installs the site as an app. It keeps nothing offline on
   purpose -- every page comes from the server, so figures are never stale -- it only has to exist. */
self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener("fetch", function (e) { e.respondWith(fetch(e.request)); });

/* Credit card reminders (owner, 2026-10-13): the server sends a short message; show it, and open its page when it is tapped. */
self.addEventListener("push", function (e) {
  var d = {};
  try { d = e.data ? e.data.json() : {}; } catch (x) { d = {body: e.data ? e.data.text() : ""}; }
  var o = {body: d.body || "", tag: d.tag || undefined, data: {url: d.url || "/", kind: d.kind || "", refuse: d.refuse || ""},
           badge: "/static/phone/badge-96.png?v=2"};
  if (d.kind === "signin" || d.kind === "test") {               /* the sign-in approval (owner, 2026-10-17): drops down, vibrates, stays until tapped */
    o.actions = [{action: "no", title: "Not me"}, {action: "yes", title: "Approve"}];
    o.requireInteraction = true; o.renotify = true; o.vibrate = [200, 100, 200, 100, 400];
  }
  e.waitUntil(self.registration.showNotification(d.title || "Slick's ERP", o));
});
self.addEventListener("notificationclick", function (e) {
  e.notification.close();
  var data = e.notification.data || {};
  if (data.kind === "test") {                                   /* the test banner: a button only closes it */
    if (e.action) { e.waitUntil(self.registration.showNotification("It works", {body: "Sign-in approvals will drop down like that.", tag: "signin-test",
      badge: "/static/phone/badge-96.png?v=2"})); return; }
  }
  if (data.kind === "signin" && e.action === "no" && data.refuse) {   /* Not me: refused from the banner, no page opened */
    e.waitUntil(fetch(data.refuse, {method: "POST", credentials: "same-origin"}).then(function (r) { return r.json(); }).then(function (res) {
      return self.registration.showNotification(res.ok ? "Refused" : "Not refused", {tag: "signin", badge: "/static/phone/badge-96.png?v=2",
        body: res.ok ? "That computer was not let in. If it keeps happening, tell the System admin: someone has your password." : (res.msg || "That request is no longer open.")});
    }).catch(function () {
      return self.registration.showNotification("Not refused", {tag: "signin", body: "No signal. Open the approval and tap \"It is not me\".", data: {url: data.url},
        badge: "/static/phone/badge-96.png?v=2"});
    }));
    return;
  }
  var url = data.url || "/";
  e.waitUntil(self.clients.matchAll({type: "window", includeUncontrolled: true}).then(function (all) {
    for (var i = 0; i < all.length; i++) { if ("focus" in all[i]) { all[i].navigate(url); return all[i].focus(); } }
    return self.clients.openWindow(url);
  }));
});
