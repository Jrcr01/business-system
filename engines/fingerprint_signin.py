"""Sign in with the device's own fingerprint or face unlock (owner, 2026-10-13: "For mobile view it is possible to have a biometrics for logging in?",
then "Go" on docs/mockups/r_fingerprint_signin.html). The standard behind it is WebAuthn: the device keeps a private key that only its fingerprint,
face or screen lock releases, and the system keeps the matching public key. The fingerprint itself never leaves the device. It stands in for typing
the password on that one device; the password stays for a new device. Cloudflare in front, and the 6-digit code when it is switched on, are untouched.

A changed or reset password and "Sign out everywhere" remove the person's devices (each is turned on again after a password sign-in). The staff
Scanner account never uses it: anyone whose finger is saved in that shared phone would sign in as it."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets

from engines.clock import manila_now
from engines.common import get_conn, write_tx

STAMP = "%Y-%m-%d %H:%M:%S"
NOT_VERIFIED = "The fingerprint could not be verified. Sign in with your password."
ALGS = (-7, -257, -8)                 # ES256 (phones), RS256 (Windows Hello), EdDSA


def on() -> bool:
    from engines.settings_registry import int_value
    return int_value("signin_fingerprint") == 1


def allowed(user: dict | None) -> bool:
    return bool(user) and user.get("role") != "scanner" and on()


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text: str) -> bytes:
    text = str(text or "")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_challenge() -> str:
    return b64(secrets.token_bytes(32))


def _client(raw: bytes, kind: str, challenge: str, origin: str) -> bool:
    """The browser's own note of what it signed: the right kind, our challenge, our site."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    return (bool(challenge) and data.get("type") == kind and secrets.compare_digest(str(data.get("challenge", "")), challenge)
            and data.get("origin") == origin)


def _device_data(auth: bytes, rp_id: str) -> bool:
    """The device's data: made for this site, by a person present (bit 1) who was verified by fingerprint, face or screen lock (bit 4)."""
    return len(auth) >= 37 and auth[:32] == hashlib.sha256(rp_id.encode()).digest() and auth[32] & 0x05 == 0x05


def _audit(conn, action: str, user_id: int, text: str, who: str | None = None) -> None:
    conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (COALESCE(?, acting_user()), ?, 'app_users', ?, ?)",
                 (who, action, int(user_id), text))


