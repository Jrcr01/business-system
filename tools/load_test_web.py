"""Load test for the Django site (stage D0 gate, docs/DJANGO_REBUILD_PROPOSAL.md, decision 1: "keep the current database type first, upgrade if the load test says so").

N simulated people work at the same moment, each in their own browser session against the running site, and each saving real records through the business rules
into the SAME database file the site reads:
  * every person signs in, opens Home, a page from the menu and /whoami/ (who the site thinks is acting) in a loop;
  * every round, every person also saves a loan note with its whole schedule (a new LN number, 4 payments, audit rows), the kind of save that needs the write lock.
At the end it checks: no page failed, nobody was ever taken for someone else, every save is there exactly once, no LN number was given twice, every schedule is whole,
and every audit row names the person who made it. Pass = all of that, with page times reported.

Only for a SANDBOX database (its file name must contain TEST). Usage, with the site running (run_web_sandbox.bat):
    .venv\\Scripts\\python tools\\load_test_web.py --db data\\ap_disbursement_WEB_TEST.db --users 10 --rounds 20
The simulated people are loadtestNN@slicks.test; their password is made fresh each run and never printed."""
from __future__ import annotations

import argparse
import os
import random
import re
import secrets
import statistics
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8505", help="the site; several, comma-separated, = several server processes (people are shared out among them)")
    ap.add_argument("--db", required=True)
    ap.add_argument("--users", type=int, default=10)
    ap.add_argument("--rounds", type=int, default=20)
    a = ap.parse_args()
    if "TEST" not in Path(a.db).name.upper():
        print("Refused: the load test runs only on a sandbox database (its file name must contain TEST).")
        return 2
    os.environ["AP_DB_PATH"] = str(Path(a.db).resolve())

    import requests
    from engines.acting_user import acting_as
    from engines.auth_service import change_password, create_user
    from engines.common import add_months, get_conn
    from engines.loan_schedule_service import create_note_with_schedule
    from engines.db_engine import open_db
    keeper = open_db(os.environ["AP_DB_PATH"])     # like the site (web/core/apps.py): one connection held open while this process saves  # noqa: F841

    # ---- the people and a lender -----------------------------------------------------------------------------------------------------
    pw = "Lt" + secrets.token_urlsafe(12) + "9"
    people = []
    with get_conn() as conn:
        existing = {r[0]: r[1] for r in conn.execute("SELECT email, user_id FROM app_users WHERE email LIKE 'loadtest%@slicks.test'")}
    for i in range(1, a.users + 1):
        email = f"loadtest{i:02d}@slicks.test"
        if email not in existing:
            ok, msg, uid, temp = create_user(email, f"Load test {i:02d}", "owner")
            assert ok, msg
            ok, msg = change_password(uid, temp, pw)
            assert ok, msg
        else:
            uid = existing[email]
            with get_conn() as conn:                     # a fresh password each run (sandbox only), so it never has to be stored
                from engines.auth_service import hash_password
                conn.execute("UPDATE app_users SET password_hash = ?, must_change_password = 0, is_active = 1, failed_attempts = 0, locked_until = NULL WHERE user_id = ?",
                             (hash_password(pw), uid))
                conn.commit()
        people.append(email)
    with get_conn() as conn:
        row = conn.execute("SELECT creditor_id FROM creditors WHERE creditor_name = 'Load Test Lender'").fetchone()
        if row:
            lender = row[0]
        else:
            lender = conn.execute("INSERT INTO creditors (creditor_name, creditor_type) VALUES ('Load Test Lender', 'LENDER')").lastrowid
            conn.commit()
        notes_before = conn.execute("SELECT COUNT(*) FROM loan_notes WHERE creditor_id = ?", (lender,)).fetchone()[0]

    pages = ["/", "/lenders/", "/purchase-orders/", "/payment-run/", "/release/", "/reports/"]
    times, slow, errors, mixups, saves = [], [], [], [], {p: [] for p in people}
    save_times = []
    lock = threading.Lock()
    start_line = threading.Barrier(len(people))

    urls = [u.strip().rstrip("/") for u in a.url.split(",") if u.strip()]
    home_of = {email: urls[i % len(urls)] for i, email in enumerate(people)}     # the sign-in is a signed cookie, so any server process can serve anyone

    def get(s, url):
        t = time.perf_counter()
        r = s.get(s.base + url, timeout=60)
        took = time.perf_counter() - t
        m = re.search(r"app;dur=(\d+)", r.headers.get("Server-Timing", ""))
        with lock:
            times.append(took)
            slow.append((took, int(m.group(1)) if m else -1, url))
        return r

    def person(email: str):
        s = requests.Session()
        s.base = home_of[email]
        try:
            r = s.get(s.base + "/login/", timeout=60)
            token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', r.text).group(1)
            r = s.post(s.base + "/login/", data={"email": email, "password": pw, "csrfmiddlewaretoken": token, "next": "/"}, timeout=60)
            if r.status_code != 200 or "Welcome" not in r.text:
                with lock:
                    errors.append(f"{email}: sign-in failed ({r.status_code})")
                return
            start_line.wait()                                                  # everyone starts at the same moment
            for n in range(a.rounds):
                for url in ("/", random.choice(pages)):
                    r = get(s, url)
                    if r.status_code != 200:
                        with lock:
                            errors.append(f"{email}: {url} gave {r.status_code}")
                who = get(s, "/whoami/").json()
                if who.get("email") != email:
                    with lock:
                        mixups.append(f"{email} was taken for {who.get('email')}")
                issue = date(2026, 1, 1) + timedelta(days=n)
                t = time.perf_counter()
                with acting_as(email, "owner"):
                    ok, msg, lnid = create_note_with_schedule(lender, issue, 100000 + n, 3, add_months(issue, 3), 4)     # 2 interest-only + 4 = 6 rows
                with lock:
                    save_times.append(time.perf_counter() - t)
                if ok:
                    saves[email].append(lnid)
                else:
                    with lock:
                        errors.append(f"{email}: save {n + 1} refused: {msg}")
        except Exception as e:                                                  # noqa: BLE001 -- every failure is reported, none stops the others
            with lock:
                errors.append(f"{email}: {type(e).__name__}: {e}")

    t0 = time.perf_counter()
    threads = [threading.Thread(target=person, args=(p,)) for p in people]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - t0

    # ---- the checks ------------------------------------------------------------------------------------------------------------------------
    made = [i for ids in saves.values() for i in ids]
    problems = list(errors) + list(mixups)
    with get_conn() as conn:
        notes_after = conn.execute("SELECT COUNT(*) FROM loan_notes WHERE creditor_id = ?", (lender,)).fetchone()[0]
        if made:
            q = ",".join("?" * len(made))
            refs = [r[0] for r in conn.execute(f"SELECT note_ref FROM loan_notes WHERE loan_note_id IN ({q})", made)]
            short = conn.execute(f"SELECT loan_note_id, COUNT(i.installment_id) FROM loan_notes n LEFT JOIN loan_installments i USING (loan_note_id) "
                                 f"WHERE n.loan_note_id IN ({q}) GROUP BY loan_note_id HAVING COUNT(i.installment_id) <> 6", made).fetchall()
            wrong_who = conn.execute(f"SELECT COUNT(*) FROM audit_logs WHERE entity_name = 'loan_notes' AND entity_id IN ({q}) "
                                     f"AND (user_email IS NULL OR user_email NOT LIKE 'loadtest%@slicks.test')", made).fetchone()[0]
            per_person_ok = all(
                conn.execute(f"SELECT COUNT(DISTINCT entity_id) FROM audit_logs WHERE entity_name = 'loan_notes' AND user_email = ? AND entity_id IN ({','.join('?' * len(ids))})",
                             (p, *ids)).fetchone()[0] == len(ids) for p, ids in saves.items() if ids)
        else:
            refs, short, wrong_who, per_person_ok = [], [], 0, True
    expected = a.users * a.rounds
    if notes_after - notes_before != len(made) or len(made) != expected:
        problems.append(f"saves: expected {expected}, made {len(made)}, found {notes_after - notes_before} new in the database")
    if len(refs) != len(set(refs)) or any(r.startswith("DRAFT-") for r in refs):
        problems.append("an LN number was given twice or left as a draft")
    if short:
        problems.append(f"{len(short)} schedule(s) not whole")
    if wrong_who or not per_person_ok:
        problems.append("an audit row names the wrong person")

    times.sort()
    pct = lambda p: times[min(len(times) - 1, int(len(times) * p))] * 1000 if times else 0  # noqa: E731
    print(f"Server processes: {len(urls)}   People at once: {a.users}   rounds each: {a.rounds}   pages opened: {len(times)}   saves: {len(made)} of {expected}   time: {wall:.1f} s")
    if times:
        print(f"Page times (ms): median {statistics.median(times) * 1000:.0f}   95% under {pct(0.95):.0f}   slowest {times[-1] * 1000:.0f}")
    if save_times:
        save_times.sort()
        print(f"Save times (ms): median {statistics.median(save_times) * 1000:.0f}   slowest {save_times[-1] * 1000:.0f}")
    print("Slowest pages (total ms / of which on the server ms / page): "
          + ", ".join(f"{t * 1000:.0f}/{srv}/{u}" for t, srv, u in sorted(slow, reverse=True)[:6]))
    print(f"LN numbers unique: {len(refs) == len(set(refs))}   schedules whole: {not short}   audit names right: {not wrong_who and per_person_ok}   mix-ups: {len(mixups)}")
    if problems:
        print("FAILED:")
        for p in problems[:30]:
            print("  -", p)
        return 1
    print("PASSED: no failed page, no mix-up, every save there once, numbers unique, schedules whole, audit rows right.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
