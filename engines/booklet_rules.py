"""Check booklet management (owner, 2026-10-09: "add, delete, update, run audit gap"; "it must generate checkbooklet code to account proper sequencing";
"assigned checkbooklet for example me or to someone"). One place for the check booklets: the page Bank > Check booklets (web/payables/booklets.py).

  * add_booklet        -- the CODE is generated, never typed: BK-0001, BK-0002 ... per bank account, in the order the booklets are added. A booklet that
                          does not follow the one before it asks first ("leaves 4501-4600 are in no booklet") and, kept anyway, goes on the Review list.
                          A range that overlaps another booklet is refused.
  * update_booklet     -- the date received always; the range only so that no leaf already used falls outside it; the status (Active / Exhausted / Archived).
  * delete_booklet     -- only while no leaf of it is used; otherwise archive it (one of the hard stops that protect the record).
  * hand_over          -- who holds the booklet: a person with an account, or a typed name ("Mama"). Each handover is audited.
  * listing            -- the booklets with what is used, spoiled and open, the holder, "up to no." and the gaps (in a booklet, and between booklets).
  * renumber_codes     -- once: the booklets on file before this get their BK codes, in check-number order.
The gap audit itself is engines/checkbook_service.audit_booklet_gaps; "used but not recorded" is unrecorded_leaves."""
from __future__ import annotations

from datetime import date

from engines.clock import manila_today
from engines.common import get_conn, write_tx
from engines.db_engine import next_doc_number
from engines.permissions import require as require_permission

PRIMARY = 1
STATUSES = ("Active", "Exhausted", "Archived")
GAP = "GAP"                      # add_booklet / update_booklet: the range leaves numbers in no booklet -- ask first (the gap is returned)


def _may() -> tuple[bool, str]:
    return require_permission("booklets.use")


def _code_no(code) -> int:
    text = str(code or "")
    return int(text[3:]) if text.startswith("BK-") and text[3:].isdigit() else 0


def _next_code(conn, bank_account_id: int, take: bool) -> str:
    """The next BK code for this bank account; `take` reserves it (inside a write), otherwise it is only shown ("will be BK-0004")."""
    used = max((_code_no(r[0]) for r in conn.execute("SELECT booklet_code FROM check_booklets WHERE bank_account_id = ?", (bank_account_id,))), default=0)
    if take:
        return f"BK-{next_doc_number(conn, 'BK', bank_account_id, used):04d}"
    row = conn.execute("SELECT last_no FROM doc_counters WHERE series = 'BK' AND year = ?", (bank_account_id,)).fetchone()
    return f"BK-{max(int(row[0]) if row else 0, used) + 1:04d}"


def next_code(bank_account_id: int = PRIMARY) -> str:
    with get_conn() as conn:
        return _next_code(conn, bank_account_id, take=False)


def suggest_range(bank_account_id: int = PRIMARY) -> tuple[int | None, int | None]:
    """A new booklet usually follows the last one and has as many leaves: (first, last), or (None, None) when there is no booklet yet."""
    with get_conn() as conn:
        row = conn.execute("SELECT start_number, end_number FROM check_booklets WHERE bank_account_id = ? AND status != 'Archived' ORDER BY end_number DESC LIMIT 1",
                           (bank_account_id,)).fetchone()
    if not row:
        return None, None
    return int(row[1]) + 1, int(row[1]) + 1 + (int(row[1]) - int(row[0]))


def _overlap(conn, bank_account_id: int, start: int, end: int, but: int | None = None):
    return conn.execute("""SELECT booklet_code, start_number, end_number FROM check_booklets WHERE bank_account_id = ? AND status != 'Archived'
                           AND start_number <= ? AND end_number >= ? AND booklet_id IS NOT ?""", (bank_account_id, end, start, but)).fetchone()


def _gap_before(conn, bank_account_id: int, start: int, but: int | None = None) -> tuple[int, int] | None:
    """The numbers between the booklet before this range and the range itself, when they are in no booklet: (first, last)."""
    row = conn.execute("""SELECT MAX(end_number) FROM check_booklets WHERE bank_account_id = ? AND status != 'Archived' AND end_number < ? AND booklet_id IS NOT ?""",
                       (bank_account_id, start, but)).fetchone()
    return (int(row[0]) + 1, start - 1) if row and row[0] is not None and start > int(row[0]) + 1 else None


