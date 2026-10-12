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
    var stream = null, timer = null;
    function stop() { if (timer) clearInterval(timer); if (stream) stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; timer = null; video.hidden = true; }
    btn.addEventListener("click", function () {
      if (stream) { stop(); return; }
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
      }).catch(function () { alert("The camera could not be opened."); });
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
    form.addEventListener("submit", function (e) {
      if (form.dataset.sending) return;                          /* the real send, after the checks below */
      e.preventDefault();
      var data = new FormData(form);
      if (pressed && pressed.name) data.append(pressed.name, pressed.value);
      var buttons = form.querySelectorAll("button[type=submit]"); buttons.forEach(function (b) { b.disabled = true; });
      note.hidden = true;
      fetch(form.getAttribute("action") || location.href, {method: "POST", body: data, credentials: "same-origin", redirect: "follow"})
        .then(function (r) {
          if (r.status >= 500) throw new Error("server " + r.status);
          return r.text().then(function (html) {              /* the page the server answered with (after its redirect), its notes included */
            history.replaceState(null, "", r.url); document.open(); document.write(html); document.close();
          });
        })
        .catch(function () {
          buttons.forEach(function (b) { b.disabled = false; });
          note.textContent = "Could not send: nothing was saved. Check the signal and press again; everything typed and scanned is still here.";
          note.hidden = false; note.scrollIntoView({block: "center"});
        });
    });
  }
  window.phone = window.phone || {};
  window.phone.guard = guard;
  document.addEventListener("DOMContentLoaded", function () { document.querySelectorAll("form[data-guard]").forEach(guard); });
})();
