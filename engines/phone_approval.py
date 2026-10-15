"""Sign in on a computer with your phone (owner, 2026-10-14: "it ensures they are the one logging in" ... "Kind 2", the site's own QR, no Bluetooth).
The computer's sign-in page shows a QR code and a two-digit number. The person scans it with a phone that is signed in to the system (by its
fingerprint), picks the number the computer shows, and approves; the computer then signs in as that person.

What protects it: the code is 32 random characters, works once, for two minutes; only the browser that asked can collect the sign-in; the number
ties the approval to the person looking at that computer (a wrong pick cancels it); the phone's account must be the one Cloudflare verified on the
computer; only a person who has fingerprint sign-in turned on somewhere may approve, never the Scanner account. Mock-up: docs/mockups/w_phone_approves.html.

PUSH (owner, 2026-10-15: "drop scan qr but push notification", "comes after it", "1 phone only"; mock-up docs/mockups/z_push_approval.html): on a
computer, after the right password, a notification goes to the person's phone (the last one set up for notifications). Tapping it opens the approve
page, which Cloudflare lets through (a phone whose Cloudflare session ran out can still approve): the person picks the number the computer shows and
proves who they are with the fingerprint kept on that phone. Nobody is locked out: a person with no phone set up (notifications AND fingerprint) signs in
with the password alone. The switch signin_push_approval (Settings -> Sign-in) starts off."""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from engines.clock import manila_now
from engines.common import get_conn, write_tx

STAMP = "%Y-%m-%d %H:%M:%S"
LIFE_SECONDS = 120
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
GONE = "This code is no longer good. Start again on the computer."


def required_for(user: dict | None) -> bool:
    """Owner, 2026-10-15 ("i want to require we have a push authentication", "right away"): with the switch on, every computer sign-in needs the
    phone's approval; a person whose phone is not set up cannot sign in on a computer until it is (the phone itself still signs in, to set it up).
    The Scanner account never approves anything and is not asked."""
    return bool(user) and push_on() and user.get("role") != "scanner"


def push_on() -> bool:
    from engines.settings_registry import int_value
    return int_value("signin_push_approval") == 1


def _is_phone(device) -> bool:
    return any(k in str(device or "") for k in ("Android", "iPhone", "iPad", "Mobile"))


def push_phone(user_id: int | None) -> dict | None:
    """The phone a computer sign-in is approved on: the last one this person set up for notifications, when they also have fingerprint sign-in
    turned on ON A PHONE (the approve page asks for it) and are not the Scanner. Only a phone counts, never a computer that allowed notifications. None = not set up: the password alone signs them in."""
    if not user_id:
        return None
    with get_conn() as conn:
        row = conn.execute("SELECT email, role, is_active FROM app_users WHERE user_id = ?", (int(user_id),)).fetchone()
        if not row or not row[2] or row[1] == "scanner":
            return None
        keys = [d for (d,) in conn.execute("SELECT device FROM sign_in_keys WHERE user_id = ?", (int(user_id),)).fetchall()]
        subs = conn.execute("""SELECT sub_id, device, created_at FROM push_subscriptions WHERE user_email = ? ORDER BY created_at DESC, sub_id DESC""",
                            (str(row[0]).strip().lower(),)).fetchall()
    phones = [x for x in subs if _is_phone(x[1])]                 # a laptop that allowed notifications is not the phone (live, 2026-10-15)
    if not any(_is_phone(d) for d in keys) or not phones:
        return None
    sub = phones[0]
    backup = phones[1] if row[1] == "admin" and len(phones) > 1 else None     # owner, 2026-10-15: the System admin keeps a second phone as a backup
    return {"sub_id": int(sub[0]), "device": _short(sub[1]), "since": str(sub[2] or "")[:10], "email": str(row[0]).strip().lower(),
            "subs": [int(sub[0])] + ([int(backup[0])] if backup else []), "backup": _short(backup[1]) if backup else ""}


