"""Bank and cash page (2026-09-27, owner: "I do not have the time to reconcile which checks are cleared"). The owner downloads the Metrobank
MBOS statement and drops it here; the app reads it, stores each bank line once, and matches our checks automatically
(engines/bank_statement_service.py). Only the exceptions need a look. Phase 2 adds the cash insights on this page.

Show-both rule (owner): the rest of the app still treats a check as cleared on its check date; the bank's real cleared date is shown here
and next to the checks."""
from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from engines.bank_statement_service import (
    exceptions, import_statement, last_import, mark_reviewed, not_yet_cleared, parse_statement, preview_matches, statement_coverage,
)
from engines.common import php
from views.shared import date_col, guard_duplicate_submit, money_col, page_header, q, refresh, section

KIND_LABELS = {"CHECK": "Our checks clearing", "DEPOSIT": "Money in (deposits, transfers)", "RETURNED_CHECK": "Returned (bounced) deposited checks",
               "BILLS_PAYMENT": "Bills payments", "DEBIT_MEMO": "Bank debit memos (loan debits, charges)", "OTHER": "Other"}
EXCEPTION_LABELS = {
    "cancelled_check_cleared": "A check we cancelled / replaced was cleared by the bank",
    "amount_mismatch": "Cleared amount differs from our check",
    "returned_check": "A deposited check was returned (bounced)",
    "not_in_system": "Check not in the system (older than the system, or never recorded)",
}


def _import_tab() -> None:
    st.caption("Download the Statement of Account from Metrobank MBOS (PDF works; CSV / Excel too if MBOS offers them) and drop it here. "
               "The same file is never imported twice, and overlapping periods are fine — lines already on file are skipped.")
    up = st.file_uploader("Bank statement", type=["pdf", "csv", "xlsx", "xls"], key="bank_stmt_upload", label_visibility="collapsed")
    if up is None:
        return
    data = up.getvalue()
    h = hashlib.sha256(data).hexdigest()
    cache = st.session_state.get("_bank_preview")
    if not cache or cache["hash"] != h:
        with st.spinner("Reading the statement..."):
            parsed = parse_statement(data, up.name)
        cache = {"hash": h, "parsed": parsed, "preview": preview_matches(parsed) if parsed.lines else {}}
        st.session_state["_bank_preview"] = cache
    parsed, prev = cache["parsed"], cache["preview"]
    for m in parsed.messages:
        st.warning(m)
    if not parsed.lines:
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Period", f"{parsed.period_from:%b %d} – {parsed.period_to:%b %d, %Y}", border=True)
    c2.metric("Lines", f"{len(parsed.lines):,}", border=True)
    c3.metric("Closing balance", php(parsed.closing_balance) if parsed.closing_balance is not None else "—", border=True)
    c4.metric("Running balances", "All add up" if parsed.balance_ok else f"{parsed.bad_balance_steps} don't add up", border=True)
    st.caption(f"Account {parsed.masked_account() or '—'} · opening balance {php(parsed.opening_balance) if parsed.opening_balance is not None else '—'}")
    kinds = pd.Series([x.kind for x in parsed.lines]).value_counts()
    st.dataframe(pd.DataFrame({"What": [KIND_LABELS.get(k, k) for k in kinds.index], "Lines": kinds.values}), hide_index=True)
    if prev:
        st.markdown(f"Checks on this statement: **{prev['matched']} match our checks** (number and amount)"
                    + (f", {prev['amount_mismatch']} with a different amount" if prev["amount_mismatch"] else "")
                    + (f", **{prev['cancelled_check_cleared']} that we cancelled**" if prev["cancelled_check_cleared"] else "")
                    + (f", {prev['not_in_system']} not in the system" if prev["not_in_system"] else "") + ".")
    if st.button("Import this statement", icon=":material/upload_file:", type="primary", key="bank_import_btn"):
        if not guard_duplicate_submit("bank_import", h):
            st.warning("Duplicate click ignored — identical to the click just before it.")
            return
        with st.spinner("Importing and matching..."):
            res = import_statement(data, up.name)
        st.session_state.pop("_bank_preview", None)
        if res.get("already_imported"):
            st.info(res["messages"][0])
            return
        refresh(done=f"Imported {res['new_lines']} new line(s)" + (f" ({res['duplicate_lines']} already on file)" if res["duplicate_lines"] else "")
                + f": {res['matched']} check(s) confirmed cleared, {res['exceptions']} to look at.")


