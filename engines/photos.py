"""Photos kept as proof (owner, 2026-10-17: "All four" of docs/mockups/ak_phone_features.html): the receipt of an expense, the front and back of a
customer's check. Taken with the phone's camera (or chosen from a file on the laptop), made smaller on the way in (longest side 1600 px, JPEG),
stored beside the database in photos/<yyyy-mm>/, backed up by the nightly job and the laptop's pull. Unlike the snip, these are kept."""
from __future__ import annotations

import io
import os
import secrets

from engines.clock import manila_now
from engines.common import DB_PATH, get_conn, write_tx

MAX_SIDE, QUALITY, MAX_IN = 1600, 80, 25 * 1024 * 1024
KINDS = {"receipt": "Receipt", "check_front": "Check, front", "check_back": "Check, back"}


def folder() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "photos")


def save(upload, ref_type: str, ref_id: int, kind: str) -> tuple[bool, str]:
    """One picture from a form (a Django UploadedFile, or None = nothing to do)."""
    if upload is None or not getattr(upload, "size", 0):
        return True, ""
    if kind not in KINDS or ref_type not in ("expenses", "collections"):
        return False, "Unknown kind of photo."
    if upload.size > MAX_IN:
        return False, "That picture is too large (over 25 MB)."
    from PIL import Image, ImageOps
    try:
        im = Image.open(upload)
        im = ImageOps.exif_transpose(im)                       # the way the phone was held
        im = im.convert("RGB")
    except Exception:                                          # noqa: BLE001 -- not a picture
        return False, "That file is not a picture. Take a photo, or choose a JPG or PNG."
    im.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=QUALITY, optimize=True)
    month = manila_now().strftime("%Y-%m")
    rel = os.path.join("photos", month, secrets.token_hex(12) + ".jpg").replace("\\", "/")
    full = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(buf.getvalue())
    with write_tx() as conn:
        pid = conn.execute("INSERT INTO photos (ref_type, ref_id, kind, file, bytes, created_by) VALUES (?, ?, ?, ?, ?, acting_user()) RETURNING photo_id",
                           (ref_type, int(ref_id), kind, rel, len(buf.getvalue()))).fetchone()[0]
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'PHOTO_KEPT', ?, ?, ?)",
                     (ref_type, int(ref_id), f"{KINDS[kind]} photo #{pid} ({len(buf.getvalue()) // 1024} KB)"))
    return True, f"{KINDS[kind]} photo kept."


def for_refs(ref_type: str, ids) -> dict[int, list[dict]]:
    ids = [int(i) for i in ids]
    if not ids:
        return {}
    with get_conn() as conn:
        rows = conn.execute(f"SELECT photo_id, ref_id, kind FROM photos WHERE ref_type = ? AND ref_id IN ({','.join('?' * len(ids))}) ORDER BY photo_id",
                            (ref_type, *ids)).fetchall()
    out: dict[int, list[dict]] = {}
    for pid, rid, kind in rows:
        out.setdefault(int(rid), []).append({"photo_id": int(pid), "kind": kind, "label": KINDS[kind]})
    return out


def one(photo_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT ref_type, file FROM photos WHERE photo_id = ?", (int(photo_id),)).fetchone()
    if not row:
        return None
    full = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), row[1])
    return {"ref_type": row[0], "path": full} if os.path.isfile(full) else None
