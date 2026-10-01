"""Reports screen (moved out of app.py unchanged, 2026-09-27). app.py calls render() only while this page is open.

2026-09-28 (Reports review, plan phase 3): every table uses the shared friendly column names and the one money / date format
(views.shared.show_table), every export offers CSV and Excel (export_buttons), the heavy queries are cached for a minute (refresh() clears
them after any save), the Schedule of payments gained a date range and payee filter, the Overview shows its trend and check activity on
screen, and the Schedule / Parked daily views and the per-supplier pivots share one function each."""
from __future__ import annotations

from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from engines.account_history_pdf_service import generate_bank_loan_statement_pdf
from engines.account_history_pdf_service import generate_cardholder_statement_pdf
from engines.account_history_pdf_service import generate_credit_card_statement_pdf
from engines.account_history_pdf_service import generate_lender_statement_pdf
from engines.account_history_pdf_service import generate_rundown_pdf
from engines.account_history_pdf_service import generate_supplier_statement_pdf
from engines.cleared_checks_report import by_payee, by_week, cleared_checks, cleared_span
from engines.common import _pdf_money
from engines.common import filter_by_date
from engines.common import php
from engines.duplicate_scan_service import dismiss as dismiss_duplicates
from engines.duplicate_scan_service import restore as restore_duplicates
from engines.duplicate_scan_service import scan_possible_duplicates
from engines.overview_pdf_service import generate_overview_pdf
from engines.overview_report_service import PERIOD_KINDS
from engines.overview_report_service import bank_note, bank_rows, get_overview
from engines.payment_schedule_report_service import generate_parked_checks_pdf
from engines.payment_schedule_report_service import generate_payment_schedule_pdf
from engines.reports_service import filter_rundown, filter_schedule
from engines.reports_service import get_bank_loan_history
from engines.reports_service import get_bank_loan_servicing_report
from engines.reports_service import get_cardholder_all_cards_history
from engines.reports_service import get_credit_card_history
from engines.reports_service import get_invoice_rundown
from engines.reports_service import get_lender_history
from engines.reports_service import get_parked_checks_report
from engines.reports_service import get_payables_rundown
from engines.reports_service import get_payment_schedule_all_tracks
from engines.reports_service import get_private_lender_master_report
from engines.reports_service import get_supplier_ap_aging_report
from engines.reports_service import get_supplier_history

from views.shared import (  # noqa: F401
    MONEY_FORMAT,
    bank_loan_options,
    cardholder_options_with_cards,
    credit_card_options_for_holder,
    creditor_options_all,
    date_col,
    export_buttons,
    filter_by_status,
    history_date_range,
    money_col,
    page_header,
    pdf_preview_and_download,
    refresh,
    section,
    show_table,
    status_badge,
    status_text,
)
from engines.clock import manila_today

# The heavy report queries, cached for a minute: switching filters or reports no longer re-runs every query (the Overview alone runs ~40).
# refresh() -- called after every save in the app -- clears the cache, so a change shows at once.
_cached = st.cache_data(ttl=60, show_spinner=False)
c_overview = _cached(get_overview)
c_schedule = _cached(get_payment_schedule_all_tracks)
c_parked = _cached(get_parked_checks_report)
c_rundown = _cached(get_payables_rundown)
c_invoice_rundown = _cached(get_invoice_rundown)
c_supplier_history = _cached(get_supplier_history)
c_lender_history = _cached(get_lender_history)
c_bank_loan_history = _cached(get_bank_loan_history)
c_card_history = _cached(get_credit_card_history)
c_cardholder_history = _cached(get_cardholder_all_cards_history)
c_aging = _cached(get_supplier_ap_aging_report)
c_lender_master = _cached(get_private_lender_master_report)
c_bank_loan_master = _cached(get_bank_loan_servicing_report)
c_duplicates = _cached(scan_possible_duplicates)

# 13 reports became 9 (owner, 2026-09-28: "clean redundant standard reports ... if it can be integrated or combined to a similar report then
# do so"; taken off the menu, nothing deleted): Parked checks is a view of the Schedule of payments, the batch and invoice rundowns are one
# report with a By batch / By invoice switch, and the three master rollups are one report with a switch.
REPORT_OPTIONS = [
    "Overview — highlights & insights",
    "Possible duplicates — check everything on file",
    "Schedule of payments — all tracks",
    "Cleared checks — from the bank",
    "Supplier — invoice & check history",
    "Supplier — rundown (paid / partial / pending)",
    "Private lender — loan note & check history",
    "Bank loan — installment history",
    "Credit card — statement history",
    "Master rollups — aging and exposure",
    "Lists — suppliers, lenders, banks, batches and more",
    "Decision reports — answers for choices",
]
# the old menu names (a browser tab left open on one of them) -> (new report, sub-view state to set)
OLD_REPORTS = {
    "Parked checks (not yet printed)": ("Schedule of payments — all tracks", {"sched_show": "Checks not yet printed (parked)"}),
    "Supplier — batch rundown (paid / partial / pending)": ("Supplier — rundown (paid / partial / pending)", {"rpt_rundown_view": "By batch"}),
    "Supplier — invoice rundown (paid / partial / pending)": ("Supplier — rundown (paid / partial / pending)", {"rpt_rundown_view": "By invoice"}),
    "Master rollup — supplier AP aging": ("Master rollups — aging and exposure", {"rpt_rollup_view": "Supplier AP aging"}),
    "Master rollup — private lender exposure": ("Master rollups — aging and exposure", {"rpt_rollup_view": "Private lender exposure"}),
    "Master rollup — bank loan servicing": ("Master rollups — aging and exposure", {"rpt_rollup_view": "Bank loan servicing"}),
}
SCHEDULE_VIEWS = ["Everything still owed", "Checks not yet printed (parked)"]
RUNDOWN_VIEWS = ["By batch", "By invoice"]
ROLLUP_VIEWS = ["Supplier AP aging", "Private lender exposure", "Bank loan servicing"]
STATUS_ORDER = ["Paid", "Partially Paid", "Pending"]


def _default(key: str, value) -> dict:
    """default= for a switch only when nothing preset it (a redirect from an old report name sets it; giving both makes Streamlit warn)."""
    return {} if key in st.session_state else {"default": value}


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(text).lower()).strip("_")


def _status_pivot(df: pd.DataFrame, value: str, order: list[str] = STATUS_ORDER) -> pd.DataFrame:
    """Per supplier: the `value` total for each status (missing statuses as 0) plus a Total column -- the batch and invoice rundowns."""
    piv = df.pivot_table(index="supplier", columns="status", values=value, aggfunc="sum", fill_value=0.0)
    for name in order:
        if name not in piv.columns:
            piv[name] = 0.0
    piv = piv[order].reset_index()
    piv["Total"] = piv[order].sum(axis=1)
    return piv


