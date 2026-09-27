"""Screen helpers shared by the views (moved out of app.py unchanged, 2026-09-27): the cached query q(), refresh(), the
duplicate-submit guard, confirmation dialogs, pickers, the check-number and print sections, the search pop-up, and a few
screen settings. app.py imports every name back, so app.<name> keeps working."""
from __future__ import annotations

import base64
import hashlib
import os
import pandas as pd
import re
import streamlit as st
import streamlit.components.v1 as components
import time

from datetime import date
from datetime import datetime
from datetime import timedelta
from engines.bank_loan_service import cancel_bank_loan
from engines.check_rules import assign_check_number
from engines.check_rules import assign_check_numbers_from_booklet
from engines.check_rules import cancel_order_statement
from engines.check_rules import get_available_booklet_numbers
from engines.check_rules import get_booklets
from engines.check_rules import pick_number_source
from engines.check_rules import plan_sequential_numbers
from engines.check_rules import print_check_pdf
from engines.check_rules import retract_check_number
from engines.common import PRIMARY_BANK_ACCOUNT_ID
from engines.common import has_value
from engines.common import php
from engines.db_engine import df_query
from engines.global_search_service import search_everything
from engines.loan_note_rules import cancel_loan_note
from engines.loan_note_rules import delete_loan_note
from engines.statement_rules import unlock_statement


try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    PdfReader = PdfWriter = None


# 2026-09-17, per user request: checks are now printed outside this system entirely (Chrysanth's
# batch-print feature, via the Excel export on the Checks & checkbook tab) instead of this app's
# own PDF generation. Flip back to True to bring the in-app "Print a check"/"Print these checks"
# buttons back -- the underlying PDF-generation code (render_check_print_section(),
# generate_check_run_pdf(), etc.) is untouched, just not rendered while this is False.
PRINT_CHECKS_IN_APP = False


@st.cache_data(ttl="15s")
def q(sql: str, params: tuple = ()) -> pd.DataFrame:
    return df_query(sql, params)


def refresh() -> None:
    st.cache_data.clear()
    st.rerun()


def guard_duplicate_submit(action_key: str, *values, cooldown_seconds: float = 3.0) -> bool:
    """True if this submission should actually proceed. Streamlit has no built-in re-submit
    protection — a fast double-click sends two separate script reruns before the first one's
    response can visually disable anything, so a create/insert action fires twice with identical
    inputs (the real bug: a rapid double-click on "Add creditor" created two identical creditor
    rows). Fingerprints the submitted values themselves rather than counting clicks, and silently
    swallows a second submission whose fingerprint matches the immediately-prior one within
    cooldown_seconds — a genuine second submission with different values always goes through."""
    sig = hashlib.sha256(repr(values).encode("utf-8", "ignore")).hexdigest()
    state_key = f"_dupe_guard::{action_key}"
    now = time.monotonic()
    last = st.session_state.get(state_key)
    st.session_state[state_key] = {"sig": sig, "ts": now}
    return not (last is not None and last["sig"] == sig and (now - last["ts"]) < cooldown_seconds)


@st.dialog("Possible double recording")
def duplicate_warning_dialog(key: str, fp: str, warnings: list) -> None:
    st.warning("This looks like it may already be on file:")
    for w in warnings:
        st.markdown(f"- {w}")
    st.markdown("**Do you still want to continue?**")
    c1, c2 = st.columns(2)
    if c1.button("Yes, continue anyway  [Enter]", icon=":material/check:", width="stretch", key=f"{key}_dup_yes"):
        st.session_state[f"_dup_ok::{key}"] = {"fp": fp, "ts": time.monotonic()}
        st.session_state[f"_dup_resume::{key}"] = time.monotonic()
        st.rerun()
    if c2.button("No, go back  [Esc]", width="stretch", key=f"{key}_dup_no"):
        st.rerun()


def dup_resume(key: str) -> bool:
    """True on the rerun right after 'Yes, continue anyway' -- lets the original button's action run
    again without the user having to click it a second time."""
    ts = st.session_state.pop(f"_dup_resume::{key}", None)
    return ts is not None and time.monotonic() - ts < 120