def _leaves_used(conn, booklet_id: int, bank_account_id: int, start: int, end: int) -> set[int]:
    """The leaves of start..end that are accounted for as used: a check under that number (any status), a spoiled leaf, a guarantee check."""
    from engines.checkbook_service import _raw_sequence_number
    branch = (conn.execute("SELECT branch_code FROM bank_accounts WHERE bank_account_id = ?", (bank_account_id,)).fetchone() or [""])[0]
    numbers = [r[0] for r in conn.execute("SELECT check_number FROM checks_issued WHERE bank_account_id = ? AND check_number IS NOT NULL AND check_number != ''", (bank_account_id,))]
    numbers += [r[0] for r in conn.execute("SELECT check_number FROM spoiled_checks WHERE bank_account_id = ?", (bank_account_id,))]
    try:
        numbers += [r[0] for r in conn.execute("SELECT check_number FROM guarantee_checks WHERE bank_account_id = ?", (bank_account_id,))]
    except Exception:                                    # noqa: BLE001 -- an older database without the table
        pass
    out = set()
    for text in numbers:
        t = (text or "").strip()
        if t.isdigit() and start <= int(t) <= end:
            out.add(int(t))
            continue
        short = _raw_sequence_number(t, branch)
        if short is not None and start <= short <= end:
            out.add(int(short))
    return out


def _holder(conn, user_id, name) -> str:
    if user_id:
        row = conn.execute("SELECT name, email FROM app_users WHERE user_id = ?", (user_id,)).fetchone()
        if row:
            return row[0] or row[1]
    return " ".join(str(name or "").split())


def add_booklet(start, end, received=None, holder_user_id: int | None = None, holder_name: str = "", confirm_gap: bool = False,
                bank_account_id: int = PRIMARY) -> tuple[bool, str, int | None, tuple[int, int] | None]:
    """Registers a booklet under the next BK code. Returns (ok, message, booklet_id, gap). When its first number does not follow the booklet before it and
    confirm_gap is not given, nothing is saved: the message is GAP and `gap` = the numbers in no booklet, to ask about."""
    ok, why = _may()
    if not ok:
        return False, why, None, None
    try:
        start, end = int(start), int(end)
    except (TypeError, ValueError):
        return False, "Type the first and last check numbers of the booklet.", None, None
    if end < start:
        return False, "The last number cannot be before the first.", None, None
    day = received if isinstance(received, date) else manila_today()
    with write_tx() as conn:
        clash = _overlap(conn, bank_account_id, start, end)
        if clash:
            return False, f"Overlaps {clash[0] or 'an existing booklet'} ({clash[1]}-{clash[2]}): a check number can be in one booklet only.", None, None
        gap = _gap_before(conn, bank_account_id, start)
        if gap and not confirm_gap:
            return False, GAP, None, gap
        code = _next_code(conn, bank_account_id, take=True)
        who = _holder(conn, holder_user_id, holder_name)
        bid = conn.execute(
            """INSERT INTO check_booklets (bank_account_id, booklet_code, start_number, end_number, next_available_number, date_received, status,
                                           holder_user_id, holder_name, held_since) VALUES (?, ?, ?, ?, ?, ?, 'Active', ?, ?, ?) RETURNING booklet_id""",
            (bank_account_id, code, start, end, start, str(day), holder_user_id or None, None if holder_user_id else (who or None), str(day) if who else None)).fetchone()[0]
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'BOOKLET_ADDED', 'check_booklets', ?, ?)",
                     (bid, f"{code}: {start}-{end}, received {day:%m/%d/%Y}" + (f", held by {who}" if who else "")))
        if gap:
            # the owner confirmed it: the bank's own jump (2026-10-12: "I cannot control what sequence will give to us when we order").
            # Recorded on the booklet and in the audit -- no Review flag, and the list shows it quietly from now on.
            conn.execute("UPDATE check_booklets SET gap_before_confirmed_at = CURRENT_TIMESTAMP, gap_before_confirmed_by = acting_user() WHERE booklet_id = ?", (bid,))
            conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'BOOKLET_GAP_CONFIRMED', 'check_booklets', ?, ?)",
                         (bid, f"{code} starts at {start}: check numbers {gap[0]}-{gap[1]} are in no booklet -- confirmed as the bank's own numbering jump."))
    return True, f"{code} registered: {start}-{end}." + (f" Numbers {gap[0]}-{gap[1]}: noted as the bank's own jump." if gap else ""), int(bid), gap