def _daily_view(detail: pd.DataFrame, *, day_label: str, count_label: str, what: str, key: str, file_stem: str, pdf, pdf_label: str) -> None:
    """Daily totals + metrics + full breakdown + CSV / Excel + PDF: shared by the Schedule of payments and the Parked checks reports."""
    daily = (detail.groupby("actual_payment_date").agg(num_items=("amount", "count"), total_amount=("amount", "sum")).reset_index()
             .sort_values("actual_payment_date"))
    labels = {"actual_payment_date": day_label, "num_items": count_label, "due_date": "Check date" if what == "checks" else "Due date"}
    st.markdown("**Daily totals**")
    show_table(daily, labels)
    m1, m2 = st.columns(2)
    m1.metric(f"Total {what}", int(daily["num_items"].sum()), border=True)
    m2.metric("Total amount", php(daily["total_amount"].sum()), border=True)
    st.markdown("**Full breakdown**")
    show_table(detail, labels)
    export_buttons({"Daily totals": daily, "Full breakdown": detail}, f"{file_stem}", key=key, labels=labels,
                   what="daily totals + breakdown")
    st.caption("The CSV holds the daily totals; the Excel file has both the daily totals and the full breakdown.")
    if st.button("Generate PDF", icon=":material/picture_as_pdf:", key=f"{key}_pdf_btn"):
        pdf_preview_and_download(pdf(), f"{file_stem}.pdf", key=f"{key}_pdf_download", label=pdf_label)


def _month_label(m: str) -> str:
    return pd.to_datetime(m + "-01").strftime("%B %Y")


# ------------------------------------------------------------------ the reports
def _overview() -> None:
    st.subheader("Overview — highlights & insights")
    st.caption("What happened in a day, week, month, quarter or year, compared with the period before it — highlights first, then the numbers behind them. Pick the short version for a quick read or the full one for the detail.")
    ov1, ov2, ov3 = st.columns([2, 2, 2])
    ov_kind = ov1.selectbox("Period", PERIOD_KINDS, index=2, key="rpt_ov_kind")
    ov_anchor = ov2.date_input("Any date inside the period", value=manila_today(), key="rpt_ov_anchor")
    ov_version = ov3.radio("Version", ["Short", "Full"], horizontal=True, key="rpt_ov_version")
    ov = c_overview(ov_kind, ov_anchor)
    ov_cur, ov_prev = ov["current"], ov["previous"]
    st.markdown(f"#### {ov['label']}  ·  compared with {ov['prev_label']}")

    st.markdown("**Highlights**")
    for line in ov["highlights"]:
        st.markdown(f"- {line}")

    def _ov_delta(now, before):
        return None if not before else f"{(now - before) / before * 100:+.0f}%"

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Paid out", php(ov_cur["paid"]), _ov_delta(ov_cur["paid"], ov_prev["paid"]), border=True, help="Checks, loan installments and card payments whose date has passed. Anything dated today still counts as scheduled.")
    m2.metric("Scheduled, not yet due", php(ov_cur["scheduled"]), _ov_delta(ov_cur["scheduled"], ov_prev["scheduled"]), border=True)
    m3.metric("Interest cost", php(ov_cur["interest_total"]), _ov_delta(ov_cur["interest_total"], ov_prev["interest_total"]), delta_color="inverse", border=True)
    m4.metric("Total outstanding today", php(ov["outstanding_today"]), border=True, help="All four tracks, as on the Dashboard.")

    # trend and check activity on screen too (2026-09-28: they used to be in the PDF only)
    trend = pd.DataFrame(ov["trend"])
    if not trend.empty:
        st.markdown(f"**Trend — last {len(trend)} {ov_kind.lower()}s**")
        long = trend.melt(id_vars="label", value_vars=["paid", "scheduled"], var_name="kind", value_name="amount")
        long["kind"] = long["kind"].map({"paid": "Paid", "scheduled": "Scheduled"})
        st.altair_chart(alt.Chart(long).mark_bar().encode(
            x=alt.X("label:N", title=None, sort=list(trend["label"]), axis=alt.Axis(labelAngle=0)),
            xOffset="kind:N", y=alt.Y("amount:Q", title="PHP"),
            color=alt.Color("kind:N", title=None, scale=alt.Scale(domain=["Paid", "Scheduled"], range=["#015088", "#9FB3C8"])),
            tooltip=[alt.Tooltip("label:N", title=ov_kind), alt.Tooltip("kind:N", title="What"), alt.Tooltip("amount:Q", title="Amount", format=",.2f")]),
            width="stretch")
    ck, pk = ov_cur["checks"], ov_prev["checks"]
    st.markdown("**Check activity**")
    st.dataframe(pd.DataFrame([{"Measure": "Checks issued", "This period": ck["issued"], "Previous": pk["issued"]},
                               {"Measure": "Checks cancelled / replaced", "This period": ck["voided"], "Previous": pk["voided"]}]), hide_index=True)

    st.markdown("**Where the money went**")
    ov_tracks = ov_cur["by_track"].copy()
    ov_prev_tracks = ov_prev["by_track"].set_index("track")
    ov_tracks["Total"] = ov_tracks["paid"] + ov_tracks["scheduled"]
    ov_tracks["Previous"] = [float(ov_prev_tracks.loc[t, "paid"] + ov_prev_tracks.loc[t, "scheduled"]) for t in ov_tracks["track"]]
    st.dataframe(ov_tracks.rename(columns={"track": "Track", "paid": "Paid", "scheduled": "Scheduled"}), hide_index=True,
                 column_config={c: money_col(c) for c in ("Paid", "Scheduled", "Total", "Previous")})

    st.markdown("**Bank and cash**")
    ov_bank_rows = bank_rows(ov["bank"])
    if ov_bank_rows:
        st.dataframe(pd.DataFrame(ov_bank_rows, columns=["Measure", "Figure"]), hide_index=True)
    st.caption(bank_note(ov["bank"]))

    if ov["watch"]:
        st.markdown("**Needs attention**")
        st.dataframe(pd.DataFrame(ov["watch"]).rename(columns={"item": "Item", "count": "Count", "detail": "Detail"}), hide_index=True)

    for mr_row, mr in ov.get("my_reports") or []:      # saved "My reports" included in the overall report
        st.markdown(f"**My report — {mr_row['name']}**")
        if isinstance(mr, str):
            st.warning(f"This saved report no longer runs: {mr}")
            continue
        st.caption(" · ".join(mr.criteria))
        if mr.table.empty:
            st.caption("Nothing in this period.")
        else:
            show_table(mr.table.head(50), kinds=mr.kinds)
            if len(mr.table) > 50:
                st.caption(f"First 50 of {len(mr.table):,} rows — open it under My reports for all of them.")

    if ov_version == "Full":
        if not ov_cur["payees"].empty:
            st.markdown("**Top payees**")
            show_table(ov_cur["payees"].head(10), {"scheduled": "Scheduled", "total": "Total"})
        st.markdown("**Cost of financing**")
        if ov_cur["interest"].empty:
            st.caption("No interest recorded in this period.")
        else:
            show_table(ov_cur["interest"].head(8), {"source": "Source", "name": "Facility"})
        st.markdown("**Payment behavior**")
        ov_b = ov_cur["behavior"]
        if not ov_b["count"]:
            st.caption("Not enough itemised supplier invoices in this period to measure how early or late checks were.")
        else:
            st.write(f"{ov_b['count']} supplier check(s) cleared — on average {abs(ov_b['avg_days']):.0f} day(s) {'after' if ov_b['avg_days'] > 0 else 'before'} the due date, {ov_b['on_time_pct']:.0f}% on time.")
        ov_up = ov["upcoming"]
        st.markdown(f"**Coming up — {ov_up['label']}**")
        if not ov_up["total"]:
            st.caption("Nothing scheduled.")
        else:
            st.write(f"{php(ov_up['total'])} falls due. " + ("Heaviest days: " + "; ".join(f"{d} ({php(a)})" for d, a in ov_up["peak_days"]) if ov_up["peak_days"] else ""))
            uc1, uc2 = st.columns(2)
            uc1.dataframe(pd.DataFrame([{"Track": k, "Amount due": v} for k, v in ov_up["by_track"].items()]), hide_index=True,
                          column_config={"Amount due": money_col("Amount due")})
            uc2.dataframe(pd.DataFrame([{"Week starting": w, "Amount due": a} for w, a in ov_up["by_week"]]), hide_index=True,
                          column_config={"Amount due": money_col("Amount due")})

    if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="ov_pdf_btn"):
        pdf_preview_and_download(
            generate_overview_pdf(ov, short=(ov_version == "Short")),
            f"overview_{ov_kind.lower()}_{ov['start']}_{ov_version.lower()}.pdf", key="ov_pdf_download", label="Overview PDF",
        )