def dup_needs_confirm(key: str, warnings: list) -> bool:
    """True if the action must NOT run yet: warnings exist and the user hasn't said to continue
    (the pop-up is opened here). Once they confirm, the same warnings pass through once."""
    if not warnings:
        return False
    fp = hashlib.sha256(repr(warnings).encode("utf-8", "ignore")).hexdigest()
    ok = st.session_state.get(f"_dup_ok::{key}")
    if ok and ok["fp"] == fp and time.monotonic() - ok["ts"] < 120:
        st.session_state.pop(f"_dup_ok::{key}", None)
        return False
    duplicate_warning_dialog(key, fp, warnings)
    return True


def dup_note(warnings: list) -> str:
    """Warning block to append to a 'Please confirm before saving' summary."""
    nl = chr(10)
    return "" if not warnings else nl + nl + ":warning: **Possible double recording:**" + nl + nl.join(f"- {w}" for w in warnings) + nl + nl + "Save it anyway?"


def date_preset_filter(container, df: pd.DataFrame, column: str, label: str, key: str) -> pd.DataFrame:
    """Today / Last 7 days / Last 30 days / All time / Custom picker on a datetime-text column of `df`; returns the matching rows.
    Used for the 'Last modified' filter of the order statement, loan note and bank loan browsers."""
    days = pd.to_datetime(df[column], errors="coerce").dt.date if not df.empty else pd.Series([], dtype=object)
    valid = days.dropna()
    full_range = (min(valid.min(), date.today()), max(valid.max(), date.today())) if not valid.empty else (date.today(), date.today())
    preset = container.segmented_control(label, options=["Today", "Last 7 days", "Last 30 days", "All time", "Custom"],
                                         default="All time", required=True, key=key)
    if preset == "Today":
        start, end = date.today(), date.today()
    elif preset == "Last 7 days":
        start, end = date.today() - timedelta(days=6), date.today()
    elif preset == "Last 30 days":
        start, end = date.today() - timedelta(days=29), date.today()
    elif preset == "Custom":
        picked = container.date_input("Custom range", value=full_range, key=key + "_range")
        if not (isinstance(picked, (tuple, list)) and len(picked) == 2):
            return df
        start, end = picked
    else:
        return df
    if preset != "Custom":
        container.caption(f"{start:%Y-%m-%d} to {end:%Y-%m-%d}")
    return df[(days >= start) & (days <= end)]


def default_index(options, selected_id) -> int:
    """Index of selected_id within options (a pandas Series/list of ids), for defaulting a
    selectbox to whatever batch is currently open in the Order Statement browser. Falls back
    to 0 (first option) if there's no active selection or it isn't in this particular list."""
    if selected_id is None:
        return 0
    try:
        return list(options).index(selected_id)
    except ValueError:
        return 0


def batch_scoped_key(base: str) -> str:
    """A widget key that changes whenever the open batch changes, so Streamlit treats it as a
    brand-new widget instead of reusing stale session_state from a previously-open batch — a
    plain fixed key would otherwise ignore index= after its first render (Streamlit widgets keep
    their session_state value across reruns regardless of a changed default)."""
    return f"{base}__{st.session_state.get('selected_statement_id')}"


def pdf_preview_and_download(pdf_bytes: bytes, file_name: str, key: str, label: str = "PDF") -> None:
    """Opens the PDF in a new browser tab as soon as it's ready — no second click needed after the
    Generate button — plus the usual download button below it. Built via components.v1.html (not
    st.markdown's unsafe_allow_html, which strips <script> tags so it can't run JS) that turns the
    PDF bytes into a Blob URL and calls window.open() — a popup, not a top-level navigation, so it
    isn't subject to the block that made an <a href="data:..." target="_blank"> link silently do
    nothing (logged earlier this session). The open call fires immediately on mount; the visible
    button is a fallback for when a browser's popup blocker still catches the automatic one (the
    Generate click's "user activation" can expire during the server round-trip that produces this
    PDF, so the auto-open isn't 100% guaranteed — that's a browser security policy, not a bug)."""
    b64 = base64.b64encode(pdf_bytes).decode()
    safe_key = re.sub(r"\W", "_", key)
    dom_id = f"openpdf-{key}"
    components.html(
        f"""
        <button id="{dom_id}" style="width:100%;padding:0.5rem 1rem;border:1px solid rgba(49,51,63,0.2);
        border-radius:0.5rem;background:#fff;cursor:pointer;font-size:14px;font-family:inherit;">
          Open {label} in new tab (click if it didn't open automatically)
        </button>
        <script>
          function openPdf_{safe_key}() {{
            const byteChars = atob("{b64}");
            const byteNumbers = new Array(byteChars.length);
            for (let i = 0; i < byteChars.length; i++) {{
              byteNumbers[i] = byteChars.charCodeAt(i);
            }}
            const blob = new Blob([new Uint8Array(byteNumbers)], {{type: "application/pdf"}});
            return window.open(URL.createObjectURL(blob), "_blank");
          }}
          document.getElementById("{dom_id}").addEventListener("click", openPdf_{safe_key});
          openPdf_{safe_key}();
        </script>
        """,
        height=50,
    )
    st.download_button(f"Download {label}", data=pdf_bytes, file_name=file_name, mime="application/pdf", key=key)


