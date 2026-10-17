/* The phone pages (owner, 2026-10-12): a scan box that keeps focus, the camera where the browser can read barcodes (Android Chrome), a beep on each
   scan, and minus / plus buttons around any quantity box. */
(function () {
  var ph = window.phone = {};
  ph.beep = function () {
    try { var AC = window.AudioContext || window.webkitAudioContext; ph._ac = ph._ac || new AC(); var o = ph._ac.createOscillator(), g = ph._ac.createGain();
          o.frequency.value = 1320; g.gain.value = .07; o.connect(g); g.connect(ph._ac.destination); o.start(); o.stop(ph._ac.currentTime + .08); } catch (e) { }
  };
  /* camera(button, video, onCode): the button shows only where the browser has BarcodeDetector; a code is reported once per 2.5 s while it stays in view */
  ph.camera = function (btn, video, onCode) {
    if (!btn || !("BarcodeDetector" in window) || !navigator.mediaDevices) return;
    btn.hidden = false;
    var stream = null, timer = null, liveFailed = false;
    function liveAllowed() { try { return !document.featurePolicy || document.featurePolicy.allowsFeature("camera"); } catch (e) { return true; } }
    function snap(onCode, fail) {                                /* the phone's own camera app: take a photo, the barcode is read from it */
      var pick = document.createElement("input"); pick.type = "file"; pick.accept = "image/*"; pick.capture = "environment";
      pick.addEventListener("change", function () {
        var f = pick.files && pick.files[0]; if (!f) return;
        createImageBitmap(f).then(function (bmp) { return new BarcodeDetector().detect(bmp); })
          .then(function (found) { if (found.length) onCode(found[0].rawValue); else fail("No barcode was read in that photo. Hold the phone closer, keep it steady, and try again."); })
          .catch(function () { fail("That photo could not be read. Try again."); });
      });
      pick.click();
    }
    
    function stop() { if (timer) clearInterval(timer); if (stream) stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; timer = null; video.hidden = true; }
    btn.addEventListener("click", function () {
      if (stream) { stop(); return; }
      if (liveFailed || !liveAllowed()) { snap(onCode, function (m) { alert(m); }); return; }
      navigator.mediaDevices.getUserMedia({video: {facingMode: "environment"}}).then(function (s) {
        stream = s; video.srcObject = s; video.hidden = false; video.play();
        var det = new BarcodeDetector(), last = "", when = 0;
        timer = setInterval(function () {
          det.detect(video).then(function (found) {
            if (!found.length) return;
            var code = found[0].rawValue, now = Date.now();
            if (code === last && now - when < 2500) return;
            last = code; when = now; onCode(code);
          }).catch(function () {});
        }, 400);
      }).catch(function () { liveFailed = true; snap(onCode, function (m) { alert(m); }); });       /* no live camera here: the camera app instead */
    });
    window.addEventListener("pagehide", stop);
  };
  /* steppers(root): a minus and a plus button around every input marked data-step inside root; changing it fires 'input' and 'change' as typing would */
  ph.steppers = function (root) {
    (root || document).querySelectorAll("input[data-step]").forEach(function (inp) {
      if (inp.dataset.stepped) return;
      inp.dataset.stepped = "1";
      var wrap = document.createElement("span"); wrap.className = "qty";
      inp.parentNode.insertBefore(wrap, inp);
      var minus = document.createElement("button"); minus.type = "button"; minus.textContent = "−"; minus.setAttribute("aria-label", "One less");
      var plus = document.createElement("button"); plus.type = "button"; plus.textContent = "+"; plus.setAttribute("aria-label", "One more");
      wrap.appendChild(minus); wrap.appendChild(inp); wrap.appendChild(plus);
      function set(n) {
        var min = inp.dataset.min !== undefined ? parseFloat(inp.dataset.min) : 0;
        n = Math.max(min, n); inp.value = n;
        inp.dispatchEvent(new Event("input", {bubbles: true})); inp.dispatchEvent(new Event("change", {bubbles: true}));
      }
      minus.addEventListener("click", function () { set((parseFloat(inp.value) || 0) - 1); });
      plus.addEventListener("click", function () { set((parseFloat(inp.value) || 0) + 1); });
    });
  };
  document.addEventListener("DOMContentLoaded", function () { ph.steppers(document); });
})();
/* guard(form): a form marked data-guard is sent by the page itself; when the send fails (no signal, the server down) nothing is lost: every field
   and every scanned line stays as it was, with a note to press again (owner, 2026-10-12: the did-not-send guard). On success the page goes where
   the server says, as a plain send would. */