def _duplicates() -> None:
    st.subheader("Possible duplicates — check everything on file")
    st.caption(
        "Looks across every supplier and batch, migrated ones included, for the same invoice number written differently "
        "(SI-00123 / 123), several invoices with the same amount on the same date, and batches with the same total. "
        "Not every finding is a mistake — if it is genuine, tick it and mark it as not a duplicate so it stops showing."
    )
    dup_show_dismissed = st.checkbox("Also show the ones I dismissed", key="rpt_dup_show_dismissed")
    dup_df = c_duplicates(include_dismissed=dup_show_dismissed)
    if dup_df.empty:
        st.success("Nothing suspicious found.")
        return
    dm1, dm2 = st.columns(2)
    dm1.metric("Findings", len(dup_df), border=True)
    dm2.metric("Amount involved", php(dup_df["amount"].sum()), border=True)
    dup_view = dup_df[["kind", "supplier", "detail", "amount", "where_found", "count", "dismissed", "dup_key"]].copy()
    dup_view.insert(0, "select", False)
    dup_edited = st.data_editor(
        dup_view.drop(columns=["dup_key"]), hide_index=True, key="rpt_dup_editor",
        disabled=["kind", "supplier", "detail", "amount", "where_found", "count", "dismissed"],
        column_config={
            "select": st.column_config.CheckboxColumn("Select"), "kind": "What matched", "supplier": "Supplier", "detail": "Details",
            "amount": money_col("Amount"), "where_found": "Batches", "count": "Items", "dismissed": "Dismissed",
        },
    )
    chosen_keys = [dup_view.loc[i, "dup_key"] for i in dup_edited.index[dup_edited["select"]]]
    dc1, dc2 = st.columns(2)
    if dc1.button("Mark selected as not a duplicate", icon=":material/check:", key="rpt_dup_dismiss_btn", disabled=not chosen_keys):
        dismiss_duplicates(chosen_keys)
        refresh()
    if dup_show_dismissed and dc2.button("Restore selected", icon=":material/undo:", key="rpt_dup_restore_btn", disabled=not chosen_keys):
        restore_duplicates(chosen_keys)
        refresh()
    export_buttons(dup_df.drop(columns=["dup_key"]), "possible_duplicates", key="dl_dup_scan",
                   labels={"kind": "What matched", "detail": "Details", "where_found": "Batches", "count": "Items", "dismissed": "Dismissed"})


def _schedule() -> None:
    st.subheader("Schedule of payments — all tracks")
    show = st.segmented_control("Show", SCHEDULE_VIEWS, required=True, key="sched_show",
                                **_default("sched_show", SCHEDULE_VIEWS[0]))
    if show == SCHEDULE_VIEWS[1]:
        _parked()
        return
    st.caption("Everything still owed, across all 4 tracks combined. Grouped by actual payment date — a due date falling on a weekend is paid the next banking day (Monday), so \"Due date\" (as originally scheduled) and \"Payment date\" (when it's actually paid/needs funding) can differ. \"Reference\" holds whichever identifier applies to that track (check number for Supplier/Lender, loan ref for Bank Loan, card name for Credit Card).")
    detail = c_schedule()
    if detail.empty:
        st.caption("Nothing scheduled.")
        return
    f1, f2, f3 = st.columns([1.2, 1.2, 1.6])
    track_filter = f1.multiselect("Track", options=sorted(detail["track"].unique()), key="sched_track_filter", placeholder="All tracks")
    months = sorted(detail["actual_payment_date"].astype(str).str[:7].unique())
    period = f2.selectbox("Payment date", options=["All dates"] + months + ["Custom range"],
                          format_func=lambda m: m if m in ("All dates", "Custom range") else _month_label(m), key="sched_period_filter")
    payees = f3.multiselect("Payee", options=sorted(detail["payee_name"].dropna().unique()), key="sched_payee_filter", placeholder="All payees")
    month = period if period not in ("All dates", "Custom range") else None
    date_from = date_to = None
    if month:
        date_from = date.fromisoformat(month + "-01")
        date_to = (pd.Timestamp(date_from) + pd.offsets.MonthEnd(0)).date()
    elif period == "Custom range":
        days = pd.to_datetime(detail["actual_payment_date"]).dt.date
        r1, r2 = st.columns(2)
        date_from = r1.date_input("From", value=days.min(), key="sched_from")
        date_to = r2.date_input("To", value=days.max(), key="sched_to")
    shown = filter_schedule(detail, track_filter, date_from, date_to, payees)

    bits = ([", ".join(track_filter)] if track_filter else []) + ([_month_label(month)] if month else [])
    if period == "Custom range":
        bits.append(f"{date_from:%b %d, %Y} to {date_to:%b %d, %Y}")
    if payees:
        bits.append(", ".join(payees) if len(payees) <= 3 else f"{len(payees)} payees")
    st.caption(f"Exporting {' — '.join(bits)} only." if bits else "The tables, CSV / Excel and PDF below export exactly what's filtered here.")
    if shown.empty:
        st.caption("Nothing scheduled for this filter.")
        return
    stem_bits = [_slug(t) for t in track_filter] + ([month] if month else []) + ([f"{date_from}_{date_to}"] if period == "Custom range" else []) \
        + (["selected_payees"] if payees else [])
    stem = "payment_schedule_" + ("_".join(stem_bits) if stem_bits else "all_tracks")
    _daily_view(shown, day_label="Payment date", count_label="# Items", what="items", key="dl_sched", file_stem=stem,
                pdf=lambda: generate_payment_schedule_pdf(track_filter or None, month, None if month else date_from, None if month else date_to, payees or None),
                pdf_label="Schedule of Payments PDF")