def render_check_print_section(checks_df: pd.DataFrame, payee_name: str, key_prefix: str, bank_account_id: int = PRIMARY_BANK_ACCOUNT_ID) -> None:
    """Reusable 'print a check' block: pick any Active check (parked or already printed), generate
    its PDF, preview + download via pdf_preview_and_download(). Used identically by Order
    Statement and Lenders' Checks sub-tabs.

    Phase 6 Redux: printing is completely independent of the check number -- the number isn't
    even drawn on the PDF (it's pre-printed on the physical blank stock), so there's nothing to
    pick or assign here. Any parked check can be printed with no booklet/number involved at all;
    an already-printed check just reprints. Use 'Assign a check number' separately (below) to
    give a check its number, whenever that's actually known — before, during, or after this."""
    st.markdown("##### Print a check")
    active_df = checks_df[checks_df["status"] == "Active"] if not checks_df.empty else checks_df
    if active_df.empty:
        st.caption("No active check here to print.")
        return
    options = {
        int(r["check_id"]): f"{r['check_number'] or 'No number yet'} — {php(r['amount'])} — {r['check_date']}"
        + (" (already printed)" if has_value(r.get("printed_at")) else "")
        for _, r in active_df.iterrows()
    }
    picked = st.selectbox("Check to print", options=list(options.keys()), format_func=lambda cid: options[cid], key=f"{key_prefix}_print_pick")

    if st.button("Generate check PDF", icon=":material/picture_as_pdf:", key=f"{key_prefix}_print_btn"):
        ok, msg, pdf_bytes = print_check_pdf(int(picked))
        if not ok:
            st.error(msg)
        else:
            st.success(msg)
            pdf_preview_and_download(
                pdf_bytes, f"check_{int(picked)}.pdf",
                key=f"{key_prefix}_print_dl", label="Check PDF",
            )


def _render_retract_number_section(active_df: pd.DataFrame, key_prefix: str) -> None:
    """'Retract a wrongly assigned number': pick a numbered check, confirm, and its number (and booklet link) is removed."""
    numbered = active_df[active_df["check_number"].apply(has_value)] if not active_df.empty else active_df
    if numbered.empty:
        return
    with st.expander("Assigned the wrong number? Retract it"):
        st.caption("Removes the number from the check so you can assign the right one. If it came from a booklet, that leaf goes back to the booklet. "
                   "Only do this if the check was NOT physically written or printed with that number.")
        r_options = {int(r["check_id"]): f"{r['check_number']} — {php(r['amount'])} — {r['check_date']}" for _, r in numbered.iterrows()}
        r_pick = st.selectbox("Numbered check", options=list(r_options.keys()), format_func=lambda cid: r_options[cid], key=f"{key_prefix}_retract_pick")
        if st.button("Retract this number", icon=":material/undo:", key=f"{key_prefix}_retract_btn"):
            def _do_retract(check_id=int(r_pick)):
                ok, msg = retract_check_number(check_id)
                if ok:
                    st.toast(msg)
                return ok, msg
            confirm_create_dialog(f"Take the number off **{r_options[int(r_pick)]}**? The check goes back to having no number.", f"{key_prefix}_retract_check_number", _do_retract)


