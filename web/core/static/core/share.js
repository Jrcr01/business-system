/* Share (owner, 2026-10-17; mock-up ak_phone_features #4): a button marked data-share-text (and data-share-url / data-share-title) opens the phone's
   own share sheet -- Viber, Messenger, SMS, email. Where a browser has none (most laptops), the text is copied instead. Never a cost or a margin. */
(function () {
  function note(btn, text) { var old = btn.dataset.label || btn.innerHTML; btn.dataset.label = old; btn.textContent = text; setTimeout(function () { btn.innerHTML = old; }, 1800); }
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-share-text], [data-share-url]"); if (!btn) return;
    e.preventDefault();
    var text = btn.dataset.shareText || "", url = btn.dataset.shareUrl || "", title = btn.dataset.shareTitle || "Slick's Tire Supply";
    if (navigator.share) { navigator.share({title: title, text: text, url: url || undefined}).catch(function () {}); return; }
    var all = (text + (url ? " " + url : "")).trim();
    (navigator.clipboard ? navigator.clipboard.writeText(all) : Promise.reject()).then(function () { note(btn, "Copied: paste it in Viber"); })
      .catch(function () { window.prompt("Copy this:", all); });
  });
})();