def _parked() -> None:
    st.caption("Every check already recorded but still waiting on a check number — a number is assigned at PRINT time, not issue time. "
               "Grouped by check date (the Checks and checkbook page's Current check run is the live, same-scope view for actually printing these).")
    detail = c_parked()
    if detail.empty:
        st.caption("Nothing parked — every issued check has been printed.")
        return
    months = sorted(detail["actual_payment_date"].astype(str).str[:7].unique())
    month = st.selectbox("Check date", options=["All months"] + months, format_func=lambda m: m if m == "All months" else _month_label(m),
                         key="parked_month_filter")
    shown = detail if month == "All months" else detail[detail["actual_payment_date"].astype(str).str.startswith(month)]
    if shown.empty:
        st.caption("Nothing parked for this filter.")
        return
    _daily_view(shown, day_label="Check date", count_label="# Checks", what="checks", key="dl_parked",
                file_stem=f"parked_checks_{month if month != 'All months' else 'all'}",
                pdf=lambda: generate_parked_checks_pdf(month if month != "All months" else None), pdf_label="Parked Checks PDF")


def _cleared_checks() -> None:
    """Checks the bank has cleared, from the bank reconciliation (owner, 2026-09-29). The bank's clearing date is the truth."""
    st.subheader("Cleared checks — from the bank")
    st.caption("Every check the imported Metrobank statements show as cleared: the bank's date, how many days after our check date, and how "
               "the bank line was matched. The bank's amount is shown when it differs from our record (an adjustment on Bank and cash).")
    span = cleared_span()
    if span is None:
        st.info("No cleared checks yet. Import a Metrobank statement on Bank and cash — matched checks appear here.")
        return
    f1, f2, f3 = st.columns([1, 1, 2])
    date_from = f1.date_input("Cleared from", value=span[0], min_value=span[0], max_value=span[1], key="rpt_clr_from")
    date_to = f2.date_input("Cleared to", value=span[1], min_value=span[0], max_value=span[1], key="rpt_clr_to")
    everyone = cleared_checks()
    payees = dict(sorted({int(c): p for c, p in zip(everyone["creditor_id"], everyone["payee"])}.items(), key=lambda kv: kv[1].lower()))
    payee = f3.selectbox("Payee", [None] + list(payees), format_func=lambda c: "All payees" if c is None else payees[c], key="rpt_clr_payee")
    df = cleared_checks(date_from, date_to, payee)
    if df.empty:
        st.caption("No cleared checks for this filter.")
        return
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Checks cleared", len(df), border=True)
    m2.metric("Total (our records)", php(float(df["amount"].sum())), border=True)
    m3.metric("Average days to clear", f"{df['days_to_clear'].mean():.1f}", border=True,
              help="Days from our check date to the date the bank cleared it. Negative = the bank cleared it before its check date.")
    diff = df[df["difference"].abs() >= 0.005]
    m4.metric("Different from the bank", len(diff), border=True, help="Checks where the bank's amount differs from our record.")
    labels = {"cleared_date": "Cleared by bank", "check_number": "Check #", "amount": "Amount (ours)", "check_date": "Check date",
              "days_to_clear": "Days to clear", "reference": "Batch / loan note", "bank_amount": "Bank paid", "difference": "Difference",
              "matched_how": "Matched", "status": "Our status", "payee": "Payee"}
    kinds = {"cleared_date": "date", "check_date": "date", "amount": "money", "bank_amount": "money", "difference": "money", "days_to_clear": "count"}
    st.markdown("**Cleared checks**")
    show_table(df, labels, hide=["check_id", "creditor_id"], kinds=kinds)
    w1, w2 = st.columns(2)
    with w1:
        st.markdown("**By payee**")
        show_table(by_payee(df), {"payee": "Payee", "checks": "Checks", "total": "Total", "avg_days": "Avg days to clear"},
                   kinds={"total": "money", "checks": "count", "avg_days": "number"})
    with w2:
        st.markdown("**By week**")
        show_table(by_week(df), {"week_start": "Week of", "checks": "Checks", "total": "Total"},
                   kinds={"week_start": "date", "total": "money", "checks": "count"})
    export_buttons({"Cleared checks": df.drop(columns=["check_id", "creditor_id"]), "By payee": by_payee(df), "By week": by_week(df)},
                   "cleared_checks", key="dl_cleared", labels=labels, what="cleared checks", kinds=kinds)