def _reconcile_tab() -> None:
    first, last = statement_coverage()
    if last is None:
        st.info("No bank statement imported yet. Use the Import tab.")
        return
    waiting = not_yet_cleared()
    exc = [e for e in exceptions() if not e["reviewed"]]
    serious = [e for e in exc if e["match_status"] != "not_in_system"]
    confirmed = q("SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS t FROM checks_issued WHERE bank_cleared_date IS NOT NULL")
    m1, m2, m3 = st.columns(3)
    m1.metric("Confirmed cleared by the bank", f"{int(confirmed['n'][0])}", php(confirmed["t"][0]), delta_color="off", delta_arrow="off", border=True)
    m2.metric("Not yet cleared (past their date)", f"{len(waiting)}", php(sum(w["amount"] for w in waiting)), delta_color="off", delta_arrow="off",
              border=True, help=f"Our numbered checks dated on or before {last:%b %d, %Y} (the last imported bank date) that the bank has not cleared.")
    m3.metric("Need a look", f"{len(serious)}", border=True)
    st.caption(f"Bank lines on file: {first:%b %d, %Y} to {last:%b %d, %Y}.")

    section("Need a look")
    if not serious:
        st.caption("Nothing — every check the bank cleared matches ours.")
    else:
        df = pd.DataFrame(serious)
        df["what"] = df["match_status"].map(EXCEPTION_LABELS)
        st.dataframe(df.assign(posting_date=pd.to_datetime(df["posting_date"]))[
            ["what", "posting_date", "check_number", "description", "debit", "credit", "our_amount", "our_status", "creditor_name"]],
            hide_index=True, column_config={"what": "What", "posting_date": date_col("Bank date"), "check_number": "Check #", "description": "Bank description",
                                            "debit": money_col("Out"), "credit": money_col("In"), "our_amount": money_col("Our amount"),
                                            "our_status": "Our status", "creditor_name": "Payee"})
        opts = {e["txn_id"]: f"{e['posting_date']} · {EXCEPTION_LABELS[e['match_status']]} · {e['check_number'] or e['description']}" for e in serious}
        r1, r2, r3 = st.columns([3, 3, 1.4], vertical_alignment="bottom")
        pick = r1.selectbox("Mark as looked at", list(opts), format_func=opts.get, key="bank_review_pick")
        note = r2.text_input("Note (optional)", key="bank_review_note", placeholder="e.g. bank error, corrected with Metrobank")
        if r3.button("Done", icon=":material/check:", key="bank_review_btn", width="stretch"):
            mark_reviewed(int(pick), note)
            refresh(done="Marked as looked at.")
    older = [e for e in exc if e["match_status"] == "not_in_system"]
    if older:
        with st.expander(f"{len(older)} cleared check(s) not in the system"):
            st.caption("Usually checks written before this system was used (it starts Sep 20, 2026) — nothing to do. If a recent one appears here, "
                       "it may be a check that was never recorded.")
            st.dataframe(pd.DataFrame(older)[["posting_date", "check_number", "debit"]], hide_index=True,
                         column_config={"posting_date": "Bank date", "check_number": "Check #", "debit": money_col("Amount")})

    section("Checks not yet cleared", help="Checks we issued, dated on or before the last imported bank date, that the bank has not cleared yet.")
    if not waiting:
        st.caption("None — every check dated within the imported period has cleared.")
    else:
        w = pd.DataFrame(waiting)
        st.dataframe(w.assign(check_date=pd.to_datetime(w["check_date"]))[["check_number", "check_date", "days_waiting", "creditor_name", "reference", "amount"]],
                     hide_index=True, column_config={"check_number": "Check #", "check_date": date_col("Check date"), "days_waiting": "Days waiting",
                                                     "creditor_name": "Payee", "reference": "Batch / loan note", "amount": money_col("Amount")})


def render() -> None:
    page_header("Bank and cash", "Drop the MBOS bank statement here: the app matches your checks and shows only what needs a look.")
    li = last_import()
    if li:
        st.caption(f"Last statement imported {str(li['imported_at'])[:16]} · covers {li['period_from']} to {li['period_to']}.")
    t_imp, t_rec = st.tabs([":material/upload_file: Import a statement", ":material/rule: Reconciliation"])
    with t_imp:
        _import_tab()
    with t_rec:
        _reconcile_tab()
