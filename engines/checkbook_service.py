import sqlite3
import pandas as pd

import os
DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")


def _raw_sequence_number(check_number: str, branch_code: str) -> int | None:
    """Recovers the raw booklet-leaf sequence number a check_number represents, or None if it
    doesn't look like one at all (non-numeric).

    2026-09-17 fix: a check assigned from a booklet is stored as the branch-code-prefixed,
    zero-padded Metrobank format (format_metrobank_check_number(), e.g. sequence 1000 on branch
    399 -> "3990001000"), not the raw sequence number itself. The booklet's own start_number/
    end_number columns ARE the raw sequence range (e.g. 1000-1050). Comparing the two directly
    via CAST(check_number AS INTEGER) BETWEEN start_number AND end_number -- the previous
    approach -- compares 3990001000 against 1000-1050, which can never match: this silently made
    the entire gap/duplicate audit report "all clear" for every real, booklet-issued check. This
    strips a matching branch-code prefix first, and only falls back to the bare number as-is for
    older freehand-typed entries that predate the booklet-dropdown flow (Phase 6 Redux v2)."""
    check_number = (check_number or "").strip()
    if not check_number:
        return None
    if check_number.startswith(branch_code) and len(check_number) == len(branch_code) + 7:
        remainder = check_number[len(branch_code):]
        if remainder.isdigit():
            return int(remainder)
    return int(check_number) if check_number.isdigit() else None