def _supplier_history() -> None:
    st.subheader("Supplier — invoice & check history")
    st.caption("Every order statement (batch), invoice, and check for one supplier — pick a supplier, narrow by date/status, and pull a Statement of Account whenever you're asked for this supplier's transaction history.")
    sup_options = creditor_options_all("SUPPLIER")
    if not sup_options:
        st.caption("No suppliers on file yet.")
        return
    sup_id = st.selectbox("Supplier", options=list(sup_options), format_func=lambda cid: sup_options[cid], key="rpt_supplier_pick")
    sup_statements, sup_invoices, sup_checks = c_supplier_history(sup_id)
    sup_date_from, sup_date_to = history_date_range(
        [sup_statements["created_at"] if not sup_statements.empty else None,
         sup_invoices["invoice_date"] if not sup_invoices.empty else None,
         sup_checks["check_date"] if not sup_checks.empty else None],
        key_prefix=f"rpt_sup_date_{sup_id}",
    )
    sup_statements_f = filter_by_date(sup_statements, "created_at", sup_date_from, sup_date_to)
    sup_invoices_f = filter_by_date(sup_invoices, "invoice_date", sup_date_from, sup_date_to)
    sup_checks_f = filter_by_date(sup_checks, "check_date", sup_date_from, sup_date_to)
    sup_invoices_live = sup_invoices_f[sup_invoices_f["status"] != "Cancelled"] if not sup_invoices_f.empty else sup_invoices_f
    sup_checks_live = sup_checks_f[sup_checks_f["status"] == "Active"] if not sup_checks_f.empty else sup_checks_f
    m1, m2, m3 = st.columns(3)
    m1.metric("Batches (order statements)", len(sup_statements_f), border=True)
    m2.metric("Total invoiced", php(sup_invoices_live["amount"].sum() if not sup_invoices_live.empty else 0), border=True, help="Excludes cancelled invoices. Reflects the date range above, not the status filters below.")
    m3.metric("Total paid (checks)", php(sup_checks_live["amount"].sum() if not sup_checks_live.empty else 0), border=True, help="Active checks only — excludes cancelled/replaced checks. Reflects the date range above, not the status filters below.")
    inv_labels = {"amount": "Net amount"}

    st.markdown("**Order statements (batches)**")
    sup_statements_shown = filter_by_status(sup_statements_f, "status", key=f"rpt_sup_batch_status_{sup_id}")
    if sup_statements_shown.empty:
        st.caption("No batches for this period/status.")
    else:
        show_table(sup_statements_shown)
    st.markdown("**Invoices**")
    sup_invoices_shown = filter_by_status(sup_invoices_f, "status", key=f"rpt_sup_invoice_status_{sup_id}")
    if sup_invoices_shown.empty:
        st.caption("No invoices for this period/status.")
    else:
        show_table(sup_invoices_shown, inv_labels)
    st.markdown("**Checks issued**")
    sup_checks_shown = filter_by_status(sup_checks_f, "status", key=f"rpt_sup_check_status_{sup_id}")
    if sup_checks_shown.empty:
        st.caption("No checks issued for this period/status.")
    else:
        show_table(sup_checks_shown)

    sup_slug = _slug(sup_options[sup_id])
    export_buttons({"Batches": sup_statements_shown, "Invoices": sup_invoices_shown, "Checks": sup_checks_shown}, f"supplier_{sup_slug}_history",
                   key="dl_sup", labels=inv_labels, what="history")
    st.caption("The Excel file has one sheet each for batches, invoices and checks; the CSV holds the batches.")
    st.divider()
    st.markdown("**PDF — Statement of Account**")
    sup_group_by_batch = st.checkbox("Break out each batch separately (instead of one combined statement)", key=f"rpt_sup_group_{sup_id}")
    if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="sup_pdf_btn"):
        pdf_preview_and_download(
            generate_supplier_statement_pdf(sup_options[sup_id], sup_statements_shown, sup_invoices_shown, sup_checks_shown,
                                            sup_date_from, sup_date_to, group_by_batch=sup_group_by_batch),
            f"supplier_{sup_slug}_statement.pdf", key="sup_pdf_download", label="Supplier Statement of Account PDF",
        )


def _rundown() -> None:
    st.subheader("Supplier — rundown (paid / partial / pending)")
    view = st.segmented_control("View", RUNDOWN_VIEWS, required=True, key="rpt_rundown_view",
                                **_default("rpt_rundown_view", RUNDOWN_VIEWS[0]))
    (_batch_rundown if view == "By batch" else _invoice_rundown)()


def _batch_rundown() -> None:
    st.caption("Every issued batch (order statement) with how much is paid, how much is covered by post-dated checks, and what is still open. Paid = checks whose date has passed (a check dated today is still post-dated). Pick one supplier or all of them.")
    rd_options = creditor_options_all("SUPPLIER")
    rd_choice = st.selectbox("Supplier", options=[None] + list(rd_options), format_func=lambda cid: "All suppliers" if cid is None else rd_options[cid], key="rpt_rundown_pick")
    rd_all = c_rundown(rd_choice)
    if not rd_all.empty and rd_all["first_check"].notna().any():
        rd_min, rd_max = date.fromisoformat(rd_all["first_check"].min()), date.fromisoformat(rd_all["last_check"].max())
    else:
        rd_min = rd_max = manila_today()
    rd_from_col, rd_to_col = st.columns(2)
    rd_from = rd_from_col.date_input("From", value=rd_min, key=f"rpt_rundown_from_{rd_choice}", help="Batches with a check dated on or after this day.")
    rd_to = rd_to_col.date_input("To", value=rd_max, key=f"rpt_rundown_to_{rd_choice}", help="Batches with a check dated on or before this day. Amounts shown are for the whole batch.")
    rd_df = filter_rundown(rd_all, rd_from, rd_to)              # narrowed in memory: one query, not two
    if rd_df.empty:
        st.caption("No issued batches for this selection.")
        return
    for col, status_name in zip(st.columns(3), STATUS_ORDER):
        part = rd_df[rd_df["status"] == status_name]
        col.metric(status_name, f"{len(part)} batch(es)", border=True)
        col.caption(f"Net total {php(part['net_total'].sum())} · still owed {php(part['balance'].sum())}")
    rd_picked = st.multiselect("Show", options=STATUS_ORDER, default=STATUS_ORDER, key="rpt_rundown_status")
    rd_view = rd_df[rd_df["status"].isin(rd_picked)].copy()
    rd_view["running_balance"] = rd_view["balance"].groupby(rd_view["supplier"]).cumsum()
    cols = ["supplier", "batch", "status", "first_check", "last_check", "net_total", "paid", "scheduled", "open", "balance", "running_balance"]
    sheets = {"Batches": rd_view[cols]}
    if rd_choice is None and not rd_view.empty:
        st.markdown("**Per supplier**")
        rd_sum = _status_pivot(rd_view, "net_total")
        st.dataframe(rd_sum, hide_index=True, column_config={"supplier": "Supplier", **{n: money_col(n) for n in STATUS_ORDER + ["Total"]}})
        sheets["Per supplier"] = rd_sum
    st.markdown("**Batches**")
    show_table(rd_view[cols])
    export_buttons(sheets, "batch_rundown", key="dl_rundown")
    if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="batch_rundown_pdf_btn"):
        rd_rows = [[r.supplier, r.batch, r.status, str(r.first_check or "—")[:10], str(r.last_check or "—")[:10], _pdf_money(r.net_total), _pdf_money(r.paid), _pdf_money(r.scheduled), _pdf_money(r.balance), _pdf_money(r.running_balance)] for r in rd_view.itertuples()]
        rd_summary = [(n, int((rd_view["status"] == n).sum()), float(rd_view.loc[rd_view["status"] == n, "balance"].sum())) for n in STATUS_ORDER]
        pdf_preview_and_download(
            generate_rundown_pdf(
                "Batch rundown", "All suppliers" if rd_choice is None else rd_options[rd_choice],
                ["Supplier", "Batch", "Status", "First check", "Last check", "Net total", "Paid", "Post-dated", "Still owed", "Running balance"], rd_rows,
                [0.16, 0.13, 0.08, 0.07, 0.07, 0.09, 0.09, 0.09, 0.10, 0.12], [5, 6, 7, 8, 9], rd_summary, f"Checks dated {rd_from} to {rd_to}  ·  amounts in the summary are still owed",
            ),
            "batch_rundown.pdf", key="batch_rundown_pdf_download", label="Batch Rundown PDF",
        )