def render_assign_number_section(checks_df: pd.DataFrame, key_prefix: str, bank_account_id: int = PRIMARY_BANK_ACCOUNT_ID) -> None:
    """Reusable 'assign a check number' block: pick any Active check that doesn't have a number
    yet (regardless of whether it's been printed), pick a booklet, pick one of that booklet's
    still-available numbers from a dropdown, assign. Independent of printing -- see
    render_check_print_section().

    Phase 6 Redux v2 (2026-09-13): auto-sequencing is gone -- this single-operator, low-volume
    workflow doesn't need it, and it added real complexity (spoiled-number skipping, exhaustion
    tracking) for no real benefit over just picking a number. The dropdown already lists every
    open leaf, including ones below the booklet's "suggested next" cursor, so manual selection
    covers backfill too; a plain typed-in fallback remains for a check with no booklet on file."""
    st.markdown("##### Assign a check number")
    if st.session_state.pop(f"_clear_{key_prefix}_assign_manual_number", False):
        st.session_state[f"{key_prefix}_assign_manual_number"] = ""   # a typed number never lingers into the next assignment
    active_df = checks_df[checks_df["status"] == "Active"] if not checks_df.empty else checks_df
    unnumbered_df = active_df[~active_df["check_number"].apply(has_value)] if not active_df.empty else active_df
    if unnumbered_df.empty:
        st.caption("Every active check here already has a number.")
        _render_retract_number_section(active_df, key_prefix)
        return
    options = {
        int(r["check_id"]): f"{php(r['amount'])} — {r['check_date']}" + (" (already printed)" if has_value(r.get("printed_at")) else " (not yet printed)")
        for _, r in unnumbered_df.iterrows()
    }
    picked = st.selectbox("Check", options=list(options.keys()), format_func=lambda cid: options[cid], key=f"{key_prefix}_assign_pick")

    booklets_df = get_booklets(bank_account_id)
    booklet_id, chosen_number, manual_number = None, None, None
    if booklets_df.empty:
        st.caption("No Active booklet on file for this bank account — register one in Admin & audit, or type the number directly below.")
        manual_number = st.text_input("Check number (typed exactly as physically written)", key=f"{key_prefix}_assign_manual_number")
    else:
        b_options = {
            int(r["booklet_id"]): f"{r['booklet_code'] or ('Booklet #' + str(int(r['booklet_id'])))} ({r['start_number']}-{r['end_number']})"
            for _, r in booklets_df.iterrows()
        }
        booklet_id = st.selectbox(
            "Booklet", options=list(b_options.keys()), format_func=lambda bid: b_options[bid], key=f"{key_prefix}_assign_booklet",
        )
        booklet_row = booklets_df.set_index("booklet_id").loc[int(booklet_id)]
        available_numbers = get_available_booklet_numbers(bank_account_id, booklet_row)
        if not available_numbers:
            st.caption("This booklet has no available leaves left — pick another booklet, or type a number directly below.")
        else:
            chosen_number = st.selectbox(
                "Check number", options=available_numbers,
                format_func=str,
                key=f"{key_prefix}_assign_number",
            )
            st.caption(f"{len(available_numbers)} leaf/leaves still available in this booklet.")
        with st.expander("No booklet, or need to type a number directly?"):
            st.caption("If you type a number here, it is used instead of the booklet number above.")
            manual_number = st.text_input("Check number (typed exactly as physically written)", key=f"{key_prefix}_assign_manual_number") or None

    if st.button("Assign check number", icon=":material/tag:", key=f"{key_prefix}_assign_btn"):
        source, value = pick_number_source(booklet_id, chosen_number, manual_number)
        if source == "none":
            st.error("Pick a booklet number, or type the check number directly.")
        else:
            if source == "manual":
                what = f"the **typed number {value}** (no booklet)"
            else:
                what = f"**{value[1]}** from **{b_options[value[0]]}**"
            summary = f"Give the check {options[int(picked)]} the number {what}?"

            def _do_assign(check_id=int(picked), src=source, val=value):
                ok, msg = assign_check_number(check_id, *(val[0], val[1], None) if src == "booklet" else (None, None, val))
                if ok:
                    st.toast(msg)
                    st.session_state[f"_clear_{key_prefix}_assign_manual_number"] = True
                return ok, msg
            confirm_create_dialog(summary, f"{key_prefix}_assign_check_number", _do_assign)

    # Whole batch at once (2026-09-27): the same booklet leaves, but for every unnumbered check of THIS order statement / loan note, previewed
    # and confirmed once -- the check-per-batch discipline stays, without one confirmation per check.
    if len(unnumbered_df) >= 2 and not booklets_df.empty:
        with st.expander(f"Assign all {len(unnumbered_df)} unnumbered checks of this batch in sequence"):
            st.caption("The next free leaves of the booklet go to this batch's checks in the order they were created (the order Chrysanth prints them). "
                       "Leaves already used or spoiled are skipped. Review the list, then one confirmation assigns them all — all or nothing, every leaf re-checked when saving.")
            seq_batch_rows = unnumbered_df.sort_values("check_id")
            seq_bid = st.selectbox("Booklet", options=list(b_options.keys()), format_func=lambda bid: b_options[bid], key=f"{key_prefix}_seq_booklet")
            seq_free = get_available_booklet_numbers(bank_account_id, booklets_df.set_index("booklet_id").loc[int(seq_bid)])
            seq_default = int(seq_free[0]) if seq_free else int(booklets_df.set_index("booklet_id").loc[int(seq_bid)]["start_number"])
            seq_start = st.number_input("Start at leaf (first free one is pre-filled)", value=seq_default, step=1, format="%d", key=f"{key_prefix}_seq_start_{seq_bid}_{seq_default}")
            seq_plan, seq_skipped, seq_err = plan_sequential_numbers(int(seq_bid), int(seq_start), [int(x) for x in seq_batch_rows["check_id"]])
            if seq_err:
                st.error(seq_err)
            else:
                seq_leaf = dict(seq_plan)
                seq_view = seq_batch_rows[["check_id", "check_date", "amount"]].copy()
                seq_view.insert(0, "check_number", [str(seq_leaf[int(c)]) for c in seq_view["check_id"]])
                seq_view.insert(0, "no", range(1, len(seq_view) + 1))
                st.dataframe(seq_view.drop(columns="check_id"), hide_index=True, column_config={
                    "no": "#", "check_number": "Check number", "check_date": "Check date", "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f")})
                if seq_skipped:
                    st.caption(f"Skipping {len(seq_skipped)} leaf/leaves already used or spoiled: {', '.join(str(n) for n in seq_skipped[:12])}{' …' if len(seq_skipped) > 12 else ''}")
                seq_first, seq_last = seq_plan[0][1], seq_plan[-1][1]
                if st.button(f"Assign {len(seq_plan)} numbers ({seq_first} – {seq_last})", icon=":material/format_list_numbered:", key=f"{key_prefix}_seq_btn", type="primary"):
                    seq_summary = (
                        f"Assign **{len(seq_plan)}** check numbers **{seq_first}** to **{seq_last}** from **{b_options[int(seq_bid)]}** to this batch's checks "
                        f"(first: {seq_batch_rows.iloc[0]['check_date']} {php(seq_batch_rows.iloc[0]['amount'])}; last: {seq_batch_rows.iloc[-1]['check_date']} {php(seq_batch_rows.iloc[-1]['amount'])}; "
                        f"total {php(float(seq_batch_rows['amount'].sum()))})?" + (f" {len(seq_skipped)} used/spoiled leaf/leaves are skipped." if seq_skipped else ""))

                    def _do_seq_assign(plan=list(seq_plan), bid=int(seq_bid)):
                        ok, msg, ids = assign_check_numbers_from_booklet(plan, bid)
                        if ok:
                            st.toast(msg)
                        return ok, msg
                    confirm_create_dialog(seq_summary, f"{key_prefix}_seq_assign", _do_seq_assign)
    _render_retract_number_section(active_df, key_prefix)