def audit_booklet_gaps(booklet_id: int) -> dict:
    """Audits a registered booklet's leaf range and flags leaves that were skipped without a
    reason on file. Under Phase 6 Redux v2's manual-dropdown assignment model, every number comes
    from a dropdown of that booklet's own still-open leaves, so a normal assignment shouldn't
    produce a gap at all -- a real gap here usually means a leaf was assigned outside this app
    (historical pre-Redux data, typed in freehand back then), or a spoiled leaf logged after the
    fact. Still flags a leaf recorded under two different check_number strings meaning the same
    number (e.g. "1" vs "01"), a realistic artifact of that older free-typed era."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT bank_account_id, start_number, end_number, booklet_code FROM check_booklets WHERE booklet_id = ?", (booklet_id,))
        booklet = cursor.fetchone()
        if not booklet:
            raise ValueError(f"Booklet ID {booklet_id} not found.")

        bank_account_id, start_num, end_num, booklet_code = booklet
        (branch_code,) = cursor.execute("SELECT branch_code FROM bank_accounts WHERE bank_account_id = ?", (bank_account_id,)).fetchone()

        df_all = pd.read_sql_query("""
            SELECT check_number, amount, status, check_date
            FROM checks_issued
            WHERE bank_account_id = ? AND check_number IS NOT NULL AND check_number != ''
        """, conn, params=(bank_account_id,))
        # Leaves logged as spoiled / error checks (Log a spoiled check) -- accounted for, so they are neither open stock nor a gap.
        df_spoiled = pd.read_sql_query(
            "SELECT check_number, reason FROM spoiled_checks WHERE bank_account_id = ?", conn, params=(bank_account_id,))
        try:                                                              # a guarantee check held by a lender is accounted for (migration 057)
            df_guarantee = pd.read_sql_query(
                "SELECT check_number, 'Guarantee check held by a lender' AS reason FROM guarantee_checks WHERE bank_account_id = ?", conn, params=(bank_account_id,))
            df_spoiled = pd.concat([df_spoiled, df_guarantee], ignore_index=True)
        except Exception:
            pass

    # A booklet range is normally the FULL check number as printed (e.g. 3992554301-3992554400),
    # and a check is stored under that same number. Older raw-sequence ranges (1000-1050 with
    # checks stored as branch code + 7 digits) are still understood, so a leaf matches if either
    # its full number or its prefix-stripped number falls in the range.
    def _leaf_in_range(cn):
        text = (cn or "").strip()
        if text.isdigit() and start_num <= int(text) <= end_num:
            return int(text)
        stripped = _raw_sequence_number(text, branch_code)
        return stripped if stripped is not None and start_num <= stripped <= end_num else None

    df_all['check_num_int'] = df_all['check_number'].apply(_leaf_in_range)
    df_recorded = df_all[df_all['check_num_int'].notna()].copy()
    df_recorded['check_num_int'] = df_recorded['check_num_int'].astype(int)
    max_issued = df_recorded['check_num_int'].max() if not df_recorded.empty else start_num - 1

    spoiled_by_leaf = {}
    for _, sp in df_spoiled.iterrows():
        leaf = _leaf_in_range(sp['check_number'])
        if leaf is not None:
            spoiled_by_leaf[int(leaf)] = (sp['reason'] or '').strip()

    inventory_breakdown, missing_gaps, duplicate_numbers, spoiled_leaves = [], [], [], []

    for num in range(start_num, end_num + 1):
        num_str = str(num).zfill(10)
        matches = df_recorded[df_recorded['check_num_int'] == num]
        if num in spoiled_by_leaf and len(matches) >= 1:
            inventory_breakdown.append({
                'check_number': num_str, 'state': 'CRITICAL: SPOILED AND ISSUED',
                'amount': float(matches['amount'].sum()), 'note': 'Logged as a spoiled leaf but also recorded as a check',
            })
            duplicate_numbers.append(num_str)
        elif num in spoiled_by_leaf:
            reason = spoiled_by_leaf[num]
            inventory_breakdown.append({'check_number': num_str, 'state': 'Spoiled (error check)', 'amount': 0.0,
                                        'note': f"Error check: {reason}" if reason else 'Logged as spoiled'})
            spoiled_leaves.append(num_str)
        elif len(matches) > 1:
            texts = ", ".join(sorted(matches['check_number'].unique()))
            inventory_breakdown.append({
                'check_number': num_str, 'state': 'CRITICAL: DUPLICATE CHECK NUMBER',
                'amount': float(matches['amount'].sum()), 'note': f"Recorded under conflicting text: {texts}",
            })
            duplicate_numbers.append(num_str)
        elif len(matches) == 1:
            row = matches.iloc[0]
            inventory_breakdown.append({'check_number': num_str, 'state': row['status'], 'amount': row['amount'], 'note': 'Ledger Record'})
        elif num <= max_issued:
            inventory_breakdown.append({'check_number': num_str, 'state': 'CRITICAL: MISSING GAP', 'amount': 0.0, 'note': 'Unaccounted Stock'})
            missing_gaps.append(num_str)
        else:
            inventory_breakdown.append({'check_number': num_str, 'state': 'Open Stock', 'amount': 0.0, 'note': 'Available Leaf'})

    return {
        'booklet_code': booklet_code or f"Booklet #{booklet_id}",
        'total_leaves': (end_num - start_num) + 1,
        'missing_gaps_count': len(missing_gaps),
        'missing_gaps_list': missing_gaps,
        'duplicate_numbers_count': len(duplicate_numbers),
        'duplicate_numbers_list': duplicate_numbers,
        'spoiled_count': len(spoiled_leaves),
        'spoiled_list': spoiled_leaves,
        'detailed_breakdown': pd.DataFrame(inventory_breakdown)
    }


def get_checkbook_alerts() -> pd.DataFrame:
    """Runs the gap audit for every Active or Exhausted booklet and returns only the ones with a
    real problem (a missing gap or a duplicate check number) -- the audit itself is a manual,
    on-demand tool, so nothing else prompts anyone to actually run it. Meant for a Dashboard-
    level "you should check this" banner, same reasoning as the credit-card statement-gap
    reminder: "properly account all checks... i should be warned or notified otherwise."
    Exhausted booklets are included (2026-09-17 fix) -- a fully-used booklet's history is just as
    real as an Active one's, and excluding it meant a gap/duplicate stopped being flagged the
    moment its last leaf was assigned. Archived (explicitly retired) stays excluded."""
    with sqlite3.connect(DB_PATH) as conn:
        booklets = pd.read_sql_query("SELECT booklet_id FROM check_booklets WHERE status IN ('Active', 'Exhausted')", conn)
    rows = []
    for booklet_id in booklets['booklet_id']:
        result = audit_booklet_gaps(int(booklet_id))
        if result['missing_gaps_count'] > 0 or result['duplicate_numbers_count'] > 0:
            rows.append({
                'booklet_id': booklet_id,
                'booklet_code': result['booklet_code'],
                'missing_gaps_count': result['missing_gaps_count'],
                'duplicate_numbers_count': result['duplicate_numbers_count'],
            })
    return pd.DataFrame(rows, columns=['booklet_id', 'booklet_code', 'missing_gaps_count', 'duplicate_numbers_count'])


# ---------------------------------------------------------------------------------------------------------------------------------------
# Leaves used but not recorded (owner, 2026-10-01). The owner's mother hand-writes urgent checks from her own booklet and sends photos on
# Messenger; the owner records them. A forgotten photo = a check missing from the system until the bank clears it (months, for post-dated
# checks). audit_booklet_gaps() only sees gaps BELOW the highest recorded leaf, and flags leaves the bank cleared before the system started.
# This one also takes "she is up to no. ___" and counts a leaf as accounted for when it is recorded (any status, voided included), logged
# spoiled, or already on a bank statement line.
# ---------------------------------------------------------------------------------------------------------------------------------------
def compress_leaf_ranges(numbers) -> str:
    """[1, 2, 3, 7, 9, 10] -> '1-3, 7, 9-10'."""
    nums = sorted(set(int(n) for n in numbers))
    parts, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        parts.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ", ".join(parts)


def _booklet_leaf_context(conn, booklet_id: int):
    row = conn.execute("SELECT bank_account_id, start_number, end_number, booklet_code, reported_up_to FROM check_booklets WHERE booklet_id = ?", (booklet_id,)).fetchone()
    if not row:
        return None
    bank_account_id, start_num, end_num, code, reported = row
    branch = conn.execute("SELECT branch_code FROM bank_accounts WHERE bank_account_id = ?", (bank_account_id,)).fetchone()[0]
    return bank_account_id, int(start_num), int(end_num), code or f"Booklet #{booklet_id}", reported, branch


def unrecorded_leaves(booklet_id: int) -> list[int]:
    """Leaves of this booklet (full numbers) known to be used -- up to the highest recorded leaf, or the number the owner reported -- that are
    not recorded, not logged spoiled and not on any bank statement line. Empty when nothing is known to be used yet."""
    from engines.bank_statement_service import norm_check_number

    with sqlite3.connect(DB_PATH) as conn:
        ctx = _booklet_leaf_context(conn, booklet_id)
        if ctx is None:
            raise ValueError(f"Booklet ID {booklet_id} not found.")
        bank_account_id, start_num, end_num, _code, reported, branch = ctx
        recorded = [r[0] for r in conn.execute("SELECT check_number FROM checks_issued WHERE bank_account_id = ? AND check_number IS NOT NULL AND check_number != ''", (bank_account_id,))]
        spoiled = [r[0] for r in conn.execute("SELECT check_number FROM spoiled_checks WHERE bank_account_id = ?", (bank_account_id,))]
        on_bank = {norm_check_number(r[0]) for r in conn.execute("SELECT DISTINCT check_number FROM bank_transactions WHERE check_number IS NOT NULL AND check_number != ''")}
        try:                                                              # guarantee checks given to lenders (migration 057) are accounted for too
            spoiled = spoiled + [r[0] for r in conn.execute("SELECT check_number FROM guarantee_checks WHERE bank_account_id = ?", (bank_account_id,))]
        except sqlite3.OperationalError:
            pass

    def leaf(text):
        t = (text or "").strip()
        if t.isdigit() and start_num <= int(t) <= end_num:
            return int(t)
        stripped = _raw_sequence_number(t, branch)
        return stripped if stripped is not None and start_num <= stripped <= end_num else None

    recorded_leaves = {n for n in (leaf(c) for c in recorded) if n is not None}
    accounted = recorded_leaves | {n for n in (leaf(c) for c in spoiled) if n is not None}
    limit = max(max(recorded_leaves, default=start_num - 1), int(reported) if reported else start_num - 1)
    limit = min(limit, end_num)
    return [n for n in range(start_num, limit + 1) if n not in accounted and norm_check_number(str(n)) not in on_bank]


def set_reported_up_to(booklet_id: int, number: int) -> tuple[bool, str]:
    """The owner types the last leaf number on the latest photo ("Mom's booklet is up to no. ___")."""
    with sqlite3.connect(DB_PATH) as conn:
        ctx = _booklet_leaf_context(conn, booklet_id)
        if ctx is None:
            return False, "Booklet not found."
        _acct, start_num, end_num, code, _rep, _branch = ctx
        number = int(number)
        if not start_num <= number <= end_num:
            return False, f"{number} is outside this booklet ({start_num}-{end_num})."
        conn.execute("UPDATE check_booklets SET reported_up_to = ? WHERE booklet_id = ?", (number, booklet_id))
        conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('BOOKLET_REPORTED_UP_TO', 'check_booklets', ?, ?)",
                     (booklet_id, f"{code}: leaves reported used up to {number}"))
    return True, f"{code}: used up to {number}."


def unrecorded_alerts() -> pd.DataFrame:
    """One row per Active / Exhausted booklet that has leaves used but not recorded: booklet, how many, which (short ranges), up to which number."""
    with sqlite3.connect(DB_PATH) as conn:
        booklets = conn.execute("SELECT booklet_id FROM check_booklets WHERE status IN ('Active', 'Exhausted') ORDER BY booklet_id").fetchall()
    rows = []
    for (bid,) in booklets:
        missing = unrecorded_leaves(int(bid))
        if missing:
            with sqlite3.connect(DB_PATH) as conn:
                _a, start_num, end_num, code, reported, _b = _booklet_leaf_context(conn, int(bid))
                highest = conn.execute("SELECT MAX(CAST(check_number AS INTEGER)) FROM checks_issued WHERE CAST(check_number AS INTEGER) BETWEEN ? AND ?", (start_num, end_num)).fetchone()[0]
            rows.append({"booklet_id": int(bid), "booklet": code, "count": len(missing), "leaves": compress_leaf_ranges(missing),
                         "up_to": max(int(highest or 0), int(reported or 0))})
    return pd.DataFrame(rows, columns=["booklet_id", "booklet", "count", "leaves", "up_to"])
