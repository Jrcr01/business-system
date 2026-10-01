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
from engines.clock import manila_now, manila_today


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


def refresh(done: str | None = None) -> None:
    """Reload the data and redraw the page. `done` = a short 'Saved ...' message shown as a toast after the redraw (a plain
    st.success() right before a refresh is wiped by the redraw before anyone can read it)."""
    if done:
        flash(done)
    st.cache_data.clear()
    st.rerun()


# ---- Look and feel building blocks (2026-09-27, per user: "a professional and organized UI"). Every screen uses these so
# headings, status colours, money and dates look the same everywhere. ----

def flash(msg: str, icon: str = ":material/check_circle:") -> None:
    """Queue a toast that survives the next st.rerun() (shown by show_flashes(), called once near the top of app.py)."""
    st.session_state.setdefault("_flash", []).append((msg, icon))


def show_flashes() -> None:
    for msg, icon in st.session_state.pop("_flash", []):
        st.toast(msg, icon=icon)


def page_header(title: str, subtitle: str | None = None) -> None:
    """The same heading on every page: the page's menu name with a brand-blue rule, and one short line under it."""
    st.header(title, divider="blue")
    if subtitle:
        st.caption(subtitle)


def section(title: str, help: str | None = None) -> None:
    """A section heading inside a page (one style everywhere, replacing ##### / #### / bold-text headings)."""
    st.subheader(title, help=help)


# Status colours: Draft grey, Parked orange, Issued blue, Paid/Cleared/Settled green, Cancelled/Voided red.
STATUS_STYLE = {
    "draft": ("gray", ":material/edit_note:"), "parked": ("orange", ":material/local_parking:"),
    "issued": ("blue", ":material/send:"), "exported": ("blue", ":material/upload_file:"), "printed": ("blue", ":material/print:"),
    "active": ("blue", ":material/radio_button_checked:"), "open": ("blue", ":material/radio_button_checked:"),
    "outstanding": ("orange", ":material/schedule:"), "unpaid": ("orange", ":material/schedule:"), "partly covered": ("orange", ":material/timelapse:"),
    "paid": ("green", ":material/check_circle:"), "cleared": ("green", ":material/check_circle:"), "settled": ("green", ":material/check_circle:"),
    "locked": ("gray", ":material/lock:"), "unlocked": ("gray", ":material/lock_open:"),
    "cancelled": ("red", ":material/cancel:"), "voided": ("red", ":material/block:"), "replaced": ("gray", ":material/swap_horiz:"),
    "stale": ("red", ":material/warning:"), "overdue": ("red", ":material/warning:"),
    "needs reprint": ("orange", ":material/print:"), "reconciled": ("green", ":material/task_alt:"),
}


def status_badge(status: str, container=None) -> None:
    """A coloured status pill (st.badge) -- same colour for the same status on every screen."""
    color, icon = STATUS_STYLE.get(str(status).strip().lower(), ("gray", None))
    (container or st).badge(str(status), color=color, icon=icon)


def status_text(status: str) -> str:
    """The same coloured pill as status_badge(), for use inside markdown text (st.markdown / captions)."""
    color, _icon = STATUS_STYLE.get(str(status).strip().lower(), ("gray", None))
    return f":{color}-badge[{status}]"


MONEY_FORMAT = "₱%,.2f"       # tables: ₱1,234,567.89 -- the same as php() everywhere else


def money_col(label: str, **kwargs):
    return st.column_config.NumberColumn(label, format=MONEY_FORMAT, **kwargs)


def date_col(label: str, **kwargs):
    return st.column_config.DateColumn(label, format="MMM DD, YYYY", **kwargs)


def table_config(df: pd.DataFrame, labels: dict | None = None, kinds: dict | None = None) -> dict:
    """column_config for st.dataframe from the shared column list (engines/export_service.py): friendly names, ₱ money, MMM DD, YYYY dates."""
    from engines.export_service import kind_of, label

    cfg = {}
    for c in df.columns:
        name, k = label(c, labels), kind_of(c, kinds)
        cfg[c] = (money_col(name) if k == "money" else date_col(name) if k == "date"
                  else st.column_config.NumberColumn(name, format="%,.0f" if k == "count" else "%,.2f") if k in ("number", "count")
                  else st.column_config.DatetimeColumn(name, format="MMM DD, YYYY h:mm A") if k == "datetime" else name)
    return cfg