def creditor_options(creditor_type: str) -> dict:
    df = q(
        "SELECT creditor_id, creditor_name FROM creditors WHERE creditor_type = ? AND is_active = 1 ORDER BY creditor_name",
        (creditor_type,),
    )
    return dict(zip(df.creditor_id, df.creditor_name))


def selectbox_creditor(label: str, creditor_type: str, key: str):
    options = creditor_options(creditor_type)
    if not options:
        st.warning(f"No {creditor_type.replace('_', ' ').title()} creditors on file yet. Add one below first.")
        return None
    return st.selectbox(label, options=list(options), format_func=lambda cid: options[cid], key=key)


def selectbox_creditor_label(creditor_type: str, creditor_id: int) -> str:
    return creditor_options_all(creditor_type).get(creditor_id, str(creditor_id))


def creditor_options_all(creditor_type: str) -> dict:
    """Same as creditor_options() but including inactive creditors -- a history/report picker
    still needs to find a supplier or lender that's since been deactivated."""
    df = q(
        "SELECT creditor_id, creditor_name FROM creditors WHERE creditor_type = ? ORDER BY creditor_name",
        (creditor_type,),
    )
    return dict(zip(df.creditor_id, df.creditor_name))


def bank_loan_options() -> dict:
    df = q(
        "SELECT bl.bank_loan_id, bl.loan_ref, c.creditor_name FROM bank_loans bl "
        "JOIN creditors c ON c.creditor_id = bl.creditor_id ORDER BY bl.loan_ref"
    )
    return {row.bank_loan_id: f"{row.loan_ref} — {row.creditor_name}" for row in df.itertuples()}