def _short(device) -> str:
    """"Chrome on Android" from a full browser line (what the screens show)."""
    ua = str(device or "")
    if "Mozilla/" not in ua:
        return ua or "Phone"
    system = next((n for k, n in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android")) if k in ua), "phone")
    name = next((n for k, n in (("SamsungBrowser", "Samsung Internet"), ("CriOS", "Chrome"), ("Chrome/", "Chrome"), ("Firefox/", "Firefox"), ("Safari/", "Safari")) if k in ua), "")
    return " on ".join(x for x in (name, system) if x)


def send_push(request_id: int) -> bool:
    """The notification for a waiting request, to that one phone. True when the phone's push service took it."""
    from engines.web_push import send
    with get_conn() as conn:
        row = _row(conn, "request_id", int(request_id))
    if not _live(row, manila_now()) or not row["user_id"]:
        return False
    phone = push_phone(int(row["user_id"]))
    if phone is None:
        return False
    when = manila_now().strftime("%I:%M %p").lstrip("0")
    return sum(send(phone["email"], "Sign-in request", f"A computer is asking to sign in as you, {when}. Tap to approve or refuse.",
                    url=f"/approve/{row['token']}/", tag="signin", only_sub=s) for s in phone["subs"]) > 0


def request_user(token: str) -> int | None:
    """Whose sign-in a live request is (the approve page asks for that person's fingerprint)."""
    with get_conn() as conn:
        row = _row(conn, "token", str(token or ""))
    return int(row["user_id"]) if _live(row, manila_now()) and row["user_id"] else None


def clear_phone(user_id: int, by: str = "") -> tuple[bool, str]:
    """The System admin clears a person's phone (lost, or no longer getting notifications): its notifications and every fingerprint sign-in go, so
    the person signs in with the password and sets up the new phone."""
    with write_tx() as conn:
        row = conn.execute("SELECT email FROM app_users WHERE user_id = ?", (int(user_id),)).fetchone()
        if row is None:
            return False, "No such person."
        subs = conn.execute("DELETE FROM push_subscriptions WHERE user_email = ?", (str(row[0]).strip().lower(),)).rowcount
        from engines.fingerprint_signin import remove_all
        keys = remove_all(conn, int(user_id))
        from engines.fingerprint_signin import _audit
        _audit(conn, "PHONE_CLEARED", int(user_id), f"notifications on {subs} phone(s) and fingerprint on {keys} device(s) removed", by or None)
    return True, "Phone cleared. They sign in with the password until a new phone is set up."


def may_use(user_id: int | None) -> bool:
    """May this account be signed in to a computer by its phone: the switch is on, it is not the Scanner, and fingerprint sign-in is on somewhere."""
    if not push_on() or not user_id:
        return False
    with get_conn() as conn:
        row = conn.execute("SELECT role, is_active FROM app_users WHERE user_id = ?", (int(user_id),)).fetchone()
        keys = conn.execute("SELECT COUNT(*) FROM sign_in_keys WHERE user_id = ?", (int(user_id),)).fetchone()[0]
    return bool(row and row[1] and row[0] != "scanner" and keys)


def create(user_id: int | None, browser: str = "", ip: str = "", nxt: str = "/") -> dict:
    """The computer asks: a new code and number. user_id = the account Cloudflare verified on that computer, when it did."""
    now = manila_now()
    token = "".join(secrets.choice(_ALPHABET) for _ in range(32))
    number = 10 + secrets.randbelow(90)
    with write_tx() as conn:
        conn.execute("UPDATE signin_requests SET status = 'EXPIRED' WHERE status = 'PENDING' AND expires_at < ?", (now.strftime(STAMP),))
        rid = conn.execute("""INSERT INTO signin_requests (token, user_id, number, browser, ip, next_url, created_at, expires_at)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING request_id""",
                           (token, int(user_id) if user_id else None, number, (browser or "")[:80] or None, (ip or "")[:45] or None, (nxt or "/")[:300],
                            now.strftime(STAMP), (now + timedelta(seconds=LIFE_SECONDS)).strftime(STAMP))).fetchone()[0]
    return {"request_id": int(rid), "token": token, "number": number, "seconds": LIFE_SECONDS}


def _row(conn, where: str, value):
    conn.row_factory = __import__("sqlite3").Row
    return conn.execute(f"SELECT * FROM signin_requests WHERE {where} = ?", (value,)).fetchone()


def _live(row, now) -> bool:
    return row is not None and row["status"] == "PENDING" and now.strftime(STAMP) <= str(row["expires_at"])


def choices(token: str, number: int) -> list[int]:
    """The three numbers the phone offers: the computer's and two others, in an order fixed by the code (the same on a reload)."""
    seed = int(hashlib.sha256(token.encode()).hexdigest(), 16)
    out = [int(number)]
    while len(out) < 3:
        seed, pick = seed // 90, 10 + seed % 90
        if all(abs(pick - x) > 3 for x in out):
            out.append(pick)
        seed = seed or int(hashlib.sha256(str(out).encode()).hexdigest(), 16)
    return sorted(out, key=lambda x: hashlib.sha256(f"{token}{x}".encode()).hexdigest())


def for_phone(token: str, approver: dict) -> tuple[dict | None, str]:
    """What the phone shows after the scan: (the request, '') or (None, why not)."""
    if not push_on():
        return None, "Signing in with the phone is switched off."
    with get_conn() as conn:
        row = _row(conn, "token", str(token or ""))
        if not _live(row, manila_now()):
            return None, GONE
        row = dict(row)
    if not may_use(int(approver["user_id"])):
        return None, "Turn on fingerprint sign-in on this phone first (the person menu → Fingerprint sign-in), then scan again."
    if row["user_id"] and int(row["user_id"]) != int(approver["user_id"]):
        return None, "That computer is asking for another person's account. Each person approves with their own phone."
    return {"browser": row["browser"] or "A computer", "choices": choices(token, int(row["number"])), "email": approver["email"]}, ""


def answer(token: str, approver: dict, picked, approve: bool) -> tuple[bool, str]:
    """The phone's answer. A wrong number, or "It is not me", cancels the code; the second also goes on the Review list."""
    info, why = for_phone(token, approver)
    if info is None:
        return False, why
    now = manila_now().strftime(STAMP)
    with write_tx() as conn:
        row = _row(conn, "token", token)
        if not approve:
            conn.execute("UPDATE signin_requests SET status = 'DENIED', approved_by = ?, approved_at = ? WHERE request_id = ? AND status = 'PENDING'",
                         (int(approver["user_id"]), now, int(row["request_id"])))
            conn.execute("INSERT INTO review_flags (kind, ref_type, ref_id, detail, raised_by) VALUES ('PHONE_SIGN_IN_REFUSED', 'app_users', ?, ?, ?)",
                         (int(approver["user_id"]), f"{approver['email']} said 'It is not me' to a sign-in asked on {row['browser'] or 'a computer'}"
                          + (f" at {row['ip']}" if row["ip"] else "") + f", {manila_now():%m/%d/%Y %I:%M %p}.", approver["email"]))
            return True, "Refused. Nobody was signed in, and it is noted on Checks and review."
        if str(picked or "").strip() != str(row["number"]):
            conn.execute("UPDATE signin_requests SET status = 'DENIED', approved_by = ?, approved_at = ? WHERE request_id = ? AND status = 'PENDING'",
                         (int(approver["user_id"]), now, int(row["request_id"])))
            return False, "That is not the number on the computer. The code is cancelled: start again on the computer."
        done = conn.execute("UPDATE signin_requests SET status = 'APPROVED', approved_by = ?, approved_at = ? WHERE request_id = ? AND status = 'PENDING'",
                            (int(approver["user_id"]), now, int(row["request_id"]))).rowcount
    return (True, "Approved. The computer is signed in.") if done else (False, GONE)


def state(request_id: int) -> dict:
    """For the waiting computer: {'state': pending / approved / denied / gone, 'left': seconds}."""
    now = manila_now()
    with get_conn() as conn:
        row = _row(conn, "request_id", int(request_id or 0))
    if row is None:
        return {"state": "gone", "left": 0}
    if row["status"] == "APPROVED":
        return {"state": "approved", "left": 0}
    if row["status"] == "DENIED":
        return {"state": "denied", "left": 0}
    if not _live(row, now):
        return {"state": "gone", "left": 0}
    from datetime import datetime
    left = (datetime.strptime(str(row["expires_at"]), STAMP) - now.replace(tzinfo=None)).total_seconds()
    return {"state": "pending", "left": max(0, int(left)), "number": int(row["number"]), "token": row["token"]}


def collect(request_id: int) -> tuple[int | None, str]:
    """The computer that asked takes its sign-in, once: (the approving person's id, where to go), or (None, '')."""
    with write_tx() as conn:
        row = _row(conn, "request_id", int(request_id or 0))
        if row is None or row["status"] != "APPROVED" or not row["approved_by"]:
            return None, ""
        if conn.execute("UPDATE signin_requests SET status = 'USED' WHERE request_id = ? AND status = 'APPROVED'", (int(request_id),)).rowcount != 1:
            return None, ""
    return int(row["approved_by"]), str(row["next_url"] or "/")
