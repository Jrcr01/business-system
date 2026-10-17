/* Reading a barcode with the camera on every phone (owner, 2026-10-17: "we have iphones and ipads also"). Android Chrome has a reader built in
   (BarcodeDetector); Safari on iPhone and iPad does not, so there the ZXing library (Apache-2.0, vendor/zxing-0.21.3.min.js, kept on our own server)
   reads the frame instead -- loaded only on a device that needs it. slicksBarcode.ok(): a camera can be used; slicksBarcode.detector(): an object
   with detect(video | image) -> Promise of [{rawValue}], the same shape either way. */
var slicksBarcode = (function () {
  var lib = null, canvas = null, reader = null;
  function load() {
    if (window.ZXing) return Promise.resolve();
    if (!lib) lib = new Promise(function (ok, fail) {
      var s = document.createElement("script"); s.src = "/static/core/vendor/zxing-0.21.3.min.js"; s.onload = ok; s.onerror = fail; document.head.appendChild(s);
    });
    return lib;
  }
  function zx() {
    return {detect: function (src) {
      return load().then(function () {
        var w = src.videoWidth || src.naturalWidth || src.width, h = src.videoHeight || src.naturalHeight || src.height;
        if (!w || !h) return [];
        var k = Math.min(1, 960 / Math.max(w, h));                 /* smaller frames read faster and as well */
        canvas = canvas || document.createElement("canvas");
        canvas.width = Math.round(w * k); canvas.height = Math.round(h * k);
        canvas.getContext("2d").drawImage(src, 0, 0, canvas.width, canvas.height);
        if (!reader) {
          reader = new ZXing.MultiFormatReader();
          var hints = new Map(); hints.set(ZXing.DecodeHintType.TRY_HARDER, true); reader.setHints(hints);
        }
        try {
          var bmp = new ZXing.BinaryBitmap(new ZXing.HybridBinarizer(new ZXing.HTMLCanvasElementLuminanceSource(canvas)));
          return [{rawValue: reader.decode(bmp).getText()}];
        } catch (e) { return []; }                                  /* nothing readable in this frame */
      });
    }};
  }
  return {
    ok: function () { return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia) || "BarcodeDetector" in window; },
    native: function () { return "BarcodeDetector" in window; },
    detector: function () { return "BarcodeDetector" in window ? new BarcodeDetector() : zx(); },
    zx: zx                                                         /* the iPhone way, also on Android (tests) */
  };
})();