def show_table(df: pd.DataFrame, labels: dict | None = None, hide: list | None = None, kinds: dict | None = None, **kwargs) -> None:
    """A report table with friendly column names and the one money / date format (2026-09-28, Reports review). `hide`: columns kept in
    the data (and exports) but not shown."""
    from engines.export_service import with_real_dates

    view = with_real_dates(df.drop(columns=[c for c in (hide or []) if c in df.columns]), kinds)
    st.dataframe(view, hide_index=True, column_config=table_config(view, labels, kinds), **kwargs)


def export_buttons(df: pd.DataFrame | dict, name: str, key: str, labels: dict | None = None, container=None, what: str = "",
                   kinds: dict | None = None) -> None:
    """CSV + Excel download buttons side by side, with friendly column names (engines/export_service.py). `df` may be a dict of tables:
    the CSV then holds the first one and the Excel file has one sheet each."""
    from engines.export_service import csv_bytes, excel_bytes

    first = next(iter(df.values())) if isinstance(df, dict) else df
    c1, c2 = (container or st).columns(2)
    suffix = f" {what}" if what else ""
    # the files are built only when a button is clicked (a callable), not on every redraw
    c1.download_button(f"Download{suffix} CSV", data=lambda: csv_bytes(first, labels, kinds), file_name=f"{name}.csv", mime="text/csv", key=f"{key}_csv",
                       icon=":material/download:", width="stretch")
    c2.download_button(f"Download{suffix} Excel", data=lambda: excel_bytes(df, labels, kinds=kinds), file_name=f"{name}.xlsx", key=f"{key}_xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", icon=":material/table_view:", width="stretch")


# Browse / New switches on Order Statement, Lenders and Bank loans: plain values (the search jumps set them), icons on screen.
OS_BROWSE, OS_NEW = "Browse batches", "New batch"
LN_BROWSE, LN_NEW = "Browse loan notes", "New loan note"
BL_BROWSE, BL_NEW = "Browse bank loans", "New bank loan"


