"""System health (owner, 2026-10-20: "for a system admin, what features should I have to monitor the entire system?"; mock-up system_health.html, "build").
One read-only look at the whole system for the System admin: server, backups, data safety, messages, people today, speed and what waits on people.
Each card is green / yellow / red with the reason in plain words and where it is fixed. Nothing here changes anything.
Page timings are recorded by core.middleware.PageTimeMiddleware (time only, never what was on the page) in data/page_times/ (14 days kept)."""
from __future__ import annotations

import os
import re
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

GREEN, YELLOW, RED = "green", "yellow", "red"
_SUMMARY: dict = {}                                   # Home's one line, kept 10 minutes
SUMMARY_SECONDS = 600


def _db_path() -> Path:
    from engines import common
    return Path(common.DB_PATH).resolve()


def _now() -> datetime:
    from engines.clock import manila_now
    return manila_now().replace(tzinfo=None)


def _ago(then: datetime | None) -> str:
    if then is None:
        return "never"
    s = (_now() - then).total_seconds()
    if s < 3600:
        return f"{max(int(s // 60), 0)} min ago"
    if s < 86400 * 2:
        return f"{s / 3600:.0f} hours ago"
    return f"{s / 86400:.0f} days ago"


def _mtime(p: Path) -> datetime | None:
    """A file's time, in Manila time (the server's clock is UTC)."""
    try:
        from engines.clock import MANILA
        return datetime.fromtimestamp(p.stat().st_mtime, MANILA).replace(tzinfo=None)
    except (OSError, ImportError, AttributeError):
        try:
            return datetime.utcfromtimestamp(p.stat().st_mtime) + timedelta(hours=8)
        except OSError:
            return None


def _card(key: str, title: str, state: str, rows: list, note: str = "", link: tuple | None = None) -> dict:
    return {"key": key, "title": title, "state": state, "rows": rows, "note": note, "link": link}


def _worst(*states: str) -> str:
    return RED if RED in states else (YELLOW if YELLOW in states else GREEN)


# ---- server ---------------------------------------------------------------------------------------------------------------------------------------

def release_id() -> str:
    """The release this code runs from (/srv/slicks/app/releases/<date_time_code>), or '' on the laptop."""
    here = Path(__file__).resolve()
    for p in here.parents:
        if p.parent.name == "releases":
            return p.name
    return ""


def _release_time(rid: str) -> datetime | None:
    try:
        return datetime.strptime(rid[:15], "%Y%m%d_%H%M%S")      # the deploy names releases by the laptop's (Manila) clock
    except ValueError:
        return None


def recent_releases(n: int = 5) -> list[dict]:
    rid = release_id()
    if not rid:
        return []
    folder = Path(__file__).resolve()
    while folder.parent.name != "releases":
        folder = folder.parent
    names = sorted((p.name for p in folder.parent.iterdir() if p.is_dir() and re.match(r"\d{8}_\d{6}_", p.name)), reverse=True)[:n]
    return [{"id": x, "code": x.split("_")[-1], "at": _release_time(x), "live": x == rid} for x in names]