(function () {
  function guard(form) {
    if (form.dataset.guarded) return;
    form.dataset.guarded = "1";
    var note = document.createElement("div"); note.className = "ph-bad"; note.hidden = true; note.setAttribute("role", "alert");
    form.insertBefore(note, form.firstChild);
    var pressed = null;
    form.addEventListener("click", function (e) { var b = e.target.closest("button[type=submit]"); if (b) pressed = b; });
    if (!form.querySelector('input[name="once"]')) {               /* the one-time code: pressed again after a lost answer = not saved twice (2026-10-17) */
      var once = document.createElement("input"); once.type = "hidden"; once.name = "once"; once.value = code(); form.appendChild(once);
    }
    keep(form);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var data = new FormData(form);
      if (pressed && pressed.name) data.append(pressed.name, pressed.value);
      var buttons = form.querySelectorAll("button[type=submit]"); buttons.forEach(function (b) { b.disabled = true; });
      note.hidden = true;
      fetch(form.getAttribute("action") || location.href, {method: "POST", body: data, credentials: "same-origin", redirect: "follow"})
        .then(function (r) {
          if (!r.ok) throw new Error("server " + r.status);                       /* any refusal (4xx, 5xx): keep the page */
          if (/^\/login\//.test(new URL(r.url).pathname)) throw new Error("signed out");   /* the sign-in page came back: the send never landed */
          return r.text().then(function (html) {              /* the page the server answered with (after its redirect), its notes included */
            if (form._kept) form._kept.clear();                   /* the server has it: the copy on the phone goes */
            history.replaceState(null, "", r.url); document.open(); document.write(html); document.close();
          });
        })
        .catch(function (err) {
          buttons.forEach(function (b) { b.disabled = false; });
          if (form._kept) form._kept.save();
          signal(false);
          note.textContent = String(err && err.message) === "signed out"
            ? "You were signed out meanwhile: nothing was saved. Sign in again in another tab, come back and press again; everything typed and scanned is kept on this phone."
            : "Could not send: nothing was saved yet. Everything typed and scanned is kept on this phone; press again when there is signal. If it did reach the server, pressing again will not record it twice.";
          note.hidden = false; note.scrollIntoView({block: "center"});
        });
    });
  }
  function code() {
    var a = new Uint8Array(12); (window.crypto || window.msCrypto).getRandomValues(a);
    return Array.prototype.map.call(a, function (b) { return ("0" + b.toString(16)).slice(-2); }).join("");
  }
  /* keep(form): what is typed and scanned is also written to the phone (localStorage) as it happens, so a closed app, a refresh, a call or a flat battery
     loses nothing; opening the same page brings it back with Send / Throw away. Cleared once the server has it; dropped after 7 days. (2026-10-17) */
  var ME = (document.body && document.body.dataset.me) || "x", PREFIX = "ph_keep:" + ME + ":", WEEK = 7 * 24 * 3600 * 1000;
  try { Object.keys(localStorage).forEach(function (k) { if (k.indexOf("ph_keep:") === 0) { var v = JSON.parse(localStorage.getItem(k) || "{}"); if (!v.at || Date.now() - v.at > WEEK) localStorage.removeItem(k); } }); } catch (e) {}
  function fields(form) {
    var out = {};
    Array.prototype.forEach.call(form.elements, function (el) {
      if (!el.name || el.name === "csrfmiddlewaretoken" || el.type === "submit" || el.type === "button" || el.type === "file") return;
      if ((el.type === "checkbox" || el.type === "radio") && !el.checked) return;
      if (/^ln_(item|qty|price)_/.test(el.name)) return;          /* the scanned lines are kept as lines below */
      out[el.name] = el.value;
    });
    return out;
  }
  function keep(form) {
    var key = PREFIX + location.pathname + location.search + "#" + (form.id || "form"), box = form.querySelector("#scanlines"), lines = [], first = null, ready = false;
    function snap() { return {f: fields(form), l: lines.map(function (l) { return {id: l.it.id, qty: l.qty, price: l.price}; })}; }
    function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
    function changes(s) { var n = 0; for (var k in s.f) { if (k !== "once" && (first.f[k] || "") !== (s.f[k] || "")) n++; } return n + s.l.length; }
    function save() {
      if (!ready) return;
      var s = snap();
      try { if (same({f: Object.assign({}, s.f, {once: ""}), l: s.l}, {f: Object.assign({}, first.f, {once: ""}), l: first.l})) { localStorage.removeItem(key); show(0); return; }
            localStorage.setItem(key, JSON.stringify({at: Date.now(), s: s})); show(changes(s)); } catch (e) {}
    }
    function clear() { try { localStorage.removeItem(key); } catch (e) {} }
    var line = document.createElement("div"); line.className = "ph-keep"; line.hidden = true; form.insertBefore(line, form.firstChild);
    function show(n) { line.hidden = !n; line.textContent = n ? "✓ Kept on this phone: " + n + " change" + (n === 1 ? "" : "s") + " · not sent yet" : ""; }
    form._kept = {save: save, clear: clear};
    form.addEventListener("input", save); form.addEventListener("change", save);
    if (box) box.addEventListener("lines", function (e) { lines = e.detail.lines || []; save(); });
    function start() {                                           /* once the page (and its scanned lines, if any) is ready: offer what was kept */
      first = snap(); ready = true;
      var kept = null; try { kept = JSON.parse(localStorage.getItem(key) || "null"); } catch (e) {}
      if (!kept || !kept.s || same({f: Object.assign({}, kept.s.f, {once: ""}), l: kept.s.l}, {f: Object.assign({}, first.f, {once: ""}), l: first.l})) { clear(); return; }
      for (var k in kept.s.f) { var el = form.elements[k]; if (el && el.type !== "radio" && el.type !== "checkbox") el.value = kept.s.f[k]; else if (el && el.length) Array.prototype.forEach.call(el, function (r) { r.checked = r.value === kept.s.f[k]; }); }
      if (kept.s.l && kept.s.l.length && box) box.dispatchEvent(new CustomEvent("restore", {detail: kept.s.l}));
      form.dispatchEvent(new Event("input", {bubbles: true}));
      var when = new Date(kept.at), n = changes(kept.s) || kept.s.l.length;
      var w = document.createElement("div"); w.className = "ph-kept"; w.setAttribute("role", "alert");
      var b = document.createElement("b"); b.textContent = n + " change" + (n === 1 ? "" : "s") + " from " + when.toLocaleTimeString("en-PH", {hour: "numeric", minute: "2-digit"}) + " were not sent";
      var t = document.createElement("div"); t.textContent = "They are back on this page, as they were. Check them, then send.";
      var row = document.createElement("div"); row.className = "ph-row"; row.style.marginTop = "6px";
      var go = document.createElement("button"); go.type = "button"; go.className = "btn"; go.textContent = "Send them now";
      go.addEventListener("click", function () { var sb = form.querySelector("button[type=submit]"); if (sb) sb.click(); });
      var no = document.createElement("button"); no.type = "button"; no.className = "btn small ghost"; no.textContent = "Throw them away";
      no.addEventListener("click", function () { if (confirm("Throw away what was not sent? It cannot be brought back.")) { clear(); location.reload(); } });
      row.appendChild(go); row.appendChild(no); w.appendChild(b); w.appendChild(t); w.appendChild(row);
      form.parentNode.insertBefore(w, form); save();
    }
    if (box && !box.dataset.ready) box.addEventListener("ready", start, {once: true}); else start();
  }
  /* the signal mark in the top bar */
  function signal(on) { var m = document.getElementById("ph_net"); if (!m) return; m.textContent = on ? "online" : "no signal"; m.className = "ph-net " + (on ? "on" : "off"); }
  window.addEventListener("online", function () { signal(true); }); window.addEventListener("offline", function () { signal(false); });
  document.addEventListener("DOMContentLoaded", function () {
    signal(navigator.onLine !== false);
    var out = document.querySelector('.ph-bar form[action="/logout/"]');           /* signing out with something not sent: ask first */
    if (out) out.addEventListener("submit", function (e) {
      var any = false; try { any = Object.keys(localStorage).some(function (k) { return k.indexOf(PREFIX) === 0; }); } catch (x) {}
      if (any && !confirm("Something scanned or typed on this phone was not sent yet. Sign out anyway? It stays on this phone for 7 days.")) e.preventDefault();
    });
  });
  window.phone = window.phone || {};
  window.phone.guard = guard;
  document.addEventListener("DOMContentLoaded", function () { document.querySelectorAll("form[data-guard]").forEach(guard); });
})();