def _invoice_rundown() -> None:
    st.caption("Every invoice with its status — Paid, Partially Paid or Pending. Pick one supplier or all of them, and narrow by invoice date. For payments per batch, use the batch rundown.")
    ir_options = creditor_options_all("SUPPLIER")
    ir_choice = st.selectbox("Supplier", options=[None] + list(ir_options), format_func=lambda cid: "All suppliers" if cid is None else ir_options[cid], key="rpt_inv_rundown_pick")
    ir_all = c_invoice_rundown(ir_choice)
    if ir_all.empty:
        st.caption("No invoices on file for this selection. (Migrated history was loaded as batches only — see the batch rundown for those.)")
        return
    ir_min, ir_max = date.fromisoformat(str(ir_all["invoice_date"].min())[:10]), date.fromisoformat(str(ir_all["invoice_date"].max())[:10])
    ir_from_col, ir_to_col = st.columns(2)
    ir_from = ir_from_col.date_input("From", value=ir_min, key=f"rpt_inv_from_{ir_choice}")
    ir_to = ir_to_col.date_input("To", value=ir_max, key=f"rpt_inv_to_{ir_choice}")
    ir_df = c_invoice_rundown(ir_choice, ir_from, ir_to)
    for col, status_name in zip(st.columns(3), STATUS_ORDER):
        part = ir_df[ir_df["status"] == status_name]
        col.metric(status_name, f"{len(part)} invoice(s)", border=True)
        col.caption(f"Amount {php(part['amount'].sum())}")
    ir_picked = st.multiselect("Show", options=STATUS_ORDER, default=STATUS_ORDER, key="rpt_inv_rundown_status")
    ir_view = ir_df[ir_df["status"].isin(ir_picked)].copy()
    # Running balance = what is still owed, adding up row by row within each supplier (Paid rows add nothing).
    ir_view["running_balance"] = ir_view["amount"].where(ir_view["status"] != "Paid", 0.0).groupby(ir_view["supplier"]).cumsum()
    labels = {"amount": "Net amount"}
    sheets = {"Invoices": ir_view}
    if ir_choice is None and not ir_view.empty:
        st.markdown("**Per supplier**")
        ir_sum = _status_pivot(ir_view, "amount")
        st.dataframe(ir_sum, hide_index=True, column_config={"supplier": "Supplier", **{n: money_col(n) for n in STATUS_ORDER + ["Total"]}})
        sheets["Per supplier"] = ir_sum
    st.markdown("**Invoices**")
    show_table(ir_view, labels)
    export_buttons(sheets, "invoice_rundown", key="dl_inv_rundown", labels=labels)
    if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="inv_rundown_pdf_btn"):
        ir_rows = [[r.supplier, r.invoice, r.batch or "—", str(r.invoice_date)[:10], str(r.due_date)[:10], _pdf_money(r.amount), r.status, _pdf_money(r.running_balance)] for r in ir_view.itertuples()]
        ir_summary = [(n, int((ir_view["status"] == n).sum()), float(ir_view.loc[ir_view["status"] == n, "amount"].sum())) for n in STATUS_ORDER]
        pdf_preview_and_download(
            generate_rundown_pdf(
                "Invoice rundown", "All suppliers" if ir_choice is None else ir_options[ir_choice],
                ["Supplier", "Invoice", "Batch", "Invoice date", "Due date", "Net amount", "Status", "Running balance"], ir_rows,
                [0.20, 0.09, 0.20, 0.09, 0.09, 0.11, 0.09, 0.13], [5, 7], ir_summary, f"Invoice dates {ir_from} to {ir_to}",
            ),
            "invoice_rundown.pdf", key="inv_rundown_pdf_download", label="Invoice Rundown PDF",
        )


def _lender_history() -> None:
    st.subheader("Private lender — loan note & check history")
    st.caption("Every loan note and every check for one private lender — pick a lender, narrow by date/status, and pull a Statement of Account whenever you're asked for this lender's transaction history.")
    lender_options = creditor_options_all("LENDER")
    if not lender_options:
        st.caption("No private lenders on file yet.")
        return
    lender_id = st.selectbox("Lender", options=list(lender_options), format_func=lambda cid: lender_options[cid], key="rpt_lender_pick")
    ln_notes, ln_checks = c_lender_history(lender_id)
    ln_date_from, ln_date_to = history_date_range(
        [ln_notes["issue_date"] if not ln_notes.empty else None, ln_checks["check_date"] if not ln_checks.empty else None],
        key_prefix=f"rpt_len_date_{lender_id}",
    )
    ln_notes_f = filter_by_date(ln_notes, "issue_date", ln_date_from, ln_date_to)
    ln_checks_f = filter_by_date(ln_checks, "check_date", ln_date_from, ln_date_to)
    ln_checks_live = ln_checks_f[ln_checks_f["status"] == "Active"] if not ln_checks_f.empty else ln_checks_f
    m1, m2, m3 = st.columns(3)
    m1.metric("Loan notes", len(ln_notes_f), border=True)
    m2.metric("Outstanding (uncleared)", php(ln_notes_f["outstanding_uncleared"].sum() if not ln_notes_f.empty else 0), border=True)
    m3.metric("Total paid (checks)", php(ln_checks_live["amount"].sum() if not ln_checks_live.empty else 0), border=True, help="Active checks only — excludes cancelled/replaced checks. Reflects the date range above, not the status filters below.")

    st.markdown("**Loan notes**")
    ln_notes_shown = filter_by_status(ln_notes_f, "status", key=f"rpt_len_note_status_{lender_id}")
    if ln_notes_shown.empty:
        st.caption("No loan notes for this period/status.")
    else:
        show_table(ln_notes_shown, hide=["loan_note_id"])
    st.markdown("**Checks issued**")
    ln_checks_shown = filter_by_status(ln_checks_f, "status", key=f"rpt_len_check_status_{lender_id}")
    if ln_checks_shown.empty:
        st.caption("No checks issued for this period/status.")
    else:
        show_table(ln_checks_shown)
    lender_slug = _slug(lender_options[lender_id])
    export_buttons({"Loan notes": ln_notes_shown.drop(columns=["loan_note_id"], errors="ignore"), "Checks": ln_checks_shown},
                   f"lender_{lender_slug}_history", key="dl_len", what="history")
    st.caption("The Excel file has one sheet each for loan notes and checks; the CSV holds the loan notes.")
    st.divider()
    st.markdown("**PDF — Statement of Account**")
    ln_group_by_note = st.checkbox("Break out each loan note separately (instead of one combined statement)", key=f"rpt_len_group_{lender_id}")
    if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="len_pdf_btn"):
        pdf_preview_and_download(
            generate_lender_statement_pdf(lender_options[lender_id], ln_notes_shown, ln_checks_shown, ln_date_from, ln_date_to, group_by_note=ln_group_by_note),
            f"lender_{lender_slug}_statement.pdf", key="len_pdf_download", label="Lender Statement of Account PDF",
        )