def server_errors_24h() -> int | None:
    """5xx answers and errors in the server log in the last 24 hours; None where the log cannot be read (the laptop)."""
    import subprocess
    try:
        out = subprocess.run(["journalctl", "-u", "slicks-erp", "--since", "24 hours ago", "--no-pager", "-q"], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return sum(1 for line in out.stdout.splitlines() if re.search(r'" 5\d\d |Traceback|ERROR', line))


def _server() -> dict:
    rid = release_id()
    rows, states = [], []
    if rid:
        rows.append(("Version live", f"{rid.split('_')[-1]} · since {_release_time(rid):%m/%d %I:%M %p}" if _release_time(rid) else rid, ""))
    else:
        rows.append(("Version live", "the laptop's copy (sandbox)", ""))
    errs = server_errors_24h()
    if errs is None:
        rows.append(("Errors, last 24 hours", "not readable here", ""))
    else:
        st = GREEN if errs == 0 else YELLOW
        states.append(st)
        rows.append(("Errors, last 24 hours", str(errs), "" if errs == 0 else "warn"))
    du = shutil.disk_usage(_db_path().parent)
    free = du.free / du.total
    st = RED if free < 0.10 else (YELLOW if free < 0.20 else GREEN)
    states.append(st)
    rows.append(("Disk space left", f"{du.free / 1e9:.0f} GB of {du.total / 1e9:.0f} GB", "" if st == GREEN else ("bad" if st == RED else "warn")))
    note = "" if _worst(*states) == GREEN else ("Errors: tell Claude \"the server log has errors\"." if errs else "Disk nearly full: tell Claude.")
    return _card("server", "Server", _worst(*states), rows, note)


# ---- backups --------------------------------------------------------------------------------------------------------------------------------------

def _backup_dir() -> Path:
    return Path(os.environ.get("AP_BACKUP_DIR") or (_db_path().parent.parent / "backups"))


LAPTOP_MARK = ".laptop_pulled"          # touched by the laptop's 7:30 PM copy (deploy/pull_backup.ps1) after a good pull


def _backups() -> dict:
    d = _backup_dir()
    newest = None
    try:
        files = sorted(d.glob("slicks_erp_backup_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
        newest = _mtime(files[0]) if files else None
    except OSError:
        files = []
    age_h = (_now() - newest).total_seconds() / 3600 if newest else None
    s1 = RED if age_h is None or age_h > 50 else (YELLOW if age_h > 26 else GREEN)
    rows = [("Nightly backup on the server", f"{newest:%m/%d %I:%M %p} ({_ago(newest)})" if newest else "none found", "" if s1 == GREEN else "bad" if s1 == RED else "warn")]
    mark = _mtime(d / LAPTOP_MARK)
    if mark is None:
        s2 = YELLOW
        rows.append(("Copy on your laptop", "not reported yet (from the next 7:30 PM copy)", "warn"))
    else:
        h = (_now() - mark).total_seconds() / 3600
        s2 = RED if h > 50 else (YELLOW if h > 26 else GREEN)
        rows.append(("Copy on your laptop", f"{mark:%m/%d %I:%M %p} ({_ago(mark)})", "" if s2 == GREEN else "bad" if s2 == RED else "warn"))
    state = _worst(s1, s2)
    note = ""
    if s1 != GREEN:
        note = "The server's nightly backup is late: tell Claude."
    elif s2 == RED:
        note = "Your laptop has not copied the backup: turn it on at 7:30 PM, or run the \"Slicks ERP backup pull\" task now."
    return _card("backups", "Backups", state, rows, note)


# ---- data safety ----------------------------------------------------------------------------------------------------------------------------------

def _data() -> dict:
    from engines.audit_seal import last_result
    from engines.common import get_conn
    from engines.settings_registry import int_value
    with get_conn() as conn:
        ok = conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        seal = last_result(conn) or {}
        bank = conn.execute("SELECT MAX(period_to), MAX(imported_at) FROM bank_statement_imports").fetchone()
    s1 = GREEN if ok else RED
    rows = [("Database check", "ok" if ok else "PROBLEM", "" if ok else "bad")]
    seal_at = None
    try:
        seal_at = datetime.strptime(str(seal.get("at", ""))[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass
    if not seal:
        s2 = YELLOW
        rows.append(("Audit seal", "not checked yet", "warn"))
    elif not seal.get("ok"):
        s2 = RED
        rows.append(("Audit seal", "BROKEN: " + "; ".join(seal.get("problems") or [])[:120], "bad"))
    else:
        s2 = YELLOW if seal_at and (_now() - seal_at).days >= 2 else GREEN
        rows.append(("Audit seal (nobody edited records behind the system)", f"intact · {int(seal.get('sealed_total') or 0):,} entries · {_ago(seal_at)}", "" if s2 == GREEN else "warn"))
    days = None
    if bank and bank[0]:
        try:
            days = (_now().date() - datetime.strptime(str(bank[0])[:10], "%Y-%m-%d").date()).days
        except ValueError:
            days = None
    limit = max(int_value("stale_deposit_days"), 1)
    s3 = YELLOW if days is None or days > limit else GREEN
    rows.append(("Bank statement up to", f"{str(bank[0])[:10]} ({days} days)" if days is not None else "none imported", "" if s3 == GREEN else "warn"))
    note = ""
    if s1 == RED or s2 == RED:
        note = "Tell Claude at once: the data check found a problem."
    elif s3 != GREEN:
        note = "Import the latest bank statement on Bank and cash."
    return _card("data", "Data safety", _worst(s1, s2, s3), rows, note, ("Bank and cash", "/bank-cash/?tab=import") if s3 != GREEN else None)


# ---- messages -------------------------------------------------------------------------------------------------------------------------------------

def _messages() -> dict:
    from engines.common import get_conn
    today = _now().date().isoformat()
    week = (_now().date() - timedelta(days=7)).isoformat()
    with get_conn() as conn:
        sent, failed = conn.execute("SELECT COALESCE(SUM(ok), 0), COALESCE(SUM(1 - ok), 0) FROM morning_emails_sent WHERE day = ?", (today,)).fetchone()
        why = [f"{r[0]}: {r[1]}" for r in conn.execute("SELECT user_email, COALESCE(note, '') FROM morning_emails_sent WHERE day = ? AND ok = 0", (today,))]
        missed = [r[0] for r in conn.execute("SELECT DISTINCT user_email FROM yesterday_push_sent WHERE day >= ? AND phones = 0", (week,))]
        people = conn.execute("SELECT name, email FROM app_users WHERE is_active = 1 AND role != 'scanner'").fetchall()
        phones = {r[0].strip().lower() for r in conn.execute("SELECT DISTINCT user_email FROM push_subscriptions")}
    no_phone = [n or e for n, e in people if e.strip().lower() not in phones]
    s1 = YELLOW if failed else GREEN
    s2 = YELLOW if missed else GREEN
    s3 = YELLOW if no_phone else GREEN
    rows = [("Morning emails today", f"{int(sent)} sent · {int(failed)} failed", "" if not failed else "warn")]
    if why:
        rows.append(("Email failed", "; ".join(why)[:160], "warn"))
    rows.append(("Phone notifications that reached no phone, 7 days", ", ".join(missed) if missed else "none", "" if not missed else "warn"))
    rows.append(("No phone set up (cannot get notifications or approve a computer sign-in)", ", ".join(no_phone) if no_phone else "none", "" if not no_phone else "warn"))
    note = "On that person's phone: open the Slick's app and allow notifications." if missed or no_phone else ""
    return _card("messages", "Messages", _worst(s1, s2, s3), rows, note, ("Settings → Notifications", "/settings/notifications/") if failed else None)


# ---- people today ---------------------------------------------------------------------------------------------------------------------------------

def _people() -> dict:
    from engines.auth_service import SIGNED_IN
    from engines.common import get_conn
    today = _now().date().isoformat()
    with get_conn() as conn:
        users = conn.execute("SELECT name, email, role FROM app_users WHERE is_active = 1 ORDER BY role != 'admin', name").fetchall()
        signs = conn.execute("SELECT lower(email), result, is_new_device, browser, at FROM sign_in_log WHERE date(at) = ? ORDER BY at", (today,)).fetchall()
        saves = dict(conn.execute("""SELECT lower(user_email), COUNT(*) FROM audit_logs WHERE date(timestamp, '+8 hours') = ? GROUP BY lower(user_email)""",
                                  (today,)).fetchall())
    first, device = {}, {}
    refused = new_dev = 0
    for email, result, is_new, browser, at in signs:
        if result in SIGNED_IN:
            first.setdefault(email, at)
            device[email] = "phone" if re.search(r"android|iphone|mobile", browser or "", re.I) else "computer"
            new_dev += 1 if is_new else 0
        else:
            refused += 1
    table = []
    for name, email, role in users:
        e = email.strip().lower()
        at = first.get(e)
        try:
            when = datetime.strptime(str(at)[:19], "%Y-%m-%d %H:%M:%S").strftime("%I:%M %p").lstrip("0") if at else "—"
        except ValueError:
            when = str(at)[11:16]
        table.append({"who": name or email, "when": when, "device": device.get(e, ""), "saves": int(saves.get(e, 0))})
    state = YELLOW if refused >= 3 or new_dev else GREEN
    rows = [("New devices / refused sign-ins today", f"{new_dev} / {refused}", "" if state == GREEN else "warn")]
    note = "A new device or several refused sign-ins: look at Review → Sign-ins." if state != GREEN else ""
    c = _card("people", "People today", state, rows, note, ("Review → Sign-ins", "/review/?tab=signins") if state != GREEN else None)
    c["table"] = table
    return c


# ---- speed ----------------------------------------------------------------------------------------------------------------------------------------

def _times_dir() -> Path:
    return _db_path().parent / "page_times"


def record_page_time(path: str, seconds: float) -> None:
    """One line per page opened: the time of day, the page (numbers replaced by #) and how long it took. Never what was on it."""
    now = _now()
    d = _times_dir()
    try:
        d.mkdir(exist_ok=True)
        f = d / f"{now:%Y%m%d}.log"
        new_day = not f.exists()
        page = re.sub(r"/\d+(?=/|$)", "/#", path)[:120]
        with open(f, "a", encoding="utf-8") as out:
            out.write(f"{now:%H:%M:%S}\t{page}\t{seconds:.3f}\n")
        if new_day:                                   # once a day: files older than 14 days go
            cut = f"{now - timedelta(days=14):%Y%m%d}"
            for old in d.glob("*.log"):
                if old.stem < cut:
                    old.unlink(missing_ok=True)
    except OSError:
        pass


def _read_times(day) -> list[tuple[str, float]]:
    out = []
    try:
        for line in (_times_dir() / f"{day:%Y%m%d}.log").read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                out.append((parts[1], float(parts[2])))
    except (OSError, ValueError):
        pass
    return out


def _speed() -> dict:
    today = _now().date()
    rows_today = _read_times(today)
    if not rows_today:
        return _card("speed", "Speed", GREEN, [("Pages opened today", "0 so far", "")])
    usual: dict = {}
    for k in range(1, 8):
        for p, s in _read_times(today - timedelta(days=k)):
            usual.setdefault(p, []).append(s)
    med = {p: sorted(v)[len(v) // 2] for p, v in usual.items()}
    worst_p, worst_s = max(rows_today, key=lambda r: r[1])
    over = sum(1 for _, s in rows_today if s > 1.0)
    base = med.get(worst_p)
    slow_vs_usual = base is not None and worst_s > 2.0 and worst_s > base * 3
    state = RED if slow_vs_usual else (YELLOW if worst_s > 3.0 else GREEN)
    rows = [("Slowest page today", f"{worst_p} · {worst_s:.1f} s" + (f" (usually {base:.1f} s)" if base is not None else ""), "" if state == GREEN else "bad" if state == RED else "warn"),
            ("Pages over 1 second today", f"{over} of {len(rows_today)}", "")]
    note = f"Tell Claude: \"{worst_p} is slow\"." if state != GREEN else ""
    return _card("speed", "Speed", state, rows, note)


# ---- waiting on people ----------------------------------------------------------------------------------------------------------------------------

def _waiting() -> dict:
    from engines import review_flags as rf
    rows, state = [], GREEN
    n = rf.open_count()
    rows.append(("Review list, open", str(n), ""))
    try:
        from engines.entry_changes import unseen_count
        rows.append(("Changed today, not seen", str(unseen_count()), ""))
    except Exception:                                   # noqa: BLE001
        pass
    try:
        from engines.card_reminders import missing_count
        m = missing_count()
        rows.append(("Card statements to type", str(m), "" if not m else "warn"))
        state = YELLOW if m else GREEN
    except Exception:                                   # noqa: BLE001
        pass
    return _card("waiting", "Waiting on people", state, rows, "", ("Checks and review", "/review/"))


# ---- all ------------------------------------------------------------------------------------------------------------------------------------------

CHECKS = (("server", "Server", _server), ("backups", "Backups", _backups), ("data", "Data safety", _data), ("messages", "Messages", _messages),
          ("people", "People today", _people), ("speed", "Speed", _speed), ("waiting", "Waiting on people", _waiting))


def cards() -> list[dict]:
    out = []
    for key, title, fn in CHECKS:
        try:
            out.append(fn())
        except Exception as e:                          # noqa: BLE001 -- one check must never take the page down
            out.append(_card(key, title, YELLOW, [("Could not check", type(e).__name__, "warn")], "Tell Claude this check could not run."))
    return out


def summary(cs: list[dict] | None = None) -> dict:
    cs = cards() if cs is None else cs
    red = [c["title"] for c in cs if c["state"] == RED]
    yellow = [c["title"] for c in cs if c["state"] == YELLOW]
    return {"red": red, "yellow": yellow, "at": _now()}


def summary_cached() -> dict:
    """Home's line for the System admin: worked out at most every 10 minutes."""
    from engines import common
    key = common.DB_PATH
    hit = _SUMMARY.get(key)
    if hit and time.monotonic() - hit[0] < SUMMARY_SECONDS:
        return hit[1]
    s = summary()
    _SUMMARY[key] = (time.monotonic(), s)
    return s


# ---- the Monday email -----------------------------------------------------------------------------------------------------------------------------

SENT_KEY = "system_health_email_sent"


def weekly_email(site: str = "", force: bool = False) -> str:
    """Mondays from 8 AM Manila, once: to the System admin(s), only when something is red or yellow. Run by the hourly timer."""
    from engines import mail_out
    from engines.common import get_conn
    from engines.settings_store import get_setting, set_setting
    now = _now()
    if not force and (now.weekday() != 0 or now.hour < 8 or get_setting(SENT_KEY, "") == now.date().isoformat()):
        return "System health email: not due."
    cs = cards()
    bad = [c for c in cs if c["state"] != GREEN]
    set_setting(SENT_KEY, now.date().isoformat())
    if not bad:
        return "System health email: all green, nothing sent."
    if not mail_out.ready():
        return "System health email: mail is not set up."
    with get_conn() as conn:
        admins = [r[0] for r in conn.execute("SELECT email FROM app_users WHERE is_active = 1 AND role = 'admin' AND email LIKE '%@%.%'")]
    reds = sum(1 for c in bad if c["state"] == RED)
    subject = f"{now:%a %b %d} · System health: " + (f"{reds} thing{'s' if reds != 1 else ''} need{'' if reds != 1 else 's'} you" if reds else "a few things to look at")
    from html import escape
    items = "".join(f'<p><b style="color:{"#b42318" if c["state"] == RED else "#b35900"}">● {escape(c["title"])}:</b> '
                    f'{escape(c["note"] or "; ".join(f"{a}: {b}" for a, b, cls in c["rows"] if cls))}</p>' for c in bad)
    ok = ", ".join(c["title"].lower() for c in cs if c["state"] == GREEN)
    html = (f'<div style="font-family:Segoe UI,Arial,sans-serif;max-width:640px;margin:0 auto;border:1px solid #d5dbe3;border-radius:10px;overflow:hidden">'
            f'<div style="background:#13294b;color:#fff;padding:12px 16px;font-weight:700">{escape(subject)}</div><div style="padding:12px 16px;font-size:14px">'
            f'{items}{f"<p>Everything else is OK ({escape(ok)}).</p>" if ok else ""}'
            f'{f"<p><a href={chr(34)}{escape(site)}/system-health/{chr(34)}>Open System health</a></p>" if site else ""}'
            f'<p style="color:#6b7280;font-size:12px">This is an automatic email. Replies are not read.</p></div></div>')
    text = subject + "\n\n" + "\n".join(f"- {c['title']}: {c['note'] or 'look'}" for c in bad)
    sent = 0
    for a in admins:
        good, _msg = mail_out.send(a, subject, html, text)
        sent += 1 if good else 0
    return f"System health email: sent to {sent} of {len(admins)}."