def credit_card_options() -> dict:
    df = q("SELECT card_id, card_name, card_number_last4 FROM credit_cards ORDER BY card_name")
    return {row.card_id: f"{row.card_name} - {row.card_number_last4}" for row in df.itertuples()}


def cardholder_options_with_cards() -> dict:
    """Cardholders that have at least one card on file, for the Reports tab's two-level
    Cardholder -> Card picker ("1 card holder = many cards," same grouping the Credit Cards
    tab's own "Add statement" flow already uses). `None` is included as a synthetic
    "Unassigned" option whenever a card exists with no cardholder_id set."""
    df = q(
        "SELECT DISTINCT ch.cardholder_id, ch.name FROM credit_cards cc "
        "JOIN cardholders ch ON cc.cardholder_id = ch.cardholder_id ORDER BY ch.name"
    )
    options = dict(zip(df.cardholder_id, df.name))
    unassigned = q("SELECT COUNT(*) AS n FROM credit_cards WHERE cardholder_id IS NULL")["n"].iloc[0]
    if unassigned:
        options[None] = "Unassigned"
    return options


def credit_card_options_for_holder(cardholder_id) -> dict:
    if cardholder_id is None:
        df = q("SELECT card_id, card_name, card_number_last4 FROM credit_cards WHERE cardholder_id IS NULL ORDER BY card_name")
    else:
        df = q("SELECT card_id, card_name, card_number_last4 FROM credit_cards WHERE cardholder_id = ? ORDER BY card_name", (cardholder_id,))
    return {row.card_id: f"{row.card_name} - {row.card_number_last4}" for row in df.itertuples()}


def history_date_range(date_series_list: list, key_prefix: str):
    """Reports tab history drill-downs: a From/To date-range picker defaulting to the full span
    of whatever dates actually exist across every table for the currently-picked entity (so it
    starts unfiltered, not empty). Returns (date_from, date_to) as date objects, or (None, None)
    when there's no data yet to range over. key_prefix should embed the entity's id (e.g.
    f"...{sup_id}") so switching entities gets a fresh widget instead of carrying over a stale
    date range that may not even overlap the newly-picked entity's dates."""
    non_empty = [s for s in date_series_list if s is not None and not s.empty]
    if not non_empty:
        return None, None
    parsed = pd.to_datetime(pd.concat(non_empty), errors="coerce").dropna()
    if parsed.empty:
        return None, None
    min_d, max_d = parsed.min().date(), parsed.max().date()
    f1, f2 = st.columns(2)
    with f1:
        date_from = st.date_input("From", value=min_d, min_value=min_d, max_value=max_d, key=f"{key_prefix}_from")
    with f2:
        date_to = st.date_input("To", value=max_d, min_value=min_d, max_value=max_d, key=f"{key_prefix}_to")
    return date_from, date_to


def filter_by_status(df: pd.DataFrame, col: str, key: str, label: str = "Status") -> pd.DataFrame:
    """A status multiselect that narrows what's shown/exported for one table — defaults to every
    status present (so it starts unfiltered), independent of the summary metrics above it, which
    always exclude cancelled/replaced regardless of this filter (see Reports tab callers)."""
    if df.empty:
        return df
    options = sorted(df[col].dropna().unique())
    if not options:
        return df
    picked = st.multiselect(label, options=options, default=options, key=key)
    return df[df[col].isin(picked)] if picked else df.iloc[0:0]


@st.dialog("Confirm unlock")
def confirm_unlock_dialog(statement_id: int, statement_ref: str) -> None:
    st.write(f"Are you sure you want to unlock batch **{statement_ref}**?")
    st.caption("Unlocking allows corrections and check replacements against this batch.")
    c1, c2 = st.columns(2)
    if c1.button("Yes, unlock", icon=":material/lock_open:", width="stretch"):
        ok, msg = unlock_statement(statement_id)
        if ok:
            st.cache_data.clear()
            st.rerun()
        else:
            st.error(msg)
    if c2.button("Cancel", width="stretch"):
        st.rerun()


