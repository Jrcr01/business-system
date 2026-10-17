/* Snip from a document (owner, 2026-10-16): drag a box on the page picture; the server reads what is inside (engines/snip.py) and the grid shows it with
   its column names guessed from the destination's own fields; a doubtful OCR cell is orange and can be typed over; the form sends the grid on.
   slicksSnip.start(...) draws the box and reads; slicksSnip.grid(...) is the grid and the form, shared by Price lists, Bank loans and the SOA matcher. */
var slicksSnip = (function () {
  function start(o) {
    var wrap = o.wrap, img = o.img, box = o.box, label = o.label, W = o.w, H = o.h, csrf = o.csrf, mode = "table", from = null, rect = null, data = null;
    function pos(e) { var r = img.getBoundingClientRect(), p = e.touches ? e.touches[0] : (e.changedTouches ? e.changedTouches[0] : e); return {x: (p.clientX - r.left) / r.width * W, y: (p.clientY - r.top) / r.height * H}; }
    function show(a, b) { var x0 = Math.max(0, Math.min(a.x, b.x)), y0 = Math.max(0, Math.min(a.y, b.y)), x1 = Math.min(W, Math.max(a.x, b.x)), y1 = Math.min(H, Math.max(a.y, b.y));
      box.hidden = false; box.style.left = (x0 / W * 100) + "%"; box.style.top = (y0 / H * 100) + "%"; box.style.width = ((x1 - x0) / W * 100) + "%"; box.style.height = ((y1 - y0) / H * 100) + "%"; return {x0: x0, y0: y0, x1: x1, y1: y1}; }
    function setMode(m) { mode = m; o.modeButtons.forEach(function (b) { b.classList.toggle("on", b.dataset.mode === m); }); }
    o.modeButtons.forEach(function (b) { b.addEventListener("click", function () { setMode(b.dataset.mode); if (rect) read(); }); });
    var reading = 0;
    function read() {
      var mine = ++reading;                                      /* a newer box cancels the wait for an older one */
      label.textContent = "Reading…"; o.onBusy && o.onBusy(true, "Reading the box…");
      function got(d) {
        if (mine !== reading) return;
        if (d.pending) {                                         /* OCR in the background: ask again in a moment */
          o.onBusy && o.onBusy(true, "Reading the picture (OCR)… this can take a minute or two.");
          setTimeout(function () { post({job: d.job, category: o.category ? o.category() : ""}).then(got).catch(fail); }, 2500); return;
        }
        data = d; o.onBusy && o.onBusy(false);
        if (!d.ok) { label.textContent = ""; o.onResult(null, d.msg || "Nothing read."); return; }
        label.textContent = mode === "text" ? "Text" : "Table · " + d.rows.length + " row" + (d.rows.length === 1 ? "" : "s");
        o.onResult(d, "");
      }
      function fail() { if (mine !== reading) return; o.onBusy && o.onBusy(false); label.textContent = ""; o.onResult(null, "Could not reach the server. Check the connection and try again."); }
      function post(body) { return fetch(o.readUrl, {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json", "X-CSRFToken": csrf}, body: JSON.stringify(body)}).then(function (r) { return r.json(); }); }
      post({page: o.page, mode: mode, box: rect, category: o.category ? o.category() : ""}).then(got).catch(fail);
    }
    var down = function (e) { if (e.target.closest("a, button")) return; from = pos(e); rect = show(from, from); e.preventDefault(); };
    var last = null, scrolling = null;
    function autoscroll() {                                      /* the box runs past the screen: the page follows the pointer (owner, 2026-10-17) */
      if (!from || !last) { scrolling = null; return; }
      var sc = o.scroller, top = 0, bottom = window.innerHeight;
      if (sc && sc.scrollHeight > sc.clientHeight) { var br = sc.getBoundingClientRect(); top = br.top; bottom = br.bottom; }
      var edge = 48, dy = last.clientY > bottom - edge ? 14 : last.clientY < top + edge ? -14 : 0;
      if (dy) { if (sc && sc.scrollHeight > sc.clientHeight) sc.scrollTop += dy; else window.scrollBy(0, dy); rect = show(from, pos(last)); }
      scrolling = requestAnimationFrame(autoscroll);
    }
    var move = function (e) { if (!from) return; if (e.touches && e.touches.length > 1) { from = null; box.hidden = true; return; }
      last = e.touches ? e.touches[0] : e; rect = show(from, pos(e)); if (!scrolling) scrolling = requestAnimationFrame(autoscroll); e.preventDefault(); };
    var up = function (e) { if (!from) return; rect = show(from, pos(e)); from = null; last = null; if (rect.x1 - rect.x0 > 3 && rect.y1 - rect.y0 > 3) read(); };
    wrap.addEventListener("mousedown", down); window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
    wrap.addEventListener("touchstart", down, {passive: false}); wrap.addEventListener("touchmove", move, {passive: false}); wrap.addEventListener("touchend", up);
    return {rect: function () { return rect; }, data: function () { return data; }, mode: function () { return mode; }, reread: read, setMode: setMode};
  }

  /* The grid: g = {grid, msg, text, actions, form, keys: function () -> [[key, label], ...], hidden: {rows, colmap, header, x0, y0, x1, y1}, wide: button|null, split: element|null, category: select|null} */
  function grid(g) {
    var rows = [], colmap = [], header = false, doubt = [], sn = null, target = null;
    function pick(inp) { if (target) target.classList.remove("snip-target"); target = inp; if (inp) { inp.classList.add("snip-target"); if (sn) sn.setMode("text"); } }
    function options(sel) { var out = '<option value="">—</option>'; (g.keys() || []).forEach(function (kv) { out += '<option value="' + kv[0] + '"' + (kv[0] === sel ? " selected" : "") + ">" + kv[1] + "</option>"; }); return out; }
    function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;"); }
    function draw() {
      if (!rows.length) { g.grid.hidden = true; g.actions.hidden = true; return; }
      var w = Math.max.apply(null, rows.map(function (r) { return r.length; }));
      var h = "<tr>"; for (var i = 0; i < w; i++) h += '<th><select data-col="' + i + '">' + options(colmap[i] || "") + "</select></th>"; h += "</tr>";
      rows.forEach(function (r, ri) { h += '<tr class="' + (header && ri === 0 ? "snip-head-row" : "") + '">'; for (var i = 0; i < w; i++) { var d = (doubt[ri] || [])[i]; h += '<td' + (d ? ' class="snip-doubt"' : "") + '><input class="in" data-r="' + ri + '" data-c="' + i + '" value="' + esc(r[i] || "") + '"' + (d ? ' title="OCR was not sure: confirm or fix"' : "") + "></td>"; } h += "</tr>"; });
      g.grid.innerHTML = h; g.grid.hidden = false; g.actions.hidden = false;
      g.grid.querySelectorAll("select").forEach(function (s) { s.addEventListener("change", function () { colmap[+s.dataset.col] = s.value; g.onChange && g.onChange(); }); });
      g.grid.querySelectorAll("input").forEach(function (inp) { inp.addEventListener("input", function () { rows[+inp.dataset.r][+inp.dataset.c] = inp.value; inp.parentNode.classList.remove("snip-doubt"); g.onChange && g.onChange(); });
        ["focus", "click"].forEach(function (evn) { inp.addEventListener(evn, function () { pick(inp); }); }); });
      g.onChange && g.onChange();
    }
    function onResult(d, why) {
      if (!d) { g.msg.textContent = why; rows = []; draw(); g.text.hidden = true; return; }
      if (d.mode === "text") {
        if (target && target.dataset.r !== undefined) {           /* a cell was picked: the snip fills it (owner, 2026-10-16: correct a snip by drawing a box) */
          var r = +target.dataset.r, c = +target.dataset.c; rows[r][c] = d.text; target.value = d.text; target.parentNode.classList.remove("snip-doubt");
          g.msg.textContent = "Cell filled from the box" + (d.doubtful ? " (OCR was not sure: check it)" : "") + ". Click another cell to fix it, or the Table button to snip a table."; g.onChange && g.onChange(); return; }
        if (target) { var said = false; if (g.onText) { said = g.onText(d.text) === true; } else { target.value = d.text; target.dispatchEvent(new Event("input", {bubbles: true})); }
          if (!said) g.msg.textContent = "Filled from the box."; return; }
        g.text.textContent = d.text; g.text.hidden = false; g.msg.textContent = "Text snip" + (d.doubtful ? " (OCR was not sure: check it)" : "") + ":"; g.onText && g.onText(d.text); return; }
      g.text.hidden = true; rows = d.rows; doubt = d.doubt || []; colmap = d.colmap || []; header = !!d.header;
      var nd = doubt.reduce(function (n, r) { return n + r.filter(Boolean).length; }, 0);
      g.msg.textContent = rows.length + " row" + (rows.length === 1 ? "" : "s") + " read (" + (d.how === "ruled" ? "ruled table, exact" : d.how === "positions" ? "by position, exact" : "OCR") + ")" + (header ? ", first row = headings" : "") + (nd ? " · " + nd + " cell" + (nd === 1 ? "" : "s") + " to confirm (orange)" : "") + ". Name the columns, then go on.";
      draw();
    }
    if (g.category) g.category.addEventListener("change", function () { if (rows.length && sn) sn.reread(); });
    if (g.wide && g.split) g.wide.addEventListener("click", function () { g.split.classList.toggle("wide"); g.wide.innerHTML = '<span class="ms">' + (g.split.classList.contains("wide") ? "close_fullscreen</span>Side by side" : "open_in_full</span>Bigger"); });
    g.form.addEventListener("submit", function (e) {
      var r = sn && sn.rect(); if (!r || !rows.length) { e.preventDefault(); g.msg.textContent = "Snip a table first."; return; }
      g.hidden.rows.value = JSON.stringify(rows); g.hidden.colmap.value = JSON.stringify(colmap); g.hidden.header.value = header ? "1" : "0";
      g.hidden.x0.value = r.x0; g.hidden.y0.value = r.y0; g.hidden.x1.value = r.x1; g.hidden.y1.value = r.y1; });
    function listen(els) { (els || []).forEach(function (el) { if (!el || el._snipTarget) return; el._snipTarget = true;      /* from the start, not only after a table (2026-10-17) */
      ["focus", "click"].forEach(function (evn) { el.addEventListener(evn, function () { pick(el); }); }); }); }
    listen(g.targets);
    return {onResult: onResult, onBusy: function (b, text) { if (b) g.msg.textContent = text || "Reading the box…"; }, bind: function (s) { sn = s; }, targets: listen,
            rows: function () { return rows; }, colmap: function () { return colmap; }, header: function () { return header; }};
  }
  /* Paste a picture from the clipboard as the document (owner, 2026-10-17): Ctrl+V anywhere on the page, or the Paste button (asks the browser for the
     clipboard). The picture is sent through the page's own upload form, so it lands exactly as a chosen file would. o = {form, button, hint, field} */
  function paste(o) {
    if (!o.form || !o.button) return;
    function send(blob, type) {
      var fd = new FormData(o.form), ext = (type.split("/")[1] || "png").replace("jpeg", "jpg").split("+")[0];
      var now = new Date(), when = now.toLocaleDateString("en-PH", {day: "numeric", month: "short"}) + " " + now.toLocaleTimeString("en-PH", {hour: "numeric", minute: "2-digit"});
      fd.delete(o.field); fd.append(o.field, blob, "Pasted " + when + "." + ext);     /* "Pasted 17 Oct 10:30 AM.png", local time (2026-10-17) */
      o.button.disabled = true; if (o.hint) o.hint.textContent = "Opening the picture…";
      fetch(o.form.getAttribute("action") || location.href, {method: "POST", body: fd, credentials: "same-origin", redirect: "follow"})
        .then(function (r) { location.href = r.url || location.href; })
        .catch(function () { o.button.disabled = false; if (o.hint) o.hint.textContent = "Could not send the picture. Check the connection and try again."; });
    }
    function fromItems(items) {
      for (var i = 0; i < items.length; i++) { var it = items[i]; if (it.kind === "file" && it.type.indexOf("image/") === 0) { var b = it.getAsFile(); if (b) { send(b, it.type); return true; } } }
      return false;
    }
    document.addEventListener("paste", function (e) {
      if (e.target && e.target.closest && e.target.closest("input, textarea, select, [contenteditable]")) return;
      if (e.clipboardData && fromItems(e.clipboardData.items || [])) e.preventDefault();
    });
    o.button.addEventListener("click", function () {
      if (!navigator.clipboard || !navigator.clipboard.read) { if (o.hint) o.hint.textContent = "Press Ctrl+V on this page to paste the picture."; return; }
      navigator.clipboard.read().then(function (items) {
        var found = false;
        items.forEach(function (ci) { if (found) return; var t = (ci.types || []).filter(function (x) { return x.indexOf("image/") === 0; })[0]; if (t) { found = true; ci.getType(t).then(function (b) { send(b, t); }); } });
        if (!found && o.hint) o.hint.textContent = "No picture on the clipboard. Copy a picture or take a screenshot (Win+Shift+S) first.";
      }).catch(function () { if (o.hint) o.hint.textContent = "The browser did not allow reading the clipboard: press Ctrl+V on this page instead."; });
    });
  }

  return {start: start, grid: grid, paste: paste};
})();