def register(user: dict, payload: dict, challenge: str, origin: str, rp_id: str, device: str = "") -> tuple[bool, str, str | None]:
    """Turn it on for this device. Returns (ok, message, the key's id for this device's cookie)."""
    if not allowed(user):
        return False, "Fingerprint sign-in is not on for this account.", None
    try:
        client, auth, key = unb64(payload["clientDataJSON"]), unb64(payload["authenticatorData"]), unb64(payload["publicKey"])
        cred_id, alg = unb64(payload["id"]), int(payload["alg"])
    except (KeyError, TypeError, ValueError):
        return False, "This device did not answer as expected.", None
    if not _client(client, "webauthn.create", challenge, origin) or not _device_data(auth, rp_id) or not auth[32] & 0x40 or alg not in ALGS:
        return False, "This device could not be verified. Nothing was turned on.", None
    n = int.from_bytes(auth[53:55], "big")
    if not cred_id or auth[55:55 + n] != cred_id:
        return False, "This device could not be verified. Nothing was turned on.", None
    try:
        from cryptography.hazmat.primitives.serialization import load_der_public_key
        load_der_public_key(key)
    except Exception:                                   # noqa: BLE001 -- any unreadable key is refused the same way
        return False, "This device's key could not be read. Nothing was turned on.", None
    now = manila_now().strftime(STAMP)
    with write_tx() as conn:
        if conn.execute("SELECT 1 FROM sign_in_keys WHERE credential_id = ?", (b64(cred_id),)).fetchone():
            return False, "Fingerprint sign-in is already on for this device.", None
        conn.execute("""INSERT INTO sign_in_keys (user_id, credential_id, public_key, alg, sign_count, device, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                     (int(user["user_id"]), b64(cred_id), key, alg, int.from_bytes(auth[33:37], "big"), (device or "")[:80] or None, now))
        _audit(conn, "FINGERPRINT_TURNED_ON", int(user["user_id"]), f"{user['email']}: {device or 'a device'}", who=user["email"])
    return True, "Fingerprint sign-in is on for this device. Next time, touch the sensor instead of typing your password.", b64(cred_id)


def key_ids(user_id: int) -> list[str]:
    with get_conn() as conn:
        return [r[0] for r in conn.execute("SELECT credential_id FROM sign_in_keys WHERE user_id = ? ORDER BY key_id", (int(user_id),))]


def devices(user_id: int | None = None) -> list[dict]:
    """The devices with it turned on: one person's, or everyone's."""
    from engines.auth_service import shown_time
    where, params = ("WHERE k.user_id = ?", (int(user_id),)) if user_id else ("", ())
    with get_conn() as conn:
        rows = conn.execute(f"""SELECT k.key_id, k.user_id, u.name, k.device, k.created_at, k.last_used_at, k.credential_id FROM sign_in_keys k
                                JOIN app_users u ON u.user_id = k.user_id {where} ORDER BY u.name, k.key_id""", params).fetchall()
    return [{"key_id": int(a), "user_id": int(b), "name": c, "device": d or "A device", "since": shown_time(e), "used": shown_time(f) if f else "not yet",
             "credential_id": g} for a, b, c, d, e, f, g in rows]


def _verify_signature(key: bytes, alg: int, signature: bytes, signed: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
    from cryptography.hazmat.primitives.serialization import load_der_public_key
    try:
        public = load_der_public_key(key)
        if alg == -7 and isinstance(public, ec.EllipticCurvePublicKey):
            public.verify(signature, signed, ec.ECDSA(hashes.SHA256()))
        elif alg == -257 and isinstance(public, rsa.RSAPublicKey):
            public.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
        elif alg == -8 and isinstance(public, ed25519.Ed25519PublicKey):
            public.verify(signature, signed)
        else:
            return False
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True


def verify(user_id: int, payload: dict, challenge: str, origin: str, rp_id: str) -> tuple[bool, dict | None, str]:
    """A sign-in by fingerprint: (ok, the person, message). Refused the same way whatever was wrong; a locked or switched-off account stays so."""
    from engines.auth_service import get_user
    if not on():
        return False, None, NOT_VERIFIED
    try:
        client, auth, signature = unb64(payload["clientDataJSON"]), unb64(payload["authenticatorData"]), unb64(payload["signature"])
        cred = b64(unb64(payload["id"]))
    except (KeyError, TypeError, ValueError):
        return False, None, NOT_VERIFIED
    user = get_user(int(user_id))
    if not allowed(user) or not user["is_active"]:
        return False, None, NOT_VERIFIED
    now = manila_now()
    with write_tx() as conn:
        locked = conn.execute("SELECT locked_until FROM app_users WHERE user_id = ?", (int(user_id),)).fetchone()[0]
        if locked and now.strftime(STAMP) < str(locked):
            return False, None, "This account is locked for now. Try again later."
        row = conn.execute("SELECT key_id, public_key, alg, sign_count FROM sign_in_keys WHERE user_id = ? AND credential_id = ?", (int(user_id), cred)).fetchone()
        if row is None or not _client(client, "webauthn.get", challenge, origin) or not _device_data(auth, rp_id):
            return False, None, NOT_VERIFIED
        if not _verify_signature(bytes(row[1]), int(row[2]), signature, auth + hashlib.sha256(client).digest()):
            return False, None, NOT_VERIFIED
        count = int.from_bytes(auth[33:37], "big")
        if (count or int(row[3])) and count <= int(row[3]):         # the device's own counter went backwards: a copied key
            return False, None, NOT_VERIFIED
        conn.execute("UPDATE sign_in_keys SET sign_count = ?, last_used_at = ? WHERE key_id = ?", (count, now.strftime(STAMP), int(row[0])))
    return True, user, ""


def remove(key_id: int, user_id: int | None = None) -> tuple[bool, str]:
    """Turn it off for one device: the person's own (user_id given), or anyone's by the System admin."""
    if user_id is None:
        from engines.permissions import require
        ok, why = require("users.manage")
        if not ok:
            return False, why
    with write_tx() as conn:
        row = conn.execute("SELECT k.user_id, u.email, k.device FROM sign_in_keys k JOIN app_users u ON u.user_id = k.user_id WHERE k.key_id = ?", (int(key_id),)).fetchone()
        if row is None or (user_id is not None and int(row[0]) != int(user_id)):
            return False, "No such device."
        conn.execute("DELETE FROM sign_in_keys WHERE key_id = ?", (int(key_id),))
        _audit(conn, "FINGERPRINT_REMOVED", int(row[0]), f"{row[1]}: {row[2] or 'a device'}")
    return True, f"Fingerprint sign-in removed for {row[2] or 'that device'}. It signs in with the password again."


def remove_all(conn, user_id: int) -> int:
    """Inside the caller's own save: a changed or reset password, or Sign out everywhere, ends fingerprint sign-in on every device of the person."""
    try:
        return conn.execute("DELETE FROM sign_in_keys WHERE user_id = ?", (int(user_id),)).rowcount
    except Exception:                                   # noqa: BLE001 -- a database from before migration 099
        return 0
