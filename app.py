"""AP Disbursement System — Streamlit UI.

Primary Bank Account: Metrobank Account 1 (Branch Code 399).
"""
import ast
import base64
import calendar
import hashlib
import io
import os
import re
import sqlite3
import time
from datetime import date, datetime, timedelta

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from engines.db_engine import df_query, promote_draft_statement
from engines.batch_statement_service import generate_batch_statement_pdf, generate_batch_statement_thermal_pdf, bulk_summary_rows, generate_bulk_summary_pdf
from engines.loan_note_service import generate_loan_note_pdf
from engines.loan_schedule_import_service import parse_loan_schedule_spreadsheet, get_template_csv as get_schedule_template_csv
from engines.bank_loan_service import (
    create_bank_loan,
    delete_bank_loan,
    cancel_bank_loan,
    add_bank_loan_installment,
    edit_bank_loan_installment,
    delete_bank_loan_installment,
    add_loan_beneficiary,
    edit_bank_loan_beneficiary,
)
from engines.bank_loan_import_service import parse_bank_loan_spreadsheet, get_template_csv as get_bank_loan_template_csv
from engines.bank_loan_report_service import generate_bank_loan_pdf
from engines.invoice_ocr_service import extract_invoice_fields
from engines.bank_names import expand_bank_name
from engines.cc_rules import cc_owed_case, superseded_sql
from engines.schema_check import pending_schema_updates
from engines.name_case import smart_proper_case
from engines.invoice_template_service import build_invoice_entry_workbook
from engines.duplicate_scan_service import dismiss as dismiss_duplicates, restore as restore_duplicates, scan_possible_duplicates
from engines.overview_pdf_service import generate_overview_pdf
from engines.overview_report_service import PERIOD_KINDS, get_overview
from engines.invoice_import_service import parse_invoice_spreadsheet, parse_invoice_workbook, match_supplier, get_template_csv
from engines.loan_schedule_service import (
    suggest_installment_interest,
    add_loan_installment,
    unlinked_loan_checks,
    create_schedule_from_checks,
    edit_loan_installment,
    edit_loan_note_terms,
    delete_loan_installment,
    mark_installment_active,
    unlink_installment_for_check,
    retarget_installment_check,
)
from engines.check_engine import (
    DEFAULT_BRANCH_CODE,
    DEFAULT_CHECK_WIDTH_MM,
    DEFAULT_CHECK_HEIGHT_MM,
    generate_metrobank_check_pdf,
)
from engines.cc_service import get_statement_gap_alerts, get_annual_fee_alerts, get_statement_coverage
from engines.checkbook_service import audit_booklet_gaps, get_checkbook_alerts
from engines.last_modified_sql import statement_modified_sql, loan_note_modified_sql, bank_loan_modified_sql
from engines.global_search_service import search_everything
from engines.payment_run_service import get_run_stages, waiting_count
from engines.park_draft_service import save_park_draft, load_park_draft, clear_park_draft, list_park_drafts
from engines.check_run_service import (
    get_pending_check_run,
    get_unnumbered_checks,
    mark_checks_printed,
    generate_check_run_pdf,
    generate_check_run_summary_pdf,
    generate_batch_txt,
    generate_pending_check_run_txt,
    print_batch_tag,
    file_name_for_checks,
    file_name_for_batch,
    mark_batch_printed,
    get_unprinted_batches,
    get_chrysanth_settings,
    save_chrysanth_settings,
    CHRYSANTH_CROSSING_OPTIONS,
    get_check_print_batches,
    get_check_print_batch_detail,
)
from engines.backup_service import export_database_backup, restore_database_from_bytes
from engines.duplicate_service import (
    loan_check_mismatches,
    bank_loan_warnings, bank_schedule_warnings, cc_statement_warnings, check_warnings, credit_card_warnings,
    credit_memo_warnings, cc_statement_month_warnings, creditor_warnings, invoice_warnings, loan_installment_warnings, loan_note_warnings,
)
from engines.reports_service import (
    get_payables_rundown,
    get_invoice_rundown,
    get_bank_loan_servicing_report,
    get_bank_loan_history,
    get_cardholder_all_cards_history,
    get_credit_card_history,
    get_lender_history,
    get_payment_schedule_all_tracks,
    get_payment_schedule_daily_totals,
    get_private_lender_master_report,
    get_supplier_ap_aging_report,
    get_supplier_history,
    get_parked_checks_report,
)
from engines.payment_schedule_report_service import generate_payment_schedule_pdf, generate_parked_checks_pdf
from engines.interest_service import get_monthly_interest_summary, get_yearly_interest_trend
from engines.account_history_pdf_service import (
    generate_supplier_statement_pdf,
    generate_rundown_pdf,
    generate_lender_statement_pdf,
    generate_bank_loan_statement_pdf,
    generate_credit_card_statement_pdf,
    generate_cardholder_statement_pdf,
)
from engines.dashboard_kpi_service import (
    get_average_dpo_suppliers,
    get_overdue_cancelled_checks,
    get_top_suppliers,
    get_payables_trend,
    get_upcoming_cash_obligations,
    get_supplier_concentration,
    get_top_supplier_dependency,
)
from engines.search_service import search_records
from engines.soa_matcher import reconcile_vendor_soa
from engines.validators import calculate_trade_discount, detect_invoice_anomalies, money_amount_error
from engines.analytics_service import (
    get_ap_composition_by_track,
    get_ap_growth_rate,
    get_supplier_pareto,
    get_lender_pareto,
    get_bank_loan_pareto,
    get_credit_card_pareto,
    get_all_tracks_pareto,
    get_check_status_breakdown,
    get_check_aging_distribution,
    get_debt_cost_comparison,
    get_missing_debt_terms,
)
# Business rules live in engines/*_rules.py and engines/common.py (moved out of this file 2026-09-27); imported back so every
# name still works here and as app.<name>.
from engines.common import (  # noqa: F401
    DB_PATH,
    PRIMARY_BANK_ACCOUNT_ID,
    _as_date,
    _pdf_money,
    cell_text,
    filter_by_date,
    fix_nullable_int,
    get_conn,
    has_value,
    php,
    prospective_year,
    q_live,
    remove_blocked_date,
    set_blocked_date,
)
from engines.creditor_rules import (  # noqa: F401
    CREDITOR_REFERENCE_TABLES,
    add_creditor,
    creditor_reference_counts,
    deactivate_creditor,
    delete_or_deactivate_creditor,
    get_or_create_card_issuer,
    reactivate_creditor,
    update_creditor,
)
from engines.statement_rules import (  # noqa: F401
    DEFAULT_SUPPLIER_TERMS,
    _batch_lock_status,
    add_credit_memo,
    add_invoice,
    add_terms_bank_rule,
    confirm_credit_memos,
    create_order_statement,
    delete_credit_memo,
    delete_invoice,
    edit_credit_memo,
    edit_invoice,
    get_batch_balance,
    get_batch_memo_status,
    get_invoices_with_remaining,
    lock_statement,
    new_draft_ref,
    parse_less_pct,
    parse_terms_days,
    recompute_statement_total,
    resolve_due_date,
    split_remaining_evenly,
    sync_invoice_payment_status,
    unlock_statement,
    waterfall_allocate_preview,
)
from engines.check_rules import (  # noqa: F401
    _booklet_row_dict,
    _check_number_key,
    _check_number_variants,
    _is_check_number_available,
    allocate_check_to_invoices,
    apply_check_correction,
    archive_booklet,
    assign_check_number,
    assign_check_number_from_booklet,
    assign_check_number_manual,
    assign_check_numbers_from_booklet,
    assign_check_numbers_manual,
    build_dual_copy_check_pdf,
    cancel_check,
    cancel_order_statement,
    create_booklet,
    edit_parked_check,
    get_available_booklet_numbers,
    get_booklets,
    get_check_print_calibration,
    get_existing_check_dates_for_creditor,
    get_latest_check_for_creditor,
    issue_check,
    issue_checks_grid,
    log_spoiled_check,
    pick_number_source,
    plan_sequential_numbers,
    print_check_pdf,
    replace_check,
    retarget_invoice_allocations_for_check,
    retract_check_number,
    spoiled_check_problems,
    unlink_invoice_allocations_for_check,
    unpark_checks,
)
from engines.loan_note_rules import (  # noqa: F401
    _LOAN_NOTE_STATUSES,
    cancel_loan_note,
    create_loan_note,
    delete_loan_note,
    promote_draft_loan_note,
    update_loan_note_status,
)
from engines.card_rules import (  # noqa: F401
    _is_latest_cc_statement,
    add_cardholder,
    add_cc_statement,
    add_credit_card,
    confirm_cc_payment_from_next_statement,
    deactivate_cardholder,
    deactivate_credit_card,
    delete_cc_statement,
    edit_cardholder,
    edit_cc_statement,
    edit_credit_card,
    get_default_statement_month,
    get_last_cc_statement_unpaid_balance,
    get_unconfirmed_cc_statement,
    reactivate_cardholder,
    reactivate_credit_card,
)
# Screens live in views/ (one file per main tab) and the shared screen helpers in views/shared.py (moved out of this file
# 2026-09-27); imported back so every name still works here and as app.<name>.
import views.add_data
import views.admin
import views.analytics
import views.bank_loans
import views.checks
import views.credit_cards
from views.credit_cards import _card_label  # noqa: F401 -- app._card_label, used by tests
import views.creditor_master
import views.dashboard
import views.lenders
import views.order_statement
import views.payment_run
import views.reports
from views.shared import (  # noqa: F401
    BL_BROWSE,
    BL_NEW,
    LN_BROWSE,
    LN_NEW,
    MONEY_FORMAT,
    OS_BROWSE,
    OS_NEW,
    PAGES,
    PRINT_CHECKS_IN_APP,
    PdfReader,
    PdfWriter,
    STATUS_STYLE,
    SUPPLIER_TERMS_OPTIONS,
    WHATS_NEW_PATH,
    _get_app_start_time,
    _jump,
    _read_whats_new,
    _read_whats_new_latest_section,
    _render_retract_number_section,
    _whats_new_popup_tracker,
    bank_loan_options,
    batch_scoped_key,
    cardholder_options_with_cards,
    confirm_cancel_bank_loan_dialog,
    confirm_cancel_dialog,
    confirm_cancel_loan_note_dialog,
    confirm_create_dialog,
    confirm_delete_loan_note_dialog,
    confirm_unlock_dialog,
    credit_card_options,
    credit_card_options_for_holder,
    creditor_options,
    creditor_options_all,
    date_col,
    date_preset_filter,
    default_index,
    dup_needs_confirm,
    dup_note,
    dup_resume,
    duplicate_warning_dialog,
    filter_by_status,
    flash,
    get_blocked_dates_for_month,
    global_search_dialog,
    go_to,
    guard_duplicate_submit,
    history_date_range,
    mode_label,
    money_col,
    page_header,
    pdf_preview_and_download,
    q,
    refresh,
    render_assign_number_section,
    render_check_print_section,
    section,
    selectbox_creditor,
    selectbox_creditor_label,
    show_flashes,
    status_badge,
    status_text,
)


