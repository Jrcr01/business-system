/* Fingerprint sign-in (owner, 2026-10-13): the browser's side of engines/fingerprint_signin.py. The device's own fingerprint, face or screen lock
   releases a key kept in the device; nothing about the finger is sent. */
var slicksFp = (function () {
  function b64(buf) { var a = new Uint8Array(buf), s = ""; for (var i = 0; i < a.length; i++) s += String.fromCharCode(a[i]); return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); }
  function bytes(t) { var s = atob((t + "===".slice((t.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/")), a = new Uint8Array(s.length); for (var i = 0; i < s.length; i++) a[i] = s.charCodeAt(i); return a; }
  function ids(list) { return (list || []).map(function (c) { return {type: c.type, id: bytes(c.id)}; }); }
  function post(url, token, data) {
    return fetch(url, {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json", "X-CSRFToken": token}, body: JSON.stringify(data)})
      .then(function (r) { return r.json(); });
  }
  function get(url) { return fetch(url, {credentials: "same-origin"}).then(function (r) { return r.json(); }); }
  function say(el, text, bad) { if (el) { el.textContent = text; el.style.color = bad ? "var(--fg-b3261e)" : "var(--fg-0b6e4f)"; } }

  /* Can this device do it at all (a fingerprint reader, face unlock or a screen lock the browser may use)? */
  function available() {
    if (!window.PublicKeyCredential || !PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable) return Promise.resolve(false);
    return PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable().catch(function () { return false; });
  }

  function turnOn(token, msg) {
    say(msg, "Touch the sensor when the device asks.");
    return get("/fingerprint/options/").then(function (o) {
      if (!o.ok) throw new Error(o.msg);
      var pk = o.publicKey;
      pk.challenge = bytes(pk.challenge); pk.user.id = bytes(pk.user.id); pk.excludeCredentials = ids(pk.excludeCredentials);
      return navigator.credentials.create({publicKey: pk});
    }).then(function (c) {
      var r = c.response;
      if (!r.getPublicKey || !r.getAuthenticatorData) throw new Error("This browser is too old for fingerprint sign-in. Update it and try again.");
      return post("/fingerprint/add/", token, {id: b64(c.rawId), clientDataJSON: b64(r.clientDataJSON), authenticatorData: b64(r.getAuthenticatorData()),
                                               publicKey: b64(r.getPublicKey()), alg: r.getPublicKeyAlgorithm()});
    }).then(function (res) { say(msg, res.msg, !res.ok); return !!res.ok; })
      .catch(function (e) { say(msg, e && e.name === "NotAllowedError" ? "Cancelled. Nothing was turned on." : (e && e.name === "InvalidStateError" ?
        "Fingerprint sign-in is already on for this device." : ((e && e.message) || "It could not be turned on.")), true); return false; });
  }

  function signIn(token, next, msg) {
    say(msg, "Touch the sensor when the device asks.");
    return get("/login/fingerprint/options/").then(function (o) {
      if (!o.ok) throw new Error(o.msg);
      var pk = o.publicKey;
      pk.challenge = bytes(pk.challenge); pk.allowCredentials = ids(pk.allowCredentials);
      return navigator.credentials.get({publicKey: pk});
    }).then(function (c) {
      var r = c.response;
      return post("/login/fingerprint/", token, {id: b64(c.rawId), clientDataJSON: b64(r.clientDataJSON), authenticatorData: b64(r.authenticatorData),
                                                 signature: b64(r.signature), next: next || "/"});
    }).then(function (res) {
      if (res.ok) { window.location.href = res.next || "/"; return; }
      say(msg, res.msg || "Not verified. Sign in with your password.", true);
    }).catch(function (e) { say(msg, e && e.name === "NotAllowedError" ? "Cancelled. Type your password, or try again." : ((e && e.message) || "Not verified. Sign in with your password."), true); });
  }

  return {available: available, turnOn: turnOn, signIn: signIn};
})();
