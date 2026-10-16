/* Snip from a document (owner, 2026-10-16): drag a box on the page picture; the server reads what is inside (engines/snip.py) and the grid shows it with
   its column names guessed from the category's own fields; a doubtful OCR cell is orange and can be typed over; "Check these rows" sends the grid on. */
var slicksSnip = (function () {
  function start(o) {
    var wrap = o.wrap, img = o.img, box = o.box, label = o.label, W = o.w, H = o.h, csrf = o.csrf, mode = "table", from = null, rect = null, data = null;
    function pos(e) { var r = img.getBoundingClientRect(), p = e.touches ? e.touches[0] : (e.changedTouches ? e.changedTouches[0] : e); return {x: (p.clientX - r.left) / r.width * W, y: (p.clientY - r.top) / r.height * H}; }
    function show(a, b) { var x0 = Math.max(0, Math.min(a.x, b.x)), y0 = Math.max(0, Math.min(a.y, b.y)), x1 = Math.min(W, Math.max(a.x, b.x)), y1 = Math.min(H, Math.max(a.y, b.y));
      box.hidden = false; box.style.left = (x0 / W * 100) + "%"; box.style.top = (y0 / H * 100) + "%"; box.style.width = ((x1 - x0) / W * 100) + "%"; box.style.height = ((y1 - y0) / H * 100) + "%"; return {x0: x0, y0: y0, x1: x1, y1: y1}; }
    function setMode(m) { mode = m; o.modeButtons.forEach(function (b) { b.classList.toggle("on", b.dataset.mode === m); }); }
    o.modeButtons.forEach(function (b) { b.addEventListener("click", function () { setMode(b.dataset.mode); if (rect) read(); }); });
    function read() {
      label.textContent = "Reading…"; o.onBusy && o.onBusy(true);
      fetch(o.readUrl, {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json", "X-CSRFToken": csrf},
            body: JSON.stringify({page: o.page, mode: mode, box: rect, category: o.category()})})
        .then(function (r) { return r.json(); })
        .then(function (d) { data = d; o.onBusy && o.onBusy(false);
          if (!d.ok) { label.textContent = ""; o.onResult(null, d.msg || "Nothing read."); return; }
          label.textContent = mode === "text" ? "Text" : "Table · " + d.rows.length + " row" + (d.rows.length === 1 ? "" : "s");
          o.onResult(d, ""); })
        .catch(function () { o.onBusy && o.onBusy(false); label.textContent = ""; o.onResult(null, "Could not reach the server. Check the connection and try again."); });
    }
    var down = function (e) { if (e.target.closest("a, button")) return; from = pos(e); rect = show(from, from); e.preventDefault(); };
    var move = function (e) { if (!from) return; rect = show(from, pos(e)); e.preventDefault(); };
    var up = function (e) { if (!from) return; rect = show(from, pos(e)); from = null; if (rect.x1 - rect.x0 > 3 && rect.y1 - rect.y0 > 3) read(); };
    wrap.addEventListener("mousedown", down); window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
    wrap.addEventListener("touchstart", down, {passive: false}); wrap.addEventListener("touchmove", move, {passive: false}); wrap.addEventListener("touchend", up);
    return {rect: function () { return rect; }, data: function () { return data; }, mode: function () { return mode; }, reread: read};
  }
  return {start: start};
})();