@st.dialog("Confirm cancel batch")
def confirm_cancel_dialog(statement_id: int, statement_ref: str, active_check_count: int, active_check_total: float, invoice_count: int) -> None:
    st.write(f"Are you sure you want to cancel batch **{statement_ref}**?")
    if active_check_count:
        st.warning(f"This will cancel {active_check_count} active check(s) totaling {php(active_check_total)}, restoring that amount to the bank balance.")
    if invoice_count:
        st.caption(f"{invoice_count} invoice(s) in this batch will be marked Cancelled — kept on record, excluded from AP totals, not deleted.")
    st.caption("The batch locks permanently — this can't be undone from the UI.")
    c1, c2 = st.columns(2)
    if c1.button("Yes, cancel batch", icon=":material/cancel:", width="stretch"):
        ok, msg = cancel_order_statement(statement_id)
        if ok:
            st.cache_data.clear()
            st.rerun()
        else:
            st.error(msg)
    if c2.button("Go back", width="stretch"):
        st.rerun()


SUPPLIER_TERMS_OPTIONS = ["", "COD", "30", "60", "90", "120", "140", "150"]


@st.dialog("Please confirm before saving")
def confirm_create_dialog(summary: str, action_key: str, action) -> None:
    """Second look at what's about to be saved, in plain words with the amounts formatted -- used
    on the loan note / bank loan / card forms, where a mistyped number field otherwise saves
    silently (a stored 0.001 displays as PHP 0.00 afterwards, hiding the typo). action() does the
    real write and returns (ok, message); guard_duplicate_submit stops a double-clicked Confirm."""
    st.markdown(summary)
    c1, c2 = st.columns(2)
    if c1.button("Yes, save it  [Enter]", icon=":material/check:", width="stretch", key=f"{action_key}_confirm"):
        if guard_duplicate_submit(action_key + "_confirm", summary):
            ok, msg = action()
            if ok:
                st.cache_data.clear()
                st.rerun()
            else:
                st.error(msg)
    if c2.button("Go back and edit  [Esc]", width="stretch", key=f"{action_key}_back"):
        st.rerun()


@st.dialog("Confirm cancel loan note")
def confirm_cancel_loan_note_dialog(loan_note_id: int, note_ref: str, active_check_count: int, active_check_total: float) -> None:
    st.write(f"Are you sure you want to cancel loan note **{note_ref}**?")
    if active_check_count:
        st.warning(f"This will cancel {active_check_count} active check(s) totaling {php(active_check_total)}, restoring that amount to the bank balance.")
    st.caption("This can't be undone from the UI.")
    c1, c2 = st.columns(2)
    if c1.button("Yes, cancel loan note", icon=":material/cancel:", width="stretch"):
        ok, msg = cancel_loan_note(loan_note_id)
        if ok:
            st.cache_data.clear()
            st.rerun()
        else:
            st.error(msg)
    if c2.button("Go back", width="stretch"):
        st.rerun()


@st.dialog("Confirm cancel bank loan")
def confirm_cancel_bank_loan_dialog(bank_loan_id: int, loan_ref: str, installment_count: int) -> None:
    st.write(f"Are you sure you want to cancel bank loan **{loan_ref}**?")
    st.caption(f"Its {installment_count} repayment row(s) stay on record, but they stop counting as amounts due on the dashboard, reports and interest figures.")
    c1, c2 = st.columns(2)
    if c1.button("Yes, cancel bank loan", icon=":material/cancel:", width="stretch"):
        ok, msg = cancel_bank_loan(bank_loan_id)
        if ok:
            st.cache_data.clear()
            st.rerun()
        else:
            st.error(msg)
    if c2.button("Go back", width="stretch"):
        st.rerun()


@st.dialog("Confirm delete loan note")
def confirm_delete_loan_note_dialog(loan_note_id: int, note_ref: str) -> None:
    st.write(f"Are you sure you want to delete loan note **{note_ref}**?")
    st.caption("No checks are on record for this note, so this is a hard delete — the note and its schedule are removed entirely, not just marked Cancelled. This can't be undone.")
    c1, c2 = st.columns(2)
    if c1.button("Yes, delete loan note", icon=":material/delete_forever:", width="stretch"):
        ok, msg = delete_loan_note(loan_note_id)
        if ok:
            st.session_state["selected_loan_note_id"] = None
            st.cache_data.clear()
            st.rerun()
        else:
            st.error(msg)
    if c2.button("Go back", width="stretch"):
        st.rerun()


