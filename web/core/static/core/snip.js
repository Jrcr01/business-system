/* Snip from a document (owner, 2026-10-16): drag a box on the page picture; the server reads what is inside (engines/snip.py) and the grid shows it with
   its column names guessed from the destination's own fields; a doubtful OCR cell is orange and can be typed over; the form sends the grid on.
   slicksSnip.start(...) draws the box and reads; slicksSnip.grid(...) is the grid and the form, shared by Price lists, Bank loans and the SOA matcher. */
var slicksSnip = (function () {
  /* The box and its lines (owner, 2026-10-17: "make it sizable like DataSnipper", "I can fix the rows and columns"; mock-up ap_snip_rows_columns,
     answers 1-3 A). Drag on the page = a new box. Then: drag a corner or an edge handle to resize, drag inside (Box) to move; in Columns / Rows tap
     inside the box to add a line, drag a line to move it, its ✕ to remove it. A picture is straightened first (↺ ↻ to nudge). */
  function start(o) {
    var wrap = o.wrap, img = o.img, box = o.box, label = o.label, W = o.w, H = o.h, csrf = o.csrf, mode = "table", rect = null, data = null;
    var lines = {cols: [], rows: []}, edit = "box", drag = null, sel = null, history = [], side = null, exact = true, dirty = false, timer = null;
    var token = (o.readUrl.match(/snip\/([0-9a-f]{24})\//) || [])[1];
    var layer = document.createElement("div"); layer.className = "snip-layer"; wrap.appendChild(layer);
    var bar = document.createElement("div"); bar.className = "snip-edit"; bar.hidden = true;
    bar.innerHTML = '<span class="snip-seg"><button type="button" data-e="box" class="on" title="Drag inside the box to move it; drag a handle to resize"><span class="ms">crop_free</span>Box</button>' +
      '<button type="button" data-e="cols" title="Tap inside the box to add a column line"><span class="ms">view_column</span>Columns</button>' +
      '<button type="button" data-e="rows" title="Tap inside the box to add a row line"><span class="ms">table_rows</span>Rows</button></span>' +
      '<button type="button" data-a="even" title="The row lines spread evenly between the top and the bottom">Same row height</button>' +
      '<button type="button" data-a="undo"><span class="ms">undo</span>Undo</button><button type="button" data-a="reset" title="Back to the system\'s own lines">Start over</button>' +
      '<button type="button" data-a="read" class="snip-read"><span class="ms">refresh</span>Read again</button>';
    var tilt = document.createElement("span"); tilt.className = "snip-tilt"; tilt.hidden = true;
    tilt.innerHTML = '<button type="button" data-t="-0.5" title="Turn left a little"><span class="ms">rotate_left</span></button><span></span><button type="button" data-t="0.5" title="Turn right a little"><span class="ms">rotate_right</span></button>';
    wrap.parentNode.insertBefore(bar, wrap); bar.appendChild(tilt);
    function scale() { return W / (img.getBoundingClientRect().width || W); }
    function pos(e) { var r = img.getBoundingClientRect(); return {x: Math.max(0, Math.min(W, (e.clientX - r.left) / r.width * W)), y: Math.max(0, Math.min(H, (e.clientY - r.top) / r.height * H))}; }
    function pct(v, of) { return (v / of * 100) + "%"; }
    function norm(a, b) { return {x0: Math.min(a.x, b.x), y0: Math.min(a.y, b.y), x1: Math.max(a.x, b.x), y1: Math.max(a.y, b.y)}; }
    function draw() {
      if (!rect) { box.hidden = true; layer.innerHTML = ""; return; }
      box.hidden = false; box.style.left = pct(rect.x0, W); box.style.top = pct(rect.y0, H); box.style.width = pct(rect.x1 - rect.x0, W); box.style.height = pct(rect.y1 - rect.y0, H);
      var h = "";
      if (mode === "table") {
        lines.cols.forEach(function (x, i) { h += '<div class="snip-ln col' + (sel && sel.k === "cols" && sel.i === i ? " sel" : "") + '" data-k="cols" data-i="' + i + '" style="left:' + pct(x, W) + ";top:" + pct(rect.y0, H) + ";height:" + pct(rect.y1 - rect.y0, H) + '"><i></i>' + (sel && sel.k === "cols" && sel.i === i ? '<b data-del="1" title="Remove this line">✕</b>' : "") + "</div>"; });
        lines.rows.forEach(function (y, i) { h += '<div class="snip-ln row' + (sel && sel.k === "rows" && sel.i === i ? " sel" : "") + '" data-k="rows" data-i="' + i + '" style="top:' + pct(y, H) + ";left:" + pct(rect.x0, W) + ";width:" + pct(rect.x1 - rect.x0, W) + '"><i></i>' + (sel && sel.k === "rows" && sel.i === i ? '<b data-del="1" title="Remove this line">✕</b>' : "") + "</div>"; });
      }
      var mx = (rect.x0 + rect.x1) / 2, my = (rect.y0 + rect.y1) / 2;
      [["nw", rect.x0, rect.y0], ["n", mx, rect.y0], ["ne", rect.x1, rect.y0], ["e", rect.x1, my], ["se", rect.x1, rect.y1], ["s", mx, rect.y1], ["sw", rect.x0, rect.y1], ["w", rect.x0, my]]
        .forEach(function (p) { h += '<span class="snip-h h-' + p[0] + (side === p[0] ? " sel" : "") + '" data-h="' + p[0] + '" style="left:' + pct(p[1], W) + ";top:" + pct(p[2], H) + '"></span>'; });
      layer.innerHTML = h;
      bar.querySelector(".snip-read").classList.toggle("due", dirty);
    }
    function setEdit(e) { edit = e; bar.querySelectorAll("[data-e]").forEach(function (b) { b.classList.toggle("on", b.dataset.e === e); }); wrap.dataset.edit = e; }
    function editButtons(on) { bar.querySelectorAll("[data-e],[data-a]").forEach(function (b) { b.hidden = !on; }); bar.querySelector(".snip-seg").hidden = !on; bar.hidden = !on && tilt.hidden; }
    function setMode(m) { mode = m; o.modeButtons.forEach(function (b) { b.classList.toggle("on", b.dataset.mode === m); }); editButtons(m === "table" && !!rect && !!data); draw(); }
    function remember() { history.push(JSON.stringify({rect: rect, lines: lines})); if (history.length > 40) history.shift(); }
    function changed() { dirty = true; draw(); clearTimeout(timer); if (exact) timer = setTimeout(function () { read({lines: lines}); }, 600); }   /* a text PDF reads again by itself; a picture on Read again */
    o.modeButtons.forEach(function (b) { b.addEventListener("click", function () { setMode(b.dataset.mode); if (rect) read(); }); });
    bar.addEventListener("click", function (e) {
      var b = e.target.closest("button"); if (!b) return;
      if (b.dataset.e) { setEdit(b.dataset.e); return; }
      if (b.dataset.a === "undo") { var last = history.pop(); if (last) { var s = JSON.parse(last); rect = s.rect; lines = s.lines; sel = null; changed(); } return; }
      if (b.dataset.a === "reset") { remember(); sel = null; dirty = false; read(); return; }
      if (b.dataset.a === "read") { read({lines: lines}); return; }
      if (b.dataset.a === "even" && rect) { remember(); var n = lines.rows.length + 1, step = (rect.y1 - rect.y0) / n; lines.rows = []; for (var i = 1; i < n; i++) lines.rows.push(rect.y0 + step * i); changed(); return; }
      if (b.dataset.t) { turn(+b.dataset.t); }
    });
    function turn(delta) {
      fetch("/snip/" + token + "/angle/", {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json", "X-CSRFToken": csrf}, body: JSON.stringify({page: o.page, delta: delta})})
        .then(function (r) { return r.json(); }).then(function (d) { if (!d.ok) return; showTilt(d); img.src = img.src.split("?")[0] + "?a=" + d.angle;
          rect = null; data = null; lines = {cols: [], rows: []}; editButtons(false); label.textContent = ""; draw(); o.onResult(null, "The page was turned: draw the box again."); });
    }
    function showTilt(d) { tilt.hidden = !d.can; if (d.can) bar.hidden = false; if (d.can) tilt.querySelector("span").textContent = d.angle ? "straightened " + (d.angle > 0 ? "+" : "") + d.angle + "°" : "straight"; }
    if (token) fetch("/snip/" + token + "/angle/?p=" + o.page, {credentials: "same-origin"}).then(function (r) { return r.json(); }).then(function (d) { if (d.ok) showTilt(d); }).catch(function () {});
    function showEdit() { editButtons(true); }
    var reading = 0;
    function read(extra) {
      var mine = ++reading; clearTimeout(timer);
      label.textContent = "Reading…"; o.onBusy && o.onBusy(true, "Reading the box…");
      function got(d) {
        if (mine !== reading) return;
        if (d.pending) {                                         /* OCR in the background: ask again in a moment */
          o.onBusy && o.onBusy(true, "Reading the picture (OCR)… this can take a minute or two.");
          setTimeout(function () { post({job: d.job, category: o.category ? o.category() : ""}).then(got).catch(fail); }, 2500); return;
        }
        data = d; o.onBusy && o.onBusy(false); dirty = false;
        if (!d.ok) { label.textContent = ""; o.onResult(null, d.msg || "Nothing read."); draw(); return; }
        if (d.lines) lines = {cols: d.lines.cols || [], rows: d.lines.rows || []};
        if (d.how === "ocr") exact = false; else if (d.how === "ruled" || d.how === "positions") exact = true;
        label.textContent = mode === "text" ? "Text" : "Table · " + d.rows.length + " row" + (d.rows.length === 1 ? "" : "s") + " · " + (lines.cols.length + 1) + " column" + (lines.cols.length ? "s" : "");
        if (mode === "table") showEdit();
        draw(); o.onResult(d, "");
      }
      function fail() { if (mine !== reading) return; o.onBusy && o.onBusy(false); label.textContent = ""; o.onResult(null, "Could not reach the server. Check the connection and try again."); }
      function post(body) { return fetch(o.readUrl, {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json", "X-CSRFToken": csrf}, body: JSON.stringify(body)}).then(function (r) { return r.json(); }); }
      var body = {page: o.page, mode: mode, box: rect, category: o.category ? o.category() : ""};
      if (extra && mode === "table") { body.lines = extra.lines; if (extra.merge) body.merge = true; }
      post(body).then(got).catch(fail);
    }
    function inside(p) { return rect && p.x > rect.x0 && p.x < rect.x1 && p.y > rect.y0 && p.y < rect.y1; }
    function clip() { lines.cols = lines.cols.filter(function (x) { return x > rect.x0 + 3 && x < rect.x1 - 3; }); lines.rows = lines.rows.filter(function (y) { return y > rect.y0 + 3 && y < rect.y1 - 3; }); }
    wrap.addEventListener("pointerdown", function (e) {
      if (e.button > 0 || e.target.closest("a, button")) return;
      var p = pos(e), t = e.target, h = t.dataset && t.dataset.h, ln = t.closest(".snip-ln");
      if (t.dataset && t.dataset.del && sel) { remember(); lines[sel.k].splice(sel.i, 1); sel = null; changed(); e.preventDefault(); return; }
      if (h) { remember(); side = h; drag = {kind: "resize", h: h, from: p, r: JSON.parse(JSON.stringify(rect))}; }
      else if (ln && mode === "table") { remember(); sel = {k: ln.dataset.k, i: +ln.dataset.i}; drag = {kind: "line", k: sel.k, i: sel.i}; draw(); }
      else if (inside(p) && data && mode === "table" && edit !== "box") { remember(); var k = edit; lines[k].push(k === "cols" ? p.x : p.y); lines[k].sort(function (a, b) { return a - b; });
        sel = {k: k, i: lines[k].indexOf(k === "cols" ? p.x : p.y)}; drag = {kind: "line", k: k, i: sel.i}; changed(); }
      else if (inside(p) && data && edit === "box") { remember(); drag = {kind: "move", from: p, r: JSON.parse(JSON.stringify(rect)), l: JSON.parse(JSON.stringify(lines))}; }
      else { side = null; sel = null; drag = {kind: "new", from: p}; rect = norm(p, p); lines = {cols: [], rows: []}; data = null; editButtons(false); draw(); }
      try { wrap.setPointerCapture(e.pointerId); } catch (x) {}
      e.preventDefault();
    });
    var lastPt = null, scrolling = null;
    function autoscroll() {                                      /* the box runs past the screen: the page follows the pointer (owner, 2026-10-17) */
      if (!drag || !lastPt || (drag.kind !== "new" && drag.kind !== "resize")) { scrolling = null; return; }
      var sc = o.scroller, top = 0, bottom = window.innerHeight;
      if (sc && sc.scrollHeight > sc.clientHeight) { var br = sc.getBoundingClientRect(); top = br.top; bottom = br.bottom; }
      var edge = 48, dy = lastPt.clientY > bottom - edge ? 14 : lastPt.clientY < top + edge ? -14 : 0;
      if (dy) { if (sc && sc.scrollHeight > sc.clientHeight) sc.scrollTop += dy; else window.scrollBy(0, dy); moveTo(lastPt); }
      scrolling = requestAnimationFrame(autoscroll);
    }
    function moveTo(e) {
      var p = pos(e);
      if (drag.kind === "new") rect = norm(drag.from, p);
      else if (drag.kind === "resize") { var r = JSON.parse(JSON.stringify(drag.r)), hh = drag.h;
        if (hh.indexOf("w") >= 0) r.x0 = Math.min(p.x, r.x1 - 8); if (hh.indexOf("e") >= 0) r.x1 = Math.max(p.x, r.x0 + 8);
        if (hh.indexOf("n") >= 0) r.y0 = Math.min(p.y, r.y1 - 8); if (hh.indexOf("s") >= 0) r.y1 = Math.max(p.y, r.y0 + 8); rect = r; }
      else if (drag.kind === "move") { var dx = p.x - drag.from.x, dy = p.y - drag.from.y; dx = Math.max(-drag.r.x0, Math.min(W - drag.r.x1, dx)); dy = Math.max(-drag.r.y0, Math.min(H - drag.r.y1, dy));
        rect = {x0: drag.r.x0 + dx, y0: drag.r.y0 + dy, x1: drag.r.x1 + dx, y1: drag.r.y1 + dy};
        lines = {cols: drag.l.cols.map(function (x) { return x + dx; }), rows: drag.l.rows.map(function (y) { return y + dy; })}; }
      else if (drag.kind === "line") { var v = drag.k === "cols" ? Math.max(rect.x0 + 2, Math.min(rect.x1 - 2, p.x)) : Math.max(rect.y0 + 2, Math.min(rect.y1 - 2, p.y)); lines[drag.k][drag.i] = v; }
      draw();
    }
    wrap.addEventListener("pointermove", function (e) { if (!drag) return; lastPt = e; moveTo(e); if (!scrolling && (drag.kind === "new" || drag.kind === "resize")) scrolling = requestAnimationFrame(autoscroll); });
    function end(e) {
      if (!drag) return; var d = drag; drag = null; lastPt = null;
      if (d.kind === "new") { if (rect.x1 - rect.x0 > 3 && rect.y1 - rect.y0 > 3) read(); else { rect = null; draw(); } return; }
      if (d.kind === "resize") { clip(); if (mode === "table" && data) read({lines: lines, merge: true}); else read(); return; }   /* the new part of the box gets the system's lines */
      if (d.kind === "move") { if (mode === "table" && data) read({lines: lines}); else read(); return; }
      if (d.kind === "line") { var k = d.k, v = lines[k][d.i]; lines[k].sort(function (a, b) { return a - b; }); sel = {k: k, i: lines[k].indexOf(v)}; changed(); }
    }
    wrap.addEventListener("pointerup", end); wrap.addEventListener("pointercancel", end);
    document.addEventListener("keydown", function (e) {                    /* arrow keys nudge the side last grabbed: 1 pixel, Shift 10 */
      if (!rect || !side || /INPUT|SELECT|TEXTAREA/.test((e.target || {}).tagName || "")) return;
      var k = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]}[e.key]; if (!k) return;
      var n = (e.shiftKey ? 10 : 1) * scale(); e.preventDefault();
      if (side.indexOf("w") >= 0) rect.x0 = Math.min(rect.x1 - 8, Math.max(0, rect.x0 + k[0] * n)); if (side.indexOf("e") >= 0) rect.x1 = Math.max(rect.x0 + 8, Math.min(W, rect.x1 + k[0] * n));
      if (side.indexOf("n") >= 0) rect.y0 = Math.min(rect.y1 - 8, Math.max(0, rect.y0 + k[1] * n)); if (side.indexOf("s") >= 0) rect.y1 = Math.max(rect.y0 + 8, Math.min(H, rect.y1 + k[1] * n));
      clip(); draw(); clearTimeout(timer); timer = setTimeout(function () { if (mode === "table" && data) read({lines: lines, merge: true}); else read(); }, 700);
    });
    setEdit("box"); editButtons(false);
    return {rect: function () { return rect; }, data: function () { return data; }, mode: function () { return mode; }, reread: function () { read(data && mode === "table" ? {lines: lines} : null); }, setMode: setMode};
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
      g.msg.textContent = rows.length + " row" + (rows.length === 1 ? "" : "s") + " read (" + (d.how === "ruled" ? "ruled table, exact" : d.how === "positions" ? "by position, exact" : d.how === "lines" ? "by your rows and columns" : "OCR") + ")" + (header ? ", first row = headings" : "") + (nd ? " · " + nd + " cell" + (nd === 1 ? "" : "s") + " to confirm (orange)" : "") + ". Name the columns, then go on.";
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