def mode_label(option: str) -> str:
    return (":material/search: " if option.startswith("Browse") else ":material/add: ") + option


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
    full_range = (min(valid.min(), manila_today()), max(valid.max(), manila_today())) if not valid.empty else (manila_today(), manila_today())
    preset = container.segmented_control(label, options=["Today", "Last 7 days", "Last 30 days", "All time", "Custom"],
                                         default="All time", required=True, key=key)
    if preset == "Today":
        start, end = manila_today(), manila_today()
    elif preset == "Last 7 days":
        start, end = manila_today() - timedelta(days=6), manila_today()
    elif preset == "Last 30 days":
        start, end = manila_today() - timedelta(days=29), manila_today()
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
    section("Print a check")
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
    section("Assign a check number")
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
        st.caption("No Active booklet on file for this bank account — register one in Admin and audit, or type the number directly below.")
        manual_number = st.text_input("Check number (typed exactly as physically written)", key=f"{key_prefix}_assign_manual_number", help='Exactly as printed on the leaf, all digits — it is matched against the bank statement later.')
    else:
        b_options = {
            int(r["booklet_id"]): f"{r['booklet_code'] or ('Booklet #' + str(int(r['booklet_id'])))} ({r['start_number']}-{r['end_number']})"
            for _, r in booklets_df.iterrows()
        }
        booklet_id = st.selectbox(
            "Booklet", options=list(b_options.keys()), format_func=lambda bid: b_options[bid], key=f"{key_prefix}_assign_booklet",
            help='Pick the booklet the leaf comes from; only its unused leaves are offered.',
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
            manual_number = st.text_input("Check number (typed exactly as physically written)", key=f"{key_prefix}_assign_manual_number", help='Exactly as printed on the leaf, all digits — it is matched against the bank statement later.') or None

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
            seq_start = st.number_input("Start at leaf (first free one is pre-filled)", value=seq_default, step=1, format="%d", key=f"{key_prefix}_seq_start_{seq_bid}_{seq_default}", help='The first leaf number to use; used or spoiled leaves after it are skipped by themselves.')
            seq_plan, seq_skipped, seq_err = plan_sequential_numbers(int(seq_bid), int(seq_start), [int(x) for x in seq_batch_rows["check_id"]])
            if seq_err:
                st.error(seq_err)
            else:
                seq_leaf = dict(seq_plan)
                seq_view = seq_batch_rows[["check_id", "check_date", "amount"]].copy()
                seq_view.insert(0, "check_number", [str(seq_leaf[int(c)]) for c in seq_view["check_id"]])
                seq_view.insert(0, "no", range(1, len(seq_view) + 1))
                st.dataframe(seq_view.drop(columns="check_id"), hide_index=True, column_config={
                    "no": "#", "check_number": "Check number", "check_date": "Check date", "amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT)})
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


def _type_filter(creditor_type: str) -> tuple[str, tuple]:
    """'LESSOR' = the lenders flagged as lessors; 'LENDER' = the others (a lessor is not a lender on any picker)."""
    if creditor_type == "LESSOR":
        return "creditor_type = 'LENDER' AND is_lessor = 1", ()
    if creditor_type == "LENDER":
        return "creditor_type = 'LENDER' AND is_lessor = 0", ()
    if creditor_type == "LENDER_OR_LESSOR":                 # where a loan note or a lease can be made (Lenders page, handwritten checks)
        return "creditor_type = 'LENDER'", ()
    return "creditor_type = ?", (creditor_type,)


def creditor_options(creditor_type: str) -> dict:
    cond, params = _type_filter(creditor_type)
    df = q(
        f"SELECT creditor_id, creditor_name FROM creditors WHERE {cond} AND is_active = 1 ORDER BY creditor_name",
        params,
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
    cond, params = _type_filter(creditor_type)
    df = q(
        f"SELECT creditor_id, creditor_name FROM creditors WHERE {cond} ORDER BY creditor_name",
        params,
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


def history_span(date_series_list: list):
    """(earliest, latest) date across several columns of dates, or None when there is nothing to range over. Each column is read on its
    own: the columns can be written differently ("2026-09-19 14:27:19" for a batch's created time, "2026-08-20" for an invoice or check
    date) and pandas 3 reads a mixed lot with the FIRST entry's format, silently dropping the rest -- which collapsed the Supplier history's
    default range to the batch dates and hid every invoice and check (found 2026-09-29)."""
    spans = []
    for s in date_series_list:
        if s is None or len(s) == 0:
            continue
        parsed = pd.to_datetime(s, errors="coerce", format="mixed").dropna()
        if not parsed.empty:
            spans.append((parsed.min().date(), parsed.max().date()))
    if not spans:
        return None
    return min(a for a, _ in spans), max(b for _, b in spans)


def history_date_range(date_series_list: list, key_prefix: str):
    """Reports tab history drill-downs: a From/To date-range picker defaulting to the full span
    of whatever dates actually exist across every table for the currently-picked entity (so it
    starts unfiltered, not empty). Returns (date_from, date_to) as date objects, or (None, None)
    when there's no data yet to range over. key_prefix should embed the entity's id (e.g.
    f"...{sup_id}") so switching entities gets a fresh widget instead of carrying over a stale
    date range that may not even overlap the newly-picked entity's dates."""
    span = history_span(date_series_list)
    if span is None:
        return None, None
    min_d, max_d = span
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


@st.dialog("Edit this batch")
def confirm_unlock_dialog(statement_id: int, statement_ref: str) -> None:
    st.write(f"Reopen batch **{statement_ref}** to change an invoice or credit memo?")
    st.caption("It closes again by itself when you save and its checks still cover it. Voiding, replacing or correcting a check never needs this.")
    reason = st.text_input("Why? (kept in the audit log)", key=f"reopen_reason_{statement_id}")
    c1, c2 = st.columns(2)
    if c1.button("Yes, reopen", icon=":material/lock_open:", width="stretch", disabled=not reason.strip()):
        ok, msg = unlock_statement(statement_id, f"Batch reopened to edit: {reason.strip()}")
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


@st.dialog("Please confirm")
def confirm_change_dialog(summary: str, action_key: str, action, yes_label: str = "Yes, do it", warning: str = "") -> None:
    """The same second look as confirm_create_dialog, for changes to records that already exist (void a check, correct a printed check, delete rows,
    ...), with its own wording on the Yes button. Enter = Yes and Esc = Go back (app.py's keyboard script looks for a button starting with 'Yes' and one
    saying 'Go back'). action() does the real work and returns (ok, message); it may also st.rerun() itself (refresh()). A double-clicked Yes is ignored."""
    st.markdown(summary)
    if warning:
        st.warning(warning, icon=":material/warning:")
    c1, c2 = st.columns(2)
    if c1.button(f"{yes_label}  [Enter]", icon=":material/check:", width="stretch", key=f"{action_key}_yes"):
        if guard_duplicate_submit(action_key + "_yes", summary):
            ok, msg = action()
            if ok:
                st.cache_data.clear()
                st.rerun()
            else:
                st.error(msg)
    if c2.button("Go back  [Esc]", width="stretch", key=f"{action_key}_back"):
        st.rerun()


def grid_risk_lines(original: pd.DataFrame, edited: pd.DataFrame, id_col: str, label, money_cols: list[str] | None = None) -> tuple[list[str], list[str]]:
    """What a grid Save would do that is hard to take back: (rows removed, money values changed on existing rows), as plain lines for a pop-up.
    `label(row)` names a row; `money_cols` are the columns whose change deserves a second look (amounts, rates). New rows and edits to other columns
    are not listed -- typing in new records stays fast. Both lists empty = nothing risky, save straight away."""
    removed, changed = [], []
    if original is None or original.empty:
        return removed, changed
    new_ids = {int(v) for v in edited[id_col].dropna()} if id_col in edited.columns and not edited.empty else set()
    edited_by_id = edited.dropna(subset=[id_col]).assign(**{id_col: lambda d: d[id_col].astype(int)}).set_index(id_col) if new_ids else None
    for _, row in original.iterrows():
        rid = int(row[id_col])
        if rid not in new_ids:
            removed.append(f"- **{label(row)}**")
            continue
        now = edited_by_id.loc[rid]
        for col in money_cols or []:
            try:
                old_v, new_v = float(row[col] or 0), float(now[col] or 0)
            except (TypeError, ValueError):
                continue
            if abs(old_v - new_v) > 0.001:
                changed.append(f"- **{label(row)}** — {col.replace('_', ' ')}: {old_v:,.2f} → **{new_v:,.2f}**")
    return removed, changed


def grid_risk_summary(what: str, removed: list[str], changed: list[str]) -> str:
    parts = []
    if removed:
        parts.append(f"**{len(removed)} {what} will be deleted** (they were removed from the table):\n" + "\n".join(removed))
    if changed:
        parts.append(f"**{len(changed)} amount(s) will change:**\n" + "\n".join(changed))
    return "\n\n".join(parts) + "\n\nSave these changes?"


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
    return manila_now()


WHATS_NEW_PATH = "WHATS_NEW.md"


def _read_whats_new() -> str:
    """Full changelog text, HTML-comment header stripped -- backs the permanent Changelog
    section in Admin and audit. Missing file (e.g. a fresh checkout without it yet) degrades to a
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


# Pages of the sidebar menu, by id (filled by app.py on every run, before anything can jump).
PAGES: dict = {}


def _jump(page_id: str, **state) -> None:
    """Open another page with some state pre-set (which batch / loan is open, which sub-tab). The state is set before the
    target page draws its widgets, so plain assignment works. Switching to a page restarts the script there."""
    if page_id not in PAGES:                      # a page this role does not have (the menu is cut down to the role)
        return
    for k, v in state.items():
        st.session_state[k] = v
    st.switch_page(PAGES[page_id])


def go_to(page_id: str, **state) -> None:
    """For buttons inside a page: open another page (or the same one) with some state pre-set -- used by the Payment run
    and Add data pages. Uses the same path as the search pop-up, which applies the state before any page draws."""
    st.session_state["_global_jump"] = {"page": page_id, "state": state}
    st.rerun()


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
                st.session_state["_global_jump"] = {"page": r["page"], "state": r["state"]}
                st.rerun()