def update_booklet(booklet_id: int, start, end, received, status: str, confirm_gap: bool = False) -> tuple[bool, str, tuple[int, int] | None]:
    """Changes a booklet's range, date received or status. The code never changes. The range may change only so that no leaf already used, spoiled or
    held as a guarantee check falls outside it, and it overlaps no other booklet. Audited old -> new."""
    ok, why = _may()
    if not ok:
        return False, why, None
    try:
        start, end = int(start), int(end)
    except (TypeError, ValueError):
        return False, "Type the first and last check numbers.", None
    if end < start:
        return False, "The last number cannot be before the first.", None
    if status not in STATUSES:
        return False, "Choose Active, Exhausted or Archived.", None
    with write_tx() as conn:
        row = conn.execute("SELECT bank_account_id, booklet_code, start_number, end_number, date_received, status, next_available_number FROM check_booklets WHERE booklet_id = ?",
                           (booklet_id,)).fetchone()
        if not row:
            return False, "Booklet not found.", None
        acct, code, old_start, old_end, old_day, old_status, nxt = row
        day = received if isinstance(received, date) else date.fromisoformat(str(old_day)[:10])
        gap = None
        if (start, end) != (int(old_start), int(old_end)):
            used = _leaves_used(conn, booklet_id, acct, int(old_start), int(old_end))
            outside = sorted(n for n in used if not start <= n <= end)
            if outside:
                return False, (f"Leaves already used would fall outside {start}-{end}: {', '.join(map(str, outside[:6]))}" + (" …" if len(outside) > 6 else "")
                               + ". The range can change only around the leaves in use."), None
        if status != "Archived":
            clash = _overlap(conn, acct, start, end, but=booklet_id)
            if clash:
                return False, f"Overlaps {clash[0] or 'another booklet'} ({clash[1]}-{clash[2]}).", None
            if start != int(old_start):
                gap = _gap_before(conn, acct, start, but=booklet_id)
                if gap and not confirm_gap:
                    return False, GAP, gap
        conn.execute("UPDATE check_booklets SET start_number = ?, end_number = ?, date_received = ?, status = ?, next_available_number = ? WHERE booklet_id = ?",
                     (start, end, str(day), status, min(max(int(nxt), start), max(end, start)), booklet_id))
        old = f"{old_start}-{old_end}, received {str(old_day)[:10]}, {old_status}"
        new = f"{start}-{end}, received {day}, {status}"
        if old != new:
            conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, old_values, new_values) VALUES (acting_user(), 'BOOKLET_CHANGED', 'check_booklets', ?, ?, ?)",
                         (booklet_id, f"{code}: {old}", f"{code}: {new}"))
        if gap:
            conn.execute("UPDATE check_booklets SET gap_before_confirmed_at = CURRENT_TIMESTAMP, gap_before_confirmed_by = acting_user() WHERE booklet_id = ?", (booklet_id,))
            conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'BOOKLET_GAP_CONFIRMED', 'check_booklets', ?, ?)",
                         (booklet_id, f"{code} now starts at {start}: check numbers {gap[0]}-{gap[1]} are in no booklet -- confirmed as the bank's own numbering jump."))
    return True, f"{code} saved.", gap


