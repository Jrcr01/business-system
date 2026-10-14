/* Click a column heading to sort the list (owner, 2026-10-15: "why these can't be sorted?" -> every list). Click again for the other way, a third time
   for the order the page came in. Only what is on the screen is rearranged: nothing is saved, nothing is asked of the server.
   A list in groups (a sub-heading row: a day on Daily payables and Reports, a supplier on Invoices) sorts inside each group; the sub-headings and total
   rows (a heading cell, or a top border) stay where they are. Left alone: entry grids (boxes to type in), headings spanning columns, a list marked
   "nosort". Empty cells ("—") go last either way. */
(function () {
  var coll = new Intl.Collator(undefined, {numeric: true, sensitivity: "base"});
  var DATE = /^(\d{1,2})\/(\d{1,2})\/(\d{4})(?:\D+(\d{1,2}):(\d{2}))?/;

  function cellText(td) { return (td ? (td.getAttribute("data-sort") || td.textContent) : "").replace(/\s+/g, " ").trim(); }
  function empty(s) { return s === "" || s === "—" || s === "-" || s === "–"; }
  function asNumber(s) {
    var neg = /^\(.*\)$/.test(s);
    var t = s.replace(/[₱,%\s()]/g, "");
    if (!/^[-+]?\d+(\.\d+)?$/.test(t)) return null;
    var n = parseFloat(t);
    return neg ? -n : n;
  }
  function asDate(s) {
    var m = DATE.exec(s);
    if (m) return new Date(+m[3], +m[1] - 1, +m[2], +(m[4] || 0), +(m[5] || 0)).getTime();
    if (/^[A-Z][a-z]{2} \d{4}$/.test(s)) { var d = Date.parse("1 " + s); return isNaN(d) ? null : d; }      // "Oct 2026"
    return null;
  }

  function setUp(table) {
    if (table.classList.contains("nosort") || table.dataset.sortReady) return;
    var rows = Array.prototype.slice.call(table.rows);
    if (rows.length < 3) return;
    var heads = Array.prototype.slice.call(rows[0].cells);
    if (!heads.length || heads.some(function (c) { return c.tagName !== "TH" || c.colSpan > 1; })) return;
    var groups = [], cur = null;                                     // runs of ordinary rows between sub-headings / totals
    for (var i = 1; i < rows.length; i++) {
      var r = rows[i];
      var cells = Array.prototype.slice.call(r.cells);
      var plain = cells.length === heads.length && !cells.some(function (c) { return c.tagName === "TH" || c.colSpan > 1; })
                  && !/border-top/.test(r.getAttribute("style") || "");
      if (plain && r.querySelector("input:not([type=checkbox]):not([type=hidden]):not([type=radio]), select, textarea")) return;   // an entry grid
      if (plain && cur && cur.rows[cur.rows.length - 1].nextElementSibling === r) { cur.rows.push(r); continue; }
      if (plain) { cur = {rows: [r]}; groups.push(cur); continue; }
      cur = null;
    }
    groups = groups.filter(function (g) { return g.rows.length > 1; });
    if (!groups.length) return;
    table.dataset.sortReady = "1";
    groups.forEach(function (g) {
      g.parent = g.rows[0].parentNode;
      g.anchor = g.rows[g.rows.length - 1].nextElementSibling;       // the sub-heading or total that follows the group: never moves
      g.rows.forEach(function (r, k) { r.dataset.sortOrig = k; });
    });
    heads.forEach(function (th, col) {
      if (!th.textContent.trim()) return;
      th.classList.add("sortable");
      th.tabIndex = 0;
      th.title = "Sort by " + th.textContent.trim();
      var arrow = document.createElement("span");
      arrow.className = "sort-arrow";
      arrow.setAttribute("aria-hidden", "true");
      th.appendChild(arrow);
      function go() {
        var state = th.getAttribute("aria-sort");
        var next = state === "ascending" ? "descending" : state === "descending" ? "none" : "ascending";
        heads.forEach(function (h) { h.removeAttribute("aria-sort"); var a = h.querySelector(".sort-arrow"); if (a) a.textContent = ""; });
        if (next !== "none") { th.setAttribute("aria-sort", next); arrow.textContent = next === "ascending" ? " ▲" : " ▼"; }
        var every = [];
        groups.forEach(function (g) { every = every.concat(g.rows); });
        var filled = every.map(function (r) { return cellText(r.cells[col]); }).filter(function (s) { return !empty(s); });
        var kind = filled.length && filled.every(function (s) { return asNumber(s) !== null; }) ? "n"
                 : filled.length && filled.every(function (s) { return asDate(s) !== null; }) ? "d" : "t";
        var dir = next === "ascending" ? 1 : -1;
        groups.forEach(function (g) {
          var keyed = g.rows.map(function (r) {
            var s = cellText(r.cells[col]);
            return {r: r, e: empty(s), v: kind === "n" ? asNumber(s) : kind === "d" ? asDate(s) : s, o: +r.dataset.sortOrig};
          });
          keyed.sort(function (a, b) {
            if (next === "none") return a.o - b.o;
            if (a.e !== b.e) return a.e ? 1 : -1;                     // empty cells last, either way
            var c = a.e ? 0 : (kind === "t" ? coll.compare(a.v, b.v) : a.v - b.v);
            return c !== 0 ? c * dir : a.o - b.o;
          });
          keyed.forEach(function (k) { g.parent.insertBefore(k.r, g.anchor); });
        });
      }
      th.addEventListener("click", function (e) { if (e.target.closest("a, button, input, label")) return; go(); });
      th.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
    });
  }

  function all() { document.querySelectorAll("table.list").forEach(setUp); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", all); else all();
  window.slicksSort = all;                                           // for a list drawn later by the page itself
})();