def get_blocked_dates_for_month(year: int, month: int) -> dict:
    """{date: {'label': str, 'kind': 'holiday'|'custom'}} for every greyed-out date in one
    calendar month — Philippine holidays (seeded, migration 024) and any date the user has
    marked as a custom no-issue day, both stored in calendar_blocked_dates."""
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    df = q("SELECT blocked_date, label, kind FROM calendar_blocked_dates WHERE blocked_date >= ? AND blocked_date < ?", (str(start), str(end)))
    return {datetime.strptime(r["blocked_date"], "%Y-%m-%d").date(): {"label": r["label"], "kind": r["kind"]} for _, r in df.iterrows()}


@st.cache_resource
def _get_app_start_time() -> datetime:
    """Computed once per process, cached for that process's whole lifetime (st.cache_resource,
    unlike st.cache_data, doesn't expire or get cleared by "Refresh data") -- so this only ever
    changes when the app is actually restarted, not on an ordinary page interaction."""
    return datetime.now()


WHATS_NEW_PATH = "WHATS_NEW.md"


def _read_whats_new() -> str:
    """Full changelog text, HTML-comment header stripped -- backs the permanent Changelog
    section in Admin & audit. Missing file (e.g. a fresh checkout without it yet) degrades to a
    plain message instead of crashing the page."""
    if not os.path.exists(WHATS_NEW_PATH):
        return "No changelog yet."
    with open(WHATS_NEW_PATH, encoding="utf-8") as f:
        text = f.read()
    return re.sub(r"<!--.*?-->\s*", "", text, count=1, flags=re.DOTALL).strip()


def _read_whats_new_latest_section() -> str | None:
    """Just the newest "## YYYY-MM-DD" section, for the one-time popup -- None if the file is
    missing or empty so the caller can just skip showing anything."""
    full = _read_whats_new()
    if full == "No changelog yet.":
        return None
    sections = re.split(r"(?=^## )", full, flags=re.MULTILINE)
    sections = [s.strip() for s in sections if s.strip().startswith("## ")]
    return sections[0] if sections else None


@st.cache_resource
def _whats_new_popup_tracker() -> dict:
    """A mutable object living for this process's whole lifetime (unlike st.session_state, which
    is per browser tab/session) -- so the "What's new" popup fires exactly once per actual
    restart, for whoever happens to load the page first, and never again until the next restart
    -- not once per new tab someone opens against an already-running process."""
    return {"shown": False}


def _jump(tab_label: str, **state) -> None:
    """Sets the session_state that drives the sidebar quick-search's "single match auto-jumps"
    behavior. Runs from inside the sidebar block, which executes before the tab/sub-tab widgets
    it targets are instantiated later in this same script pass -- so plain assignment here is
    enough (this is the "set session_state before the widget renders" case, not the "set after
    instantiation" case that needs the _os_mode_next-style staging trick elsewhere in this app)."""
    st.session_state["main_tab"] = tab_label
    for k, v in state.items():
        st.session_state[k] = v


@st.dialog("Search everything", width="large")
def global_search_dialog() -> None:
    st.caption("Results update as you type (at least 2 characters). Finds checks, invoices, order statements, loan notes, bank loans, creditors, credit cards and spoiled checks — by number, reference, name, TIN/contact or an exact amount. Click a result to open it.")
    term = st.text_input("Search", key="global_search_q", placeholder="e.g. 3992554313, 69055, Tire Asia, 81711.50 …", label_visibility="collapsed")
    if len((term or "").strip()) < 2:
        return
    groups = search_everything(term)
    if not groups:
        st.info(f"Nothing found for '{term}'.")
    for gi, g in enumerate(groups):
        more = f" — showing {len(g['rows'])} of {g['total']}" if g["total"] > len(g["rows"]) else ""
        st.markdown(f"**{g['title']}** ({g['total']}){more}")
        for ri, r in enumerate(g["rows"]):
            if st.button(r["label"], key=f"gs_{gi}_{ri}", width="stretch"):
                st.session_state["_global_jump"] = {"tab": r["tab"], "state": r["state"]}
                st.rerun()