def set_status(booklet_id: int, status: str) -> tuple[bool, str]:
    """Archive a booklet (retired stock: out of the number picker), or bring it back."""
    ok, why = _may()
    if not ok:
        return False, why
    if status not in STATUSES:
        return False, "Choose Active, Exhausted or Archived."
    with write_tx() as conn:
        row = conn.execute("SELECT bank_account_id, booklet_code, start_number, end_number, status FROM check_booklets WHERE booklet_id = ?", (booklet_id,)).fetchone()
        if not row:
            return False, "Booklet not found."
        if status != "Archived" and row[4] == "Archived":
            clash = _overlap(conn, row[0], int(row[2]), int(row[3]), but=booklet_id)
            if clash:
                return False, f"It can't come back: its numbers now overlap {clash[0]} ({clash[1]}-{clash[2]})."
        conn.execute("UPDATE check_booklets SET status = ? WHERE booklet_id = ?", (status, booklet_id))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, old_values, new_values) VALUES (acting_user(), ?, 'check_booklets', ?, ?, ?)",
                     ("BOOKLET_ARCHIVED" if status == "Archived" else "BOOKLET_CHANGED", booklet_id, f"{row[1]}: {row[4]}", f"{row[1]}: {status}"))
    return True, f"{row[1]} is {status.lower()}."


def delete_booklet(booklet_id: int) -> tuple[bool, str]:
    """Removes a booklet registered by mistake -- only while no leaf of it is used (no check, spoiled leaf or guarantee check carries one of its numbers and
    no check is linked to it). Otherwise it is archived instead: the record of used leaves is never deleted."""
    ok, why = _may()
    if not ok:
        return False, why
    with write_tx() as conn:
        row = conn.execute("SELECT bank_account_id, booklet_code, start_number, end_number FROM check_booklets WHERE booklet_id = ?", (booklet_id,)).fetchone()
        if not row:
            return False, "Booklet not found."
        linked = conn.execute("SELECT COUNT(*) FROM checks_issued WHERE booklet_id = ?", (booklet_id,)).fetchone()[0]
        used = _leaves_used(conn, booklet_id, row[0], int(row[2]), int(row[3]))
        if linked or used:
            return False, f"{row[1]} has leaves in use ({len(used) or linked}), so it can't be deleted. Archive it instead."
        conn.execute("DELETE FROM check_booklets WHERE booklet_id = ?", (booklet_id,))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, old_values) VALUES (acting_user(), 'BOOKLET_DELETED', 'check_booklets', ?, ?)",
                     (booklet_id, f"{row[1]}: {row[2]}-{row[3]}, no leaf used"))
    return True, f"{row[1]} deleted. Its code is not used again."


def hand_over(booklet_id: int, user_id: int | None, name: str = "", since=None) -> tuple[bool, str]:
    """Who holds the booklet now: a person with an account (user_id) or a typed name; both empty = nobody. Each handover is audited (from -> to)."""
    ok, why = _may()
    if not ok:
        return False, why
    with write_tx() as conn:
        row = conn.execute("SELECT booklet_code, holder_user_id, holder_name FROM check_booklets WHERE booklet_id = ?", (booklet_id,)).fetchone()
        if not row:
            return False, "Booklet not found."
        before, after = _holder(conn, row[1], row[2]) or "nobody", _holder(conn, user_id, name) or "nobody"
        if user_id and not conn.execute("SELECT 1 FROM app_users WHERE user_id = ?", (user_id,)).fetchone():
            return False, "That person was not found."
        if before == after:
            return True, f"{row[0]} is already with {after}."
        day = since if isinstance(since, date) else manila_today()
        conn.execute("UPDATE check_booklets SET holder_user_id = ?, holder_name = ?, held_since = ? WHERE booklet_id = ?",
                     (user_id or None, None if user_id else (" ".join(str(name or "").split()) or None), str(day) if after != "nobody" else None, booklet_id))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, old_values, new_values) VALUES (acting_user(), 'BOOKLET_HANDED_OVER', 'check_booklets', ?, ?, ?)",
                     (booklet_id, f"{row[0]}: held by {before}", f"{row[0]}: held by {after} since {day:%m/%d/%Y}"))
    return True, f"{row[0]} is now with {after}."