def _bank_loan_history() -> None:
    st.subheader("Bank loan — installment history")
    st.caption("Every installment for one bank loan, paid or outstanding — pick a loan, narrow by date/status, and pull a Statement of Account whenever you're asked for this loan's history.")
    bl_options = bank_loan_options()
    if not bl_options:
        st.caption("No bank loans on file yet.")
        return
    bl_id = st.selectbox("Bank loan", options=list(bl_options), format_func=lambda lid: bl_options[lid], key="rpt_bank_loan_pick")
    bl_info, bl_installments = c_bank_loan_history(bl_id)
    bl_row = bl_info.iloc[0]
    i1, i2, i3, i4 = st.columns(4)
    i1.metric("Bank", bl_row["bank_name"], border=True)
    i2.metric("Principal", php(bl_row["principal_amount"]), border=True)
    i3.metric("Status", bl_row["status"], border=True)
    i4.metric("Maturity", bl_row["maturity_date"], border=True)
    bl_date_from, bl_date_to = history_date_range([bl_installments["due_date"] if not bl_installments.empty else None], key_prefix=f"rpt_bl_date_{bl_id}")
    bl_installments_f = filter_by_date(bl_installments, "due_date", bl_date_from, bl_date_to)
    st.markdown("**Installments**")
    bl_installments_shown = filter_by_status(bl_installments_f, "status", key=f"rpt_bl_status_{bl_id}")
    if bl_installments_shown.empty:
        st.caption("No installments for this period/status.")
    else:
        show_table(bl_installments_shown)
        export_buttons(bl_installments_shown, f"bank_loan_{_slug(bl_row['loan_ref'])}_installments", key="dl_bl_installments", what="installments")
    st.divider()
    st.markdown("**PDF — Statement of Account**")
    if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="bl_pdf_btn"):
        pdf_preview_and_download(
            generate_bank_loan_statement_pdf(bl_row, bl_installments_shown, bl_date_from, bl_date_to),
            f"bank_loan_{_slug(bl_row['loan_ref'])}_statement.pdf", key="bl_pdf_download", label="Bank Loan Statement of Account PDF",
        )


def _card_history() -> None:
    st.subheader("Credit card — statement history")
    st.caption("Grouped by whose card it is — one cardholder can have several cards. Pick a cardholder, then drill into one of their cards or combine all of them, narrow by date/status, and pull a Statement of Account whenever you're asked for this card's history.")
    cc_holder_options = cardholder_options_with_cards()
    if not cc_holder_options:
        st.caption("No credit cards on file yet.")
        return
    cc_holder_id = st.selectbox("Cardholder", options=list(cc_holder_options), format_func=lambda hid: cc_holder_options[hid], key="rpt_card_holder_pick")
    cc_holder_slug = _slug(cc_holder_options[cc_holder_id])
    cc_hist_options = credit_card_options_for_holder(cc_holder_id)
    cc_hist_id = st.selectbox("Credit card", options=["ALL"] + list(cc_hist_options),
                              format_func=lambda cid: f"All cards ({len(cc_hist_options)})" if cid == "ALL" else cc_hist_options[cid],
                              key=f"rpt_card_pick_{cc_holder_id}")
    if cc_hist_id == "ALL":
        cc_cards_info, cc_statements = c_cardholder_history(cc_holder_id)
        cc_date_from, cc_date_to = history_date_range([cc_statements["due_date"] if not cc_statements.empty else None], key_prefix=f"rpt_cc_all_date_{cc_holder_id}")
        cc_statements_f = filter_by_date(cc_statements, "due_date", cc_date_from, cc_date_to)
        cc_unpaid_live = cc_statements_f[~cc_statements_f["status"].isin(["Paid", "Cancelled"])] if not cc_statements_f.empty else cc_statements_f
        m1, m2, m3 = st.columns(3)
        m1.metric("Cards", len(cc_cards_info), border=True)
        m2.metric("Combined credit limit", php(cc_cards_info["credit_limit"].sum() if not cc_cards_info.empty else 0), border=True)
        m3.metric("Total unpaid (all cards)", php(cc_unpaid_live["total_amount_due"].sum() if not cc_unpaid_live.empty else 0), border=True, help="Excludes Paid/Cancelled statements. Reflects the date range above, not the status filter below.")
        st.markdown("**Cards**")
        if cc_cards_info.empty:
            st.caption("No cards for this cardholder.")
        else:
            show_table(cc_cards_info, hide=["card_id"])
        st.markdown("**Statements (all cards)**")
        cc_statements_shown = filter_by_status(cc_statements_f, "status", key=f"rpt_cc_all_status_{cc_holder_id}")
        if cc_statements_shown.empty:
            st.caption("No statements for this period/status.")
        else:
            show_table(cc_statements_shown)
            export_buttons(cc_statements_shown, f"cardholder_{cc_holder_slug}_all_cards_statements", key="dl_cc_all_statements", what="statements")
        st.divider()
        st.markdown("**PDF — Statement of Account**")
        cc_group_by_card = st.checkbox("Break out each card separately (instead of one combined statement)", key=f"rpt_cc_group_{cc_holder_id}")
        if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="cc_all_pdf_btn"):
            pdf_preview_and_download(
                generate_cardholder_statement_pdf(cc_holder_options[cc_holder_id], cc_cards_info, cc_statements_shown, cc_date_from, cc_date_to, group_by_card=cc_group_by_card),
                f"cardholder_{cc_holder_slug}_all_cards_statement.pdf", key="cc_all_pdf_download", label="Cardholder Statement of Account PDF",
            )
        return
    cc_info, cc_statements = c_card_history(cc_hist_id)
    cc_row = cc_info.iloc[0]
    i1, i2, i3 = st.columns(3)
    i1.metric("Issuer", cc_row["issuer"], border=True)
    i2.metric("Credit limit", php(cc_row["credit_limit"]), border=True)
    i3.metric("Cardholder", cc_row["cardholder"] or "—", border=True)
    cc_date_from, cc_date_to = history_date_range([cc_statements["due_date"] if not cc_statements.empty else None], key_prefix=f"rpt_cc_date_{cc_hist_id}")
    cc_statements_f = filter_by_date(cc_statements, "due_date", cc_date_from, cc_date_to)
    st.markdown("**Statements**")
    cc_statements_shown = filter_by_status(cc_statements_f, "status", key=f"rpt_cc_status_{cc_hist_id}")
    card_slug = _slug(f"{cc_row['card_name']}_{cc_row['card_number_last4']}")
    if cc_statements_shown.empty:
        st.caption("No statements for this period/status.")
    else:
        show_table(cc_statements_shown)
        export_buttons(cc_statements_shown, f"card_{card_slug}_statements", key="dl_cc_statements", what="statements")
    st.divider()
    st.markdown("**PDF — Statement of Account**")
    if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="cc_pdf_btn"):
        pdf_preview_and_download(
            generate_credit_card_statement_pdf(cc_row, cc_statements_shown, cc_date_from, cc_date_to),
            f"card_{card_slug}_statement.pdf", key="cc_pdf_download", label="Credit Card Statement of Account PDF",
        )


