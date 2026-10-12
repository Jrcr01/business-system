"""Phone notifications through the installed Slick's ERP app (owner, 2026-10-13: credit card reminders). The standard Web Push: the message is
encrypted for the one phone (RFC 8291, aes128gcm) and signed with this server's own key pair (VAPID, RFC 8292), then handed to the browser
maker's push service, which wakes the phone. No outside account and no cost. The key pair is made on first use and kept beside the database
(push_keys.json), never in the code.

  * public_key()            -- what a phone needs to subscribe (base64url)
  * subscribe / unsubscribe -- a phone allowed, or switched off, by its signed-in person
  * send(email, title, body, url) -- to every phone of that person; a phone the push service says is gone is forgotten"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from engines.common import get_conn, write_tx

CONTACT = "mailto:admin@slickstiresupply.com"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _keys_path() -> Path:
    import engines.db_engine as d
    return Path(d.DB_PATH).parent / "push_keys.json"


def _keys():
    """(private key, public key as 65 raw bytes): read, or made once."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    path = _keys_path()
    if path.exists():
        private = serialization.load_pem_private_key(json.loads(path.read_text())["private"].encode(), password=None)
    else:
        private = ec.generate_private_key(ec.SECP256R1())
        pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        path.write_text(json.dumps({"private": pem}))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    public = private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return private, public


def public_key() -> str:
    return _b64(_keys()[1])


def _vapid(endpoint: str) -> str:
    """The Authorization header: a short-lived signed note saying which server sends this."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    private, public = _keys()
    u = urlparse(endpoint)
    head = _b64(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = _b64(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": CONTACT}, separators=(",", ":")).encode())
    r, s = decode_dss_signature(private.sign(f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
    return f"vapid t={head}.{claims}.{_b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}, k={_b64(public)}"


def encrypt(message: bytes, p256dh: str, auth: str) -> bytes:
    """The message as only that phone can read it (RFC 8291)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    phone_public_raw, secret = _unb64(p256dh), _unb64(auth)
    phone_public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), phone_public_raw)
    ours = ec.generate_private_key(ec.SECP256R1())
    ours_raw = ours.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = ours.exchange(ec.ECDH(), phone_public)
    ikm = HKDF(hashes.SHA256(), 32, secret, b"WebPush: info\x00" + phone_public_raw + ours_raw).derive(shared)
    salt = os.urandom(16)
    key = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    return salt + (4096).to_bytes(4, "big") + bytes([len(ours_raw)]) + ours_raw + AESGCM(key).encrypt(nonce, message + b"\x02", None)


def subscribe(email: str, sub: dict, device: str = "") -> tuple[bool, str]:
    endpoint, keys = str(sub.get("endpoint") or ""), sub.get("keys") or {}
    if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
        return False, "This phone did not give what is needed for notifications."
    with write_tx() as conn:
        conn.execute("""INSERT INTO push_subscriptions (user_email, endpoint, p256dh, auth, device) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(endpoint) DO UPDATE SET user_email = excluded.user_email, p256dh = excluded.p256dh, auth = excluded.auth, device = excluded.device""",
                     (email.lower(), endpoint, keys["p256dh"], keys["auth"], device[:200]))
    return True, "Notifications are on for this phone."


def unsubscribe(email: str, endpoint: str) -> None:
    with write_tx() as conn:
        conn.execute("DELETE FROM push_subscriptions WHERE user_email = ? AND endpoint = ?", (email.lower(), endpoint))


def phones(email: str) -> int:
    with get_conn() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM push_subscriptions WHERE user_email = ?", (email.lower(),)).fetchone()[0])


def _post(endpoint: str, body: bytes, headers: dict) -> int:
    """The one call out (tests replace this)."""
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(urllib.request.Request(endpoint, data=body, headers=headers, method="POST"), timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def send(email: str, title: str, body: str, url: str = "/", tag: str = "") -> int:
    """To every phone of that person. Returns how many took it."""
    with get_conn() as conn:
        subs = conn.execute("SELECT sub_id, endpoint, p256dh, auth FROM push_subscriptions WHERE user_email = ?", (email.lower(),)).fetchall()
    message, sent, gone = json.dumps({"title": title, "body": body, "url": url, "tag": tag}).encode(), 0, []
    for sub_id, endpoint, p256dh, auth in subs:
        try:
            code = _post(endpoint, encrypt(message, p256dh, auth),
                         {"Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": "43200", "Urgency": "normal", "Authorization": _vapid(endpoint)})
        except Exception:                                        # noqa: BLE001 -- one phone that cannot be reached must not stop the others
            continue
        if code in (200, 201, 202):
            sent += 1
        elif code in (404, 410):                                 # the phone removed the app or switched notifications off
            gone.append(sub_id)
    if gone or sent:
        with write_tx() as conn:
            for sub_id in gone:
                conn.execute("DELETE FROM push_subscriptions WHERE sub_id = ?", (sub_id,))
            if sent:
                conn.execute("UPDATE push_subscriptions SET last_ok_at = CURRENT_TIMESTAMP WHERE user_email = ?", (email.lower(),))
    return sent