# --------------------------------------------------------------------------
# Page setup
# --------------------------------------------------------------------------

# Sandbox vs live indicator (2026-09-17): both instances run the exact same code, so with nothing
# else distinguishing them, the only visible difference used to be the port number in the URL --
# an easy mistake to make with two tabs open. Detected from AP_DB_PATH itself rather than a
# separate flag, so it can never drift out of sync with which database is actually behind this
# instance.
IS_SANDBOX = "TEST" in os.path.basename(DB_PATH).upper()
_PAGE_TITLE = ("🧪 SANDBOX — " if IS_SANDBOX else "") + "Slick's Tire Supply — AP Disbursement"

# No logo on screen (2026-09-27, per user: "remove Slick's logo ... everywhere in the app"); printed PDFs keep it.
st.set_page_config(page_title=_PAGE_TITLE, page_icon=":material/request_quote:", layout="wide")

# Startup check: if the program is newer than the database, say so plainly instead of failing on a missing column.
_schema_stamp = os.path.getmtime(os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql"))
if st.session_state.get("_schema_checked") != _schema_stamp:   # re-checks whenever a newer schema.sql arrives, even mid-session
    _pending_updates = pending_schema_updates(DB_PATH)
    if _pending_updates:
        st.error("The database needs a quick update before the system can run.")
        _nl = chr(10)
        st.markdown(
            "New features were added that need a small change to the database. Nothing is lost, and it takes a few seconds:" + _nl + _nl
            + "1. Close this browser tab." + _nl
            + "2. Double-click **Update Database** (`update_database.bat`) in the project folder. It backs up your data first, updates it, and restarts the system." + _nl + _nl
            + "What is missing: " + ", ".join(_pending_updates)
        )
        st.stop()
    st.session_state["_schema_checked"] = _schema_stamp

_whats_new_tracker = _whats_new_popup_tracker()
# st.runtime.exists() is False during a bare `import app` (pytest's conftest.py, or any other
# script-mode import) -- st.dialog needs a real, running Streamlit server behind it.
if st.runtime.exists() and not _whats_new_tracker["shown"]:
    _whats_new_tracker["shown"] = True
    _latest_whats_new = _read_whats_new_latest_section()
    if _latest_whats_new:
        @st.dialog("What's new")
        def _show_whats_new_dialog(text: str) -> None:
            st.markdown(text)
            if st.button("Got it", width="stretch"):
                st.rerun()
        _show_whats_new_dialog(_latest_whats_new)

show_flashes()      # "Saved ..." toasts queued by refresh(done=...) before the last redraw

# --------------------------------------------------------------------------
# Sidebar menu (2026-09-27, per user: a grouped menu instead of 10 tabs across the top). Each page is one views/<x>.render();
# only the open page runs. The id is the page's web address (localhost:8501/<id>) and what jumps / the search use.
# --------------------------------------------------------------------------
@st.cache_data(ttl="15s")
def _run_waiting() -> int:
    """How many things wait in the payment cycle -- shown beside Payment run in the menu."""
    return waiting_count(get_run_stages())


_n_waiting = _run_waiting()
PAGES.clear()
PAGES.update({
    "payment-run": st.Page(views.payment_run.render, title="Payment run" + (f" · {_n_waiting}" if _n_waiting else ""), icon=":material/checklist:",
                           url_path="payment-run", default=True),
    "add-data": st.Page(views.add_data.render, title="Add data", icon=":material/add_box:", url_path="add-data"),
    "dashboard": st.Page(views.dashboard.render, title="Dashboard", icon=":material/dashboard:", url_path="dashboard"),
    "order-statements": st.Page(views.order_statement.render, title="Order statements", icon=":material/description:", url_path="order-statements"),
    "checks": st.Page(views.checks.render, title="Checks and checkbook", icon=":material/receipt_long:", url_path="checks"),
    "lenders": st.Page(views.lenders.render, title="Lenders", icon=":material/handshake:", url_path="lenders"),
    "bank-loans": st.Page(views.bank_loans.render, title="Bank loans", icon=":material/account_balance:", url_path="bank-loans"),
    "credit-cards": st.Page(views.credit_cards.render, title="Credit cards", icon=":material/credit_card:", url_path="credit-cards"),
    "creditors": st.Page(views.creditor_master.render, title="Creditors", icon=":material/contacts:", url_path="creditors"),
    "analytics": st.Page(views.analytics.render, title="Analytics", icon=":material/query_stats:", url_path="analytics"),
    "reports": st.Page(views.reports.render, title="Reports", icon=":material/summarize:", url_path="reports"),
    "admin": st.Page(views.admin.render, title="Admin and audit", icon=":material/admin_panel_settings:", url_path="admin"),
})
MENU = {
    "Daily work": ["payment-run", "add-data", "order-statements", "checks"],
    "Other payables": ["lenders", "bank-loans", "credit-cards"],
    "Records and reports": ["dashboard", "creditors", "analytics", "reports"],
    "System": ["admin"],
}
page = st.navigation({section: [PAGES[pid] for pid in ids] for section, ids in MENU.items()}, position="sidebar")

# A jump requested by the search pop-up or a page button (set, then st.rerun()): apply it before any page draws.
_pending_global_jump = st.session_state.pop("_global_jump", None)
if _pending_global_jump:
    _jump(_pending_global_jump["page"], **_pending_global_jump["state"])


with st.sidebar:
    park_draft_slot = st.empty()      # "checks typed but not parked" reminder, filled at the end of the script
    if IS_SANDBOX:
        st.markdown(
            "<div style='background-color:#FFA500;color:#000;text-align:center;"
            "font-weight:bold;padding:8px;border-radius:4px;margin-bottom:8px;'>"
            "🧪 SANDBOX — test data, not real</div>",
            unsafe_allow_html=True,
        )
    st.markdown("**Slick's Tire Supply** · AP" + (" (SANDBOX)" if IS_SANDBOX else ""))
    bank_df = q("SELECT * FROM bank_accounts WHERE bank_account_id = ?", (PRIMARY_BANK_ACCOUNT_ID,))
    bank = bank_df.iloc[0] if not bank_df.empty else None
    # "Did my restart actually pick up the change" indicator (2026-09-17): st.cache_resource holds its value for the life of
    # this Python process, so this timestamp changes exactly when restart_live.bat / run_test_streamlit.bat restarts the app.
    st.caption((f"{bank['bank_name']} · Branch {bank['branch_code']} · " if bank is not None else "")
               + f"started {_get_app_start_time().strftime('%b %d, %I:%M %p')}")

    search_term = st.text_input(
        "Quick search", key="quick_search_term",
        placeholder="Check #, invoice, batch/LN/loan ref, name",
        label_visibility="collapsed", icon=":material/search:",
    )

    if search_term.strip():
        result = search_records(search_term)
        kind, matches = result["kind"], result["matches"]

        if kind == "none":
            st.warning(f"No check or invoice found matching '{search_term}'.")

        elif kind == "spoiled":
            for m in matches:
                extra = f" ({m['reason']})" if m["reason"] else ""
                st.info(f"Check {m['check_number']} is logged as spoiled — no batch attached.{extra}")

        elif kind == "check":
            st.caption(f"{len(matches)} check(s) match:")
            for m in matches:
                label = f"{m['check_number']} · {m['creditor_name']} · {php(m['amount'])} · {m['status']}"
                if st.button(label, key=f"jump_check_{m['check_id']}", width="stretch"):
                    if m["statement_id"] is not None:
                        _jump("order-statements", selected_statement_id=m["statement_id"], os_mode=OS_BROWSE, os_subtab="Checks",
                              **{f"os_checks_view__{m['statement_id']}": "Checks in this batch"})
                    else:
                        _jump("lenders", selected_loan_note_id=m["loan_note_id"], ln_mode=LN_BROWSE, ln_subtab="Checks")

        elif kind == "invoice":
            st.caption(f"{len(matches)} invoice(s) match:")
            for m in matches:
                label = f"{m['doc_ref']} · {m['creditor_name']} · {php(m['amount'])} · {m['statement_ref']}"
                if st.button(label, key=f"jump_inv_{m['invoice_id']}", width="stretch"):
                    _jump("order-statements", selected_statement_id=m["statement_id"], os_mode=OS_BROWSE, os_subtab="Invoices")

        elif kind == "ref":
            st.caption(f"{len(matches)} record(s) match:")
            for m in matches:
                label = f"[{m['type']}] {m['ref']} · {m['payee']} · {php(m['amount'])} · {m['status']}"
                if st.button(label, key=f"jump_ref_{m['type']}_{m['id']}", width="stretch"):
                    if m["type"] == "Order Statement":
                        _jump("order-statements", selected_statement_id=m["id"], os_mode=OS_BROWSE)
                    elif m["type"] == "Loan Note":
                        _jump("lenders", selected_loan_note_id=m["id"], ln_mode=LN_BROWSE)
                    else:
                        _jump("bank-loans", selected_bank_loan_id=m["id"], bl_mode=BL_BROWSE)

    if st.button("Search everything  [ / ]", icon=":material/manage_search:", key="global_search_open_side", width="stretch"):
        global_search_dialog()

    with st.expander("Mini calendar", icon=":material/calendar_month:", expanded=True):
        # Mini calendar grid, not a flat list (2026-09-17, revised per user's actual purpose: a quick
        # visual reference for which dates are already loaded while typing in a check date on the
        # Order Statement/Lenders/Checks & checkbook tabs). The sidebar is the one part of this app
        # that stays on screen regardless of which tab is active -- a modal/dialog popup would close
        # the moment you switched tabs, so this is the only surface that actually works for that.
        st.caption("For reference while issuing checks.")
        # Month/year pickers + prev/next arrows. The arrows change the pickers through on_click callbacks, NOT by calling
        # st.rerun() from here: this sidebar is drawn before the main tabs, so an early st.rerun() made Streamlit forget
        # which tab was open and dropped you back on the Dashboard every time (2026-09-26). A callback runs before the page
        # is redrawn, so the tabs keep their state.
        side_cal_years = list(range(date.today().year - 5, date.today().year + 11))
        side_cal_month_names = [date(2000, m, 1).strftime("%b") for m in range(1, 13)]
        if st.session_state.get("side_cal_year_pick") not in side_cal_years:
            st.session_state["side_cal_year_pick"] = date.today().year
        if st.session_state.get("side_cal_month_pick") not in range(1, 13):
            st.session_state["side_cal_month_pick"] = date.today().month

        def _side_cal_step(delta: int) -> None:
            m, y = st.session_state["side_cal_month_pick"] + delta, st.session_state["side_cal_year_pick"]
            if m < 1:
                m, y = 12, y - 1
            elif m > 12:
                m, y = 1, y + 1
            if y in side_cal_years:
                st.session_state["side_cal_month_pick"], st.session_state["side_cal_year_pick"] = m, y

        side_cal_prev_col, side_cal_month_col, side_cal_year_col, side_cal_next_col = st.columns([0.8, 2, 2.5, 0.8], vertical_alignment="center", gap="small")
        side_cal_prev_col.button("◀", key="side_cal_prev", on_click=_side_cal_step, args=(-1,))
        def _side_cal_month_picked() -> None:
            st.session_state["side_cal_year_pick"] = prospective_year(st.session_state["side_cal_month_pick"])

        side_cal_month_col.selectbox("Month", options=list(range(1, 13)), format_func=lambda m: side_cal_month_names[m - 1],
                                     key="side_cal_month_pick", label_visibility="collapsed", on_change=_side_cal_month_picked)
        side_cal_year_col.selectbox("Year", options=side_cal_years, key="side_cal_year_pick", label_visibility="collapsed")
        side_cal_next_col.button("▶", key="side_cal_next", on_click=_side_cal_step, args=(1,))
        side_cal_year, side_cal_month = st.session_state["side_cal_year_pick"], st.session_state["side_cal_month_pick"]

        # Single letters ("M","T","W","T","F","S","S") collided -- Tue/Thu both became "T" and
        # Sat/Sun both became "S", merging those column pairs into one on the x-axis instead of
        # keeping 7 distinct days. Two-letter codes stay unique while still fitting the narrow
        # sidebar width.
        side_cal_weekday_names = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
        side_cal_rows = []
        for side_cal_week_idx, side_cal_week in enumerate(calendar.Calendar(firstweekday=0).monthdayscalendar(side_cal_year, side_cal_month)):
            for side_cal_day_idx, side_cal_day_num in enumerate(side_cal_week):
                if side_cal_day_num == 0:
                    continue
                side_cal_rows.append({
                    "date": date(side_cal_year, side_cal_month, side_cal_day_num),
                    "week": side_cal_week_idx, "weekday": side_cal_weekday_names[side_cal_day_idx], "day": side_cal_day_num,
                })
        side_cal_grid_df = pd.DataFrame(side_cal_rows)
        side_cal_daily_df = get_payment_schedule_daily_totals()
        if not side_cal_daily_df.empty:
            side_cal_daily_df = side_cal_daily_df.copy()
            side_cal_daily_df["actual_payment_date"] = pd.to_datetime(side_cal_daily_df["actual_payment_date"]).dt.date
        side_cal_merged_df = (
            side_cal_grid_df.merge(side_cal_daily_df, left_on="date", right_on="actual_payment_date", how="left")
            if not side_cal_daily_df.empty else side_cal_grid_df.assign(total_amount=0.0, num_items=0)
        )
        side_cal_merged_df["total_amount"] = side_cal_merged_df["total_amount"].fillna(0.0)
        side_cal_merged_df["num_items"] = side_cal_merged_df["num_items"].fillna(0).astype(int)

        # Both layers must share the exact same Axis object, not one Axis(orient="top") and one
        # axis=None (2026-09-17 -- this was the actual bug, not a missing-label rendering issue as
        # it first looked): when Altair layers two charts whose axis specs for the same channel
        # disagree, it has to resolve the conflict, and it silently dropped the labels entirely
        # rather than keeping either version. The main Dashboard calendar avoids this by giving every
        # layer the identical axis object -- matched here.
        side_cal_weekday_axis = alt.Axis(orient="top", labelFontSize=9, labelAngle=0)
        # Marked days (2026-09-26, per user): the same grey days the Dashboard's payment calendar shows -- Philippine holidays
        # and any date marked as blocked (calendar_blocked_dates) -- drawn as a flat grey layer over the heat colour.
        side_cal_blocked = get_blocked_dates_for_month(side_cal_year, side_cal_month)
        side_cal_merged_df["is_blocked"] = side_cal_merged_df["date"].isin(side_cal_blocked.keys())
        side_cal_merged_df["blocked_label"] = side_cal_merged_df["date"].apply(
            lambda d: side_cal_blocked.get(d, {}).get("label") or side_cal_blocked.get(d, {}).get("kind", "").title())
        side_cal_normal_df = side_cal_merged_df[~side_cal_merged_df["is_blocked"]]
        side_cal_blocked_df = side_cal_merged_df[side_cal_merged_df["is_blocked"]]
        side_cal_tooltip = [alt.Tooltip("date:T", title="Date"), alt.Tooltip("total_amount:Q", title="Total", format=",.2f"), alt.Tooltip("num_items:Q", title="Items")]
        side_cal_heat = alt.Chart(side_cal_normal_df).mark_rect(stroke="white", strokeWidth=1, cornerRadius=3).encode(
            x=alt.X("weekday:N", title=None, sort=side_cal_weekday_names, axis=side_cal_weekday_axis),
            y=alt.Y("week:O", title=None, axis=None),
            color=alt.Color("total_amount:Q", scale=alt.Scale(scheme="blues", type="sqrt"), legend=None),
            tooltip=side_cal_tooltip,
        )
        side_cal_grey = alt.Chart(side_cal_blocked_df).mark_rect(stroke="white", strokeWidth=1, cornerRadius=3, color="#C4C4C4").encode(
            x=alt.X("weekday:N", title=None, sort=side_cal_weekday_names, axis=side_cal_weekday_axis),
            y=alt.Y("week:O", title=None, axis=None),
            tooltip=side_cal_tooltip + [alt.Tooltip("blocked_label:N", title="Marked")],
        )
        side_cal_dark_threshold = side_cal_merged_df["total_amount"].max() * 0.5
        side_cal_text = alt.Chart(side_cal_merged_df).mark_text(baseline="middle", fontSize=9).encode(
            x=alt.X("weekday:N", sort=side_cal_weekday_names, axis=side_cal_weekday_axis), y=alt.Y("week:O", axis=None),
            text="day:N",
            color=(
                alt.when(alt.datum.is_blocked).then(alt.value("#404040"))
                .when(alt.datum.total_amount > side_cal_dark_threshold).then(alt.value("white"))
                .otherwise(alt.value("#333333"))
            ),
            tooltip=side_cal_tooltip,
        )
        st.altair_chart((side_cal_heat + side_cal_grey + side_cal_text).properties(height=150), width="stretch")
        st.caption("Hover a day for its total. Grey = holiday or marked date. Full calendar: Dashboard.")

    st.button("Refresh data", icon=":material/refresh:", on_click=lambda: st.cache_data.clear(), width="stretch")
    with st.expander("Keyboard shortcuts", icon=":material/keyboard:"):
        st.markdown(
            "| Keys | What it does |" + chr(10) + "|---|---|" + chr(10)
            + "| **Enter** | In a confirmation pop-up: **Yes** (unless a button is focused) |" + chr(10)
            + "| **Esc** | In a pop-up: go back |" + chr(10)
            + "| **Alt + 1 … 9, 0** | Open a menu page (1 = the first page in the menu, 0 = the tenth) — hover a menu item to see its key |" + chr(10)
            + "| **/** | Open **Search everything** (checks, invoices, batches, loans, creditors, cards…) |" + chr(10)
            + "| **Alt + P** | Press the screen's main **P**ark / Save / Add button (only if there is one) |" + chr(10)
            + "| **Alt + N** | Open the **N**ew … form (New Batch, New Loan Note, New Bank Loan) |" + chr(10)
            + "| **Tab / Shift+Tab** | Next / previous box or button |" + chr(10)
            + "| **Enter / Space** | Press the focused button |" + chr(10)
            + "| **↑ ↓** | Move through a dropdown (or just type to search it) |"
        )

st.markdown("<style>.st-key-global_search_open_main{position:absolute;width:0;height:0;overflow:hidden;opacity:0;}</style>", unsafe_allow_html=True)
if st.button("Search everything", key="global_search_open_main"):
    global_search_dialog()

page.run()          # only the open page runs; the others cost nothing


# Guardrail (2026-09-27): reminder for checks typed but never parked -- in the sidebar, and a browser prompt if the page is
# about to be closed/refreshed while any are pending.
_pending_drafts = list_park_drafts()
if _pending_drafts:
    park_draft_slot.warning(
        ":material/edit_note: **Checks typed but not parked yet**" + chr(10) + chr(10)
        + chr(10).join(f"- {d['label']} — {d['n']} check(s), {php(d['total'])}" for d in _pending_drafts)
        + chr(10) + chr(10) + "Open the batch (Order Statement, Checks) and click **Park Metrobank checks**."
    )
else:
    park_draft_slot.empty()
components.html(
    "<script>try{window.parent.onbeforeunload=" + ("function(e){e.preventDefault();e.returnValue='';return '';}" if _pending_drafts else "null") + ";}catch(e){}</script>",
    height=0,
)

# Keyboard shortcuts (2026-09-27, per user: less trackpad/mouse). Streamlit has no hotkey setting, so a small script on the page listens for keys and clicks
# the matching control. It replaces its own listener on every run, so it never doubles up. The list is shown in the sidebar (Keyboard shortcuts).
components.html(r"""<script>
(function () {
  var P = window.parent, D = P.document;
  if (P.__apHotkeys) D.removeEventListener('keydown', P.__apHotkeys, true);
  function visible(el) { return el && el.offsetParent !== null; }
  function isTyping(t) { if (!t) return false; var tag = (t.tagName || '').toLowerCase(); return tag === 'input' || tag === 'textarea' || t.isContentEditable; }
  function buttons(root) { return Array.from(root.querySelectorAll('button')).filter(function (b) { return !b.disabled && visible(b); }); }
  function flash(msg) {
    var old = D.getElementById('ap-hotkey-flash'); if (old) old.remove();
    var d = D.createElement('div'); d.id = 'ap-hotkey-flash'; d.textContent = msg;
    d.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#1f3a5f;color:#fff;padding:10px 18px;border-radius:8px;z-index:99999;font-size:14px;';
    D.body.appendChild(d); setTimeout(function () { d.remove(); }, 2600);
  }
  function menuLinks() { return Array.from(D.querySelectorAll('[data-testid="stSidebarNav"] a')); }   // the sidebar menu, top to bottom
  function searchBox() { return Array.from(D.querySelectorAll('input')).find(function (i) { return (i.placeholder || '').indexOf('Check #') === 0; }); }
  var handler = function (e) {
    var dlg = D.querySelector('[role=dialog]');
    if (dlg) {
      if (e.key === 'Enter' && !e.shiftKey && !e.altKey && !e.ctrlKey) {
        var a = D.activeElement;
        if (a && a.tagName === 'BUTTON') return;                      // a focused button keeps its own behaviour
        var yes = buttons(dlg).find(function (b) { return /^\s*(\S+\s+)?yes/i.test(b.innerText || ''); });
        if (yes) { yes.click(); e.preventDefault(); }
      } else if (e.key === 'Escape') {
        var back = buttons(dlg).find(function (b) { return /go back|no, go back/i.test(b.innerText || ''); });
        if (back) { back.click(); e.preventDefault(); }
      }
      return;
    }
    if (e.altKey && !e.ctrlKey && !e.shiftKey && /^Digit[0-9]$/.test(e.code)) {
      var n = e.code === 'Digit0' ? 9 : parseInt(e.code.slice(5), 10) - 1;
      var links = menuLinks();
      if (links[n]) { links[n].click(); e.preventDefault(); }
      return;
    }
    if (e.key === '/' && !e.altKey && !e.ctrlKey && !isTyping(e.target)) {
      var opener = D.querySelector('.st-key-global_search_open_main button');
      if (opener) {
        opener.click();
        var tries = 0, timer = setInterval(function () {                 // put the cursor in the pop-up's search box as soon as it exists
          var inp = D.querySelector('[role=dialog] input');
          if (inp) { inp.focus(); clearInterval(timer); } else if (++tries > 30) { clearInterval(timer); }
        }, 100);
      } else { var box = searchBox(); if (box) box.focus(); }
      e.preventDefault();
      return;
    }
    if (e.altKey && !e.ctrlKey && !e.shiftKey && e.code === 'KeyP') {
      var area = D.querySelector('[data-testid="stMain"]') || D;
      var re = /Park Metrobank checks|Save invoice changes|Save statement changes|Add statement|Assign check number|Save changes|Save parked check edits|Save check corrections|Add installment|Add invoice|Create schedule rows/i;
      var cands = buttons(area).filter(function (b) { return re.test(b.innerText || ''); });
      if (cands.length === 1) { cands[0].click(); }
      else if (cands.length === 0) { flash('No Park / Save button on this screen.'); }
      else { flash(cands.length + ' Park / Save buttons here - click the one you want.'); }
      e.preventDefault();
      return;
    }
    if (e.altKey && !e.ctrlKey && !e.shiftKey && e.code === 'KeyN') {
      var area2 = D.querySelector('[data-testid="stMain"]') || D;
      var nb = buttons(area2).find(function (b) { return /New (Batch|Loan Note|Bank Loan)/i.test(b.innerText || ''); });
      if (nb) { nb.click(); } else { flash('No "New ..." form on this screen.'); }
      e.preventDefault();
    }
  };
  P.__apHotkeys = handler;
  D.addEventListener('keydown', handler, true);
  // Search as you type: Streamlit only reads a text box on Enter, so once you pause typing in the Search everything box a synthetic Enter is sent.
  if (P.__apLiveSearch) D.removeEventListener('input', P.__apLiveSearch, true);
  P.__apLiveSearch = function (e) {
    var t = e.target;
    if (!t || t.tagName !== 'INPUT' || !t.closest('[role=dialog]') || (t.placeholder || '').indexOf('e.g. 3992554313') !== 0) return;
    if (P.__apSearchTimer) P.clearTimeout(P.__apSearchTimer);
    P.__apSearchTimer = P.setTimeout(function () {
      t.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true }));
    }, 350);
  };
  D.addEventListener('input', P.__apLiveSearch, true);
  // hovering a menu item shows its hotkey (Streamlit redraws the menu now and then, so this is re-applied every second)
  function labelTabs() {
    menuLinks().slice(0, 10).forEach(function (t, i) {
      var tip = 'Alt+' + ((i + 1) % 10);
      if (t.title !== tip) t.title = tip;
    });
  }
  if (P.__apHotkeyTimer) P.clearInterval(P.__apHotkeyTimer);
  P.__apHotkeyTimer = P.setInterval(labelTabs, 1000);
  labelTabs();
})();
</script>""", height=0)