def _rollups() -> None:
    st.subheader("Master rollups — aging and exposure")
    view = st.segmented_control("View", ROLLUP_VIEWS, required=True, key="rpt_rollup_view",
                                **_default("rpt_rollup_view", ROLLUP_VIEWS[0]))
    {"Supplier AP aging": _aging, "Private lender exposure": _lender_master, "Bank loan servicing": _bank_loan_master}[view]()


def _aging() -> None:
    st.caption("Every supplier's open invoices, bucketed by how overdue they are, across the whole company.")
    aging_df = c_aging()
    if aging_df.empty:
        st.caption("No open supplier invoices.")
        return
    show_table(aging_df)
    export_buttons(aging_df, "supplier_ap_aging", key="dl_aging")
    if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="aging_pdf_btn"):
        pdf_preview_and_download(
            generate_rundown_pdf(
                "Supplier AP aging", "All suppliers",
                ["Supplier", "Open", "Total unpaid", "Current", "1-30 days", "31-60 days", "60+ days"],
                [[r.supplier_name, int(r.open_invoices), _pdf_money(r.total_unpaid), _pdf_money(r.current_due), _pdf_money(r.days_1_30), _pdf_money(r.days_31_60), _pdf_money(r.days_over_60)] for r in aging_df.itertuples()],
                [0.32, 0.07, 0.14, 0.12, 0.12, 0.12, 0.11], [1, 2, 3, 4, 5, 6], [], f"As of {manila_today()}",
            ),
            "supplier_ap_aging.pdf", key="aging_pdf_download", label="Supplier AP Aging PDF",
        )


def _lender_master() -> None:
    st.caption("Every private lender's loan notes and current uncleared exposure, across the whole company.")
    df = c_lender_master()
    if df.empty:
        st.caption("No private lender notes on file.")
        return
    show_table(df)
    export_buttons(df, "private_lender_exposure", key="dl_lender_master")
    if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="lender_master_pdf_btn"):
        pdf_preview_and_download(
            generate_rundown_pdf(
                "Private lender exposure", "All private lenders",
                ["Lender", "Note", "Issue date", "Principal", "Interest", "Total payable", "Outstanding", "Status"],
                [[r.lender_name, r.note_ref, str(r.issue_date)[:10], _pdf_money(r.principal_amount), _pdf_money(r.total_interest), _pdf_money(r.total_payable), _pdf_money(r.outstanding_uncleared), r.status] for r in df.itertuples()],
                [0.2, 0.13, 0.09, 0.11, 0.1, 0.12, 0.11, 0.14], [3, 4, 5, 6], [], f"As of {manila_today()}",
            ),
            "private_lender_exposure.pdf", key="lender_master_pdf_download", label="Private Lender Exposure PDF",
        )


def _bank_loan_master() -> None:
    st.caption("Every bank loan and its current outstanding balance, across the whole company.")
    df = c_bank_loan_master()
    if df.empty:
        st.caption("No bank loans on file.")
        return
    show_table(df)
    export_buttons(df, "bank_loan_servicing", key="dl_bl_master")
    if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="bl_master_pdf_btn"):
        pdf_preview_and_download(
            generate_rundown_pdf(
                "Bank loan servicing", "All bank loans",
                ["Bank", "Loan ref", "Start", "Maturity", "Principal", "Outstanding", "Status"],
                [[r.bank_name, r.loan_ref, str(r.start_date or "—")[:10], str(r.maturity_date or "—")[:10], _pdf_money(r.principal_amount) if pd.notna(r.principal_amount) else "—", _pdf_money(r.outstanding), r.status] for r in df.itertuples()],
                [0.16, 0.32, 0.09, 0.09, 0.12, 0.12, 0.10], [4, 5], [], f"As of {manila_today()}",
            ),
            "bank_loan_servicing.pdf", key="bl_master_pdf_download", label="Bank Loan Servicing PDF",
        )


def _lists() -> None:
    from views.lists_and_decisions import render_lists

    render_lists()


def _decisions() -> None:
    from views.lists_and_decisions import render_decisions

    render_decisions()


REPORTS = dict(zip(REPORT_OPTIONS, [_overview, _duplicates, _schedule, _cleared_checks, _supplier_history, _rundown, _lender_history,
                                    _bank_loan_history, _card_history, _rollups, _lists, _decisions]))


def render() -> None:
    page_header("Reports")
    mode = st.segmented_control("Reports", ["Standard reports", "My reports"], default="Standard reports", required=True, key="rpt_mode",
                                label_visibility="collapsed")
    if mode == "My reports":                          # the owner's own saved reports (views/my_reports.py, 2026-09-28)
        from views.my_reports import render as render_my_reports

        render_my_reports()
        return
    st.caption("Pick a report — its filters, tables, and exports appear below, one report at a time.")
    old = st.session_state.get("report_type_picker")
    if old in OLD_REPORTS:                            # a report that is now part of another one
        new, sub = OLD_REPORTS[old]
        st.session_state["report_type_picker"] = new
        st.session_state.update(sub)
    elif old is not None and old not in REPORT_OPTIONS:
        st.session_state["report_type_picker"] = REPORT_OPTIONS[0]
    report_choice = st.selectbox("Report", REPORT_OPTIONS, key="report_type_picker")
    st.divider()
    REPORTS[report_choice]()