def holder_of(booklet: dict, people: dict | None = None) -> str:
    """The holder's name for a booklet row (holder_user_id / holder_name); '' = nobody."""
    uid = booklet.get("holder_user_id")
    if uid is not None and uid == uid and uid:                # not None, not NaN
        if people is None:
            with get_conn() as conn:
                return _holder(conn, int(uid), "")
        return people.get(int(uid), "")
    name = booklet.get("holder_name")
    return name if isinstance(name, str) else ""


def listing(include_archived: bool = False, bank_account_id: int = PRIMARY) -> list[dict]:
    """The booklets in check-number order: code, range, received, status, holder, used / spoiled / open leaves, next free, "up to no.", the leaves used but
    not recorded, the audit's skipped numbers and duplicates, and `gap_before` = numbers between it and the booklet before it that are in no booklet."""
    from engines.check_rules import get_available_booklet_numbers
    from engines.checkbook_service import audit_booklet_gaps, compress_leaf_ranges, unrecorded_leaves
    with get_conn() as conn:
        cur = conn.execute(f"""SELECT * FROM check_booklets WHERE bank_account_id = ? {'' if include_archived else "AND status != 'Archived'"} ORDER BY start_number, booklet_id""",
                           (bank_account_id,))
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        people = {int(r[0]): (r[1] or r[2]) for r in conn.execute("SELECT user_id, name, email FROM app_users")}
    out, prev_end = [], None
    for b in rows:
        bid, start, end = int(b["booklet_id"]), int(b["start_number"]), int(b["end_number"])
        a = audit_booklet_gaps(bid)
        states = list(a["detailed_breakdown"]["state"]) if not a["detailed_breakdown"].empty else []
        open_n = sum(1 for s in states if s == "Open Stock")
        try:
            free = get_available_booklet_numbers(bank_account_id, b) if b["status"] == "Active" else []
        except Exception:                                # noqa: BLE001 -- the listing must not break on one booklet
            free = []
        missing = unrecorded_leaves(bid) if b["status"] != "Archived" else []
        archived = b["status"] == "Archived"
        out.append({**b, "code": b["booklet_code"] or f"Booklet #{bid}", "holder": holder_of(b, people), "leaves": end - start + 1,
                    "used": len(states) - open_n - a["spoiled_count"] - a["missing_gaps_count"], "spoiled": a["spoiled_count"], "open": open_n,
                    "next_free": free[0] if free else None, "unrecorded": len(missing), "unrecorded_text": compress_leaf_ranges(missing) if missing else "",
                    "skipped": a["missing_gaps_count"], "duplicates": a["duplicate_numbers_count"],
                    "gap_before": (prev_end + 1, start - 1) if (prev_end is not None and not archived and start > prev_end + 1) else None,
                    "gap_confirmed": bool(b.get("gap_before_confirmed_at"))})
        if not archived:
            prev_end = max(end, prev_end or end)
    return out


def renumber_codes(bank_account_id: int = PRIMARY) -> int:
    """Once: the booklets registered before codes were generated get BK-0001, BK-0002 ... in check-number order (the lowest range first). The old code
    goes in the audit log; checks link to a booklet by its id, so nothing else changes. Returns how many were renumbered; 0 when already done."""
    from engines.settings_store import get_setting, set_setting
    flag_key = f"booklet_codes_bk_{bank_account_id}"
    if str(get_setting(flag_key, "0")) == "1":
        return 0
    n = 0
    with write_tx() as conn:
        rows = conn.execute("SELECT booklet_id, booklet_code FROM check_booklets WHERE bank_account_id = ? ORDER BY start_number, booklet_id", (bank_account_id,)).fetchall()
        for bid, old in rows:
            new = f"BK-{next_doc_number(conn, 'BK', bank_account_id, 0):04d}"
            if old != new:
                conn.execute("UPDATE check_booklets SET booklet_code = ? WHERE booklet_id = ?", (new, bid))
                conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, old_values, new_values) VALUES (acting_user(), 'BOOKLET_CODE_SET', 'check_booklets', ?, ?, ?)",
                             (bid, str(old or ""), new))
                n += 1
    set_setting(flag_key, "1")
    return n
