"""Reports screen (moved out of app.py unchanged, 2026-09-27). app.py calls render() only while this tab is open."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from datetime import date
from engines.account_history_pdf_service import generate_bank_loan_statement_pdf
from engines.account_history_pdf_service import generate_cardholder_statement_pdf
from engines.account_history_pdf_service import generate_credit_card_statement_pdf
from engines.account_history_pdf_service import generate_lender_statement_pdf
from engines.account_history_pdf_service import generate_rundown_pdf
from engines.account_history_pdf_service import generate_supplier_statement_pdf
from engines.common import _pdf_money
from engines.common import filter_by_date
from engines.common import php
from engines.duplicate_scan_service import dismiss as dismiss_duplicates
from engines.duplicate_scan_service import restore as restore_duplicates
from engines.duplicate_scan_service import scan_possible_duplicates
from engines.overview_pdf_service import generate_overview_pdf
from engines.overview_report_service import PERIOD_KINDS
from engines.overview_report_service import get_overview
from engines.payment_schedule_report_service import generate_parked_checks_pdf
from engines.payment_schedule_report_service import generate_payment_schedule_pdf
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
    bank_loan_options,
    cardholder_options_with_cards,
    credit_card_options_for_holder,
    creditor_options_all,
    filter_by_status,
    history_date_range,
    pdf_preview_and_download,
    refresh,
)


def render() -> None:
    st.header("Reports")
    st.caption("Pick a report — its filters, tables, and exports appear below, one report at a time.")

    REPORT_OPTIONS = [
        "Overview — highlights & insights",
        "Possible duplicates — check everything on file",
        "Schedule of payments — all tracks",
        "Parked checks (not yet printed)",
        "Supplier — invoice & check history",
        "Supplier — batch rundown (paid / partial / pending)",
        "Supplier — invoice rundown (paid / partial / pending)",
        "Private lender — loan note & check history",
        "Bank loan — installment history",
        "Credit card — statement history",
        "Master rollup — supplier AP aging",
        "Master rollup — private lender exposure",
        "Master rollup — bank loan servicing",
    ]
    report_choice = st.selectbox("Report", REPORT_OPTIONS, key="report_type_picker")
    st.divider()

    if report_choice == "Overview — highlights & insights":
        st.subheader("Overview — highlights & insights")
        st.caption("What happened in a day, week, month, quarter or year, compared with the period before it — highlights first, then the numbers behind them. Pick the short version for a quick read or the full one for the detail.")
        ov1, ov2, ov3 = st.columns([2, 2, 2])
        ov_kind = ov1.selectbox("Period", PERIOD_KINDS, index=2, key="rpt_ov_kind")
        ov_anchor = ov2.date_input("Any date inside the period", value=date.today(), key="rpt_ov_anchor")
        ov_version = ov3.radio("Version", ["Short", "Full"], horizontal=True, key="rpt_ov_version")
        ov = get_overview(ov_kind, ov_anchor)
        ov_cur, ov_prev = ov["current"], ov["previous"]
        st.markdown(f"#### {ov['label']}  ·  compared with {ov['prev_label']}")

        st.markdown("**Highlights**")
        for line in ov["highlights"]:
            st.markdown(f"- {line}")

        def _ov_delta(now, before):
            return None if not before else f"{(now - before) / before * 100:+.0f}%"

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Paid out", php(ov_cur["paid"]), _ov_delta(ov_cur["paid"], ov_prev["paid"]), border=True, help="Checks, loan installments and card payments whose date has arrived.")
        m2.metric("Scheduled, not yet due", php(ov_cur["scheduled"]), _ov_delta(ov_cur["scheduled"], ov_prev["scheduled"]), border=True)
        m3.metric("Interest cost", php(ov_cur["interest_total"]), _ov_delta(ov_cur["interest_total"], ov_prev["interest_total"]), delta_color="inverse", border=True)
        m4.metric("Total outstanding today", php(ov["outstanding_today"]), border=True, help="All four tracks, as on the Dashboard.")

        st.markdown("**Where the money went**")
        ov_tracks = ov_cur["by_track"].copy()
        ov_prev_tracks = ov_prev["by_track"].set_index("track")
        ov_tracks["Total"] = ov_tracks["paid"] + ov_tracks["scheduled"]
        ov_tracks["Previous"] = [float(ov_prev_tracks.loc[t, "paid"] + ov_prev_tracks.loc[t, "scheduled"]) for t in ov_tracks["track"]]
        st.dataframe(
            ov_tracks.rename(columns={"track": "Track", "paid": "Paid", "scheduled": "Scheduled"}), hide_index=True,
            column_config={c: st.column_config.NumberColumn(c, format="₱ %.2f") for c in ("Paid", "Scheduled", "Total", "Previous")},
        )

        if ov["watch"]:
            st.markdown("**Needs attention**")
            st.dataframe(pd.DataFrame(ov["watch"]).rename(columns={"item": "Item", "count": "Count", "detail": "Detail"}), hide_index=True)

        if ov_version == "Full":
            if not ov_cur["payees"].empty:
                st.markdown("**Top payees**")
                st.dataframe(
                    ov_cur["payees"].head(10).rename(columns={"payee": "Payee", "track": "Track", "paid": "Paid", "scheduled": "Scheduled", "total": "Total"}), hide_index=True,
                    column_config={c: st.column_config.NumberColumn(c, format="₱ %.2f") for c in ("Paid", "Scheduled", "Total")},
                )
            st.markdown("**Cost of financing**")
            if ov_cur["interest"].empty:
                st.caption("No interest recorded in this period.")
            else:
                st.dataframe(ov_cur["interest"].head(8).rename(columns={"source": "Source", "name": "Facility", "interest": "Interest"}), hide_index=True,
                             column_config={"Interest": st.column_config.NumberColumn("Interest", format="₱ %.2f")})
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
                              column_config={"Amount due": st.column_config.NumberColumn("Amount due", format="₱ %.2f")})
                uc2.dataframe(pd.DataFrame([{"Week starting": w, "Amount due": a} for w, a in ov_up["by_week"]]), hide_index=True,
                              column_config={"Amount due": st.column_config.NumberColumn("Amount due", format="₱ %.2f")})

        if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="ov_pdf_btn"):
            pdf_preview_and_download(
                generate_overview_pdf(ov, short=(ov_version == "Short")),
                f"overview_{ov_kind.lower()}_{ov['start']}_{ov_version.lower()}.pdf", key="ov_pdf_download", label="Overview PDF",
            )

    elif report_choice == "Possible duplicates — check everything on file":
        st.subheader("Possible duplicates — check everything on file")
        st.caption(
            "Looks across every supplier and batch, migrated ones included, for the same invoice number written differently "
            "(SI-00123 / 123), several invoices with the same amount on the same date, and batches with the same total. "
            "Not every finding is a mistake — if it is genuine, tick it and mark it as not a duplicate so it stops showing."
        )
        dup_show_dismissed = st.checkbox("Also show the ones I dismissed", key="rpt_dup_show_dismissed")
        dup_df = scan_possible_duplicates(include_dismissed=dup_show_dismissed)
        if dup_df.empty:
            st.success("Nothing suspicious found.")
        else:
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
                    "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f"), "where_found": "Batches", "count": "Items", "dismissed": "Dismissed",
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
            st.download_button("Download CSV", data=dup_df.drop(columns=["dup_key"]).to_csv(index=False), file_name="possible_duplicates.csv", mime="text/csv", key="dl_dup_scan")

    elif report_choice == "Schedule of payments — all tracks":
        st.subheader("Schedule of payments — all tracks")
        st.caption("Everything still owed, across all 4 tracks combined. Grouped by actual payment date — a due date falling on a weekend is paid the next banking day (Monday), so \"Due Date\" (as originally scheduled) and \"Payment Date\" (when it's actually paid/needs funding) can differ. \"Reference\" holds whichever identifier applies to that track (check number for Supplier/Lender, loan ref for Bank Loan, card name for Credit Card).")
        sched_detail = get_payment_schedule_all_tracks()
        if sched_detail.empty:
            st.caption("Nothing scheduled.")
        else:
            f1, f2 = st.columns(2)
            with f1:
                track_filter = st.multiselect("Filter by track", options=sorted(sched_detail["track"].unique()), key="sched_track_filter")
            with f2:
                sched_months = sorted(sched_detail["actual_payment_date"].astype(str).str[:7].unique())
                month_labels = {m: pd.to_datetime(m + "-01").strftime("%B %Y") for m in sched_months}
                month_filter = st.selectbox(
                    "Filter by month (payment date)", options=["All months"] + sched_months,
                    format_func=lambda m: "All months" if m == "All months" else month_labels[m],
                    key="sched_month_filter",
                )

            display_sched = sched_detail
            if track_filter:
                display_sched = display_sched[display_sched["track"].isin(track_filter)]
            if month_filter != "All months":
                display_sched = display_sched[display_sched["actual_payment_date"].astype(str).str.startswith(month_filter)]

            filter_bits = []
            if track_filter:
                filter_bits.append(", ".join(track_filter))
            if month_filter != "All months":
                filter_bits.append(month_labels[month_filter])
            st.caption(f"Exporting {' — '.join(filter_bits)} only." if filter_bits else "The tables, CSVs, and PDF below export exactly what's filtered here.")

            if display_sched.empty:
                st.caption("Nothing scheduled for this filter.")
            else:
                sched_daily = display_sched.groupby("actual_payment_date").agg(num_items=("amount", "count"), total_amount=("amount", "sum")).reset_index().sort_values("actual_payment_date")

                st.markdown("**Daily totals**")
                st.dataframe(
                    sched_daily, hide_index=True,
                    column_config={
                        "actual_payment_date": st.column_config.DateColumn("Payment Date"),
                        "num_items": st.column_config.NumberColumn("# Items"),
                        "total_amount": st.column_config.NumberColumn("Total", format="₱ %.2f"),
                    },
                )
                s1, s2 = st.columns(2)
                s1.metric("Total items scheduled", int(sched_daily["num_items"].sum()), border=True)
                s2.metric("Total amount scheduled", php(sched_daily["total_amount"].sum()), border=True)

                st.markdown("**Full breakdown**")
                st.dataframe(
                    display_sched, hide_index=True,
                    column_config={
                        "due_date": st.column_config.DateColumn("Due Date"),
                        "actual_payment_date": st.column_config.DateColumn("Payment Date"),
                        "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f"),
                    },
                )

                sched_file_bits = [t.lower().replace(" ", "_") for t in track_filter] + ([month_filter] if month_filter != "All months" else [])
                sched_file_suffix = "_".join(sched_file_bits) if sched_file_bits else "all_tracks"
                st.download_button("Download daily totals CSV", data=sched_daily.to_csv(index=False), file_name=f"payment_schedule_daily_totals_{sched_file_suffix}.csv", mime="text/csv", key="dl_sched_daily")
                st.download_button("Download full breakdown CSV", data=display_sched.to_csv(index=False), file_name=f"payment_schedule_{sched_file_suffix}.csv", mime="text/csv", key="dl_sched_detail")
                if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="sched_pdf_btn"):
                    pdf_preview_and_download(
                        generate_payment_schedule_pdf(track_filter or None, month_filter if month_filter != "All months" else None),
                        f"payment_schedule_{sched_file_suffix}.pdf", key="sched_pdf_download", label="Schedule of Payments PDF",
                    )

    elif report_choice == "Parked checks (not yet printed)":
        st.subheader("Parked checks (not yet printed)")
        st.caption(
            "Every check already recorded but still waiting on a check number — Phase 6 Redux "
            "assigns a number at PRINT time, not issue time. Grouped by check date (the "
            "Checks & checkbook tab's Current check run is the live, same-scope view for actually "
            "printing these)."
        )
        parked_detail = get_parked_checks_report()
        if parked_detail.empty:
            st.caption("Nothing parked — every issued check has been printed.")
        else:
            parked_months = sorted(parked_detail["actual_payment_date"].astype(str).str[:7].unique())
            parked_month_labels = {m: pd.to_datetime(m + "-01").strftime("%B %Y") for m in parked_months}
            parked_month_filter = st.selectbox(
                "Filter by month (check date)", options=["All months"] + parked_months,
                format_func=lambda m: "All months" if m == "All months" else parked_month_labels[m],
                key="parked_month_filter",
            )
            display_parked = parked_detail
            if parked_month_filter != "All months":
                display_parked = display_parked[display_parked["actual_payment_date"].astype(str).str.startswith(parked_month_filter)]

            if display_parked.empty:
                st.caption("Nothing parked for this filter.")
            else:
                parked_daily = display_parked.groupby("actual_payment_date").agg(num_checks=("amount", "count"), total_amount=("amount", "sum")).reset_index().sort_values("actual_payment_date")

                st.markdown("**Daily totals**")
                st.dataframe(
                    parked_daily, hide_index=True,
                    column_config={
                        "actual_payment_date": st.column_config.DateColumn("Check Date"),
                        "num_checks": st.column_config.NumberColumn("# Checks"),
                        "total_amount": st.column_config.NumberColumn("Total", format="₱ %.2f"),
                    },
                )
                p1, p2 = st.columns(2)
                p1.metric("Total checks parked", int(parked_daily["num_checks"].sum()), border=True)
                p2.metric("Total amount parked", php(parked_daily["total_amount"].sum()), border=True)

                st.markdown("**Full breakdown**")
                st.dataframe(
                    display_parked, hide_index=True,
                    column_config={
                        "due_date": st.column_config.DateColumn("Check Date"),
                        "actual_payment_date": st.column_config.DateColumn("Payment Date"),
                        "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f"),
                    },
                )

                parked_file_suffix = parked_month_filter if parked_month_filter != "All months" else "all"
                st.download_button("Download daily totals CSV", data=parked_daily.to_csv(index=False), file_name=f"parked_checks_daily_totals_{parked_file_suffix}.csv", mime="text/csv", key="dl_parked_daily")
                st.download_button("Download full breakdown CSV", data=display_parked.to_csv(index=False), file_name=f"parked_checks_{parked_file_suffix}.csv", mime="text/csv", key="dl_parked_detail")
                if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="parked_pdf_btn"):
                    pdf_preview_and_download(
                        generate_parked_checks_pdf(parked_month_filter if parked_month_filter != "All months" else None),
                        f"parked_checks_{parked_file_suffix}.pdf", key="parked_pdf_download", label="Parked Checks PDF",
                    )

    elif report_choice == "Supplier — invoice & check history":
        st.subheader("Supplier — invoice & check history")
        st.caption("Every order statement (batch), invoice, and check for one supplier — pick a supplier, narrow by date/status, and pull a Statement of Account whenever you're asked for this supplier's transaction history.")
        sup_options = creditor_options_all("SUPPLIER")
        if not sup_options:
            st.caption("No suppliers on file yet.")
        else:
            sup_id = st.selectbox("Supplier", options=list(sup_options), format_func=lambda cid: sup_options[cid], key="rpt_supplier_pick")
            sup_statements, sup_invoices, sup_checks = get_supplier_history(sup_id)

            sup_date_from, sup_date_to = history_date_range(
                [
                    sup_statements["created_at"] if not sup_statements.empty else None,
                    sup_invoices["invoice_date"] if not sup_invoices.empty else None,
                    sup_checks["check_date"] if not sup_checks.empty else None,
                ],
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

            st.markdown("**Order statements (batches)**")
            sup_statements_shown = filter_by_status(sup_statements_f, "status", key=f"rpt_sup_batch_status_{sup_id}")
            if sup_statements_shown.empty:
                st.caption("No batches for this period/status.")
            else:
                st.dataframe(
                    sup_statements_shown, hide_index=True,
                    column_config={
                        "total_amount": st.column_config.NumberColumn("Total", format="₱ %.2f"),
                        "created_at": st.column_config.DatetimeColumn("Created"),
                    },
                )

            st.markdown("**Invoices**")
            sup_invoices_shown = filter_by_status(sup_invoices_f, "status", key=f"rpt_sup_invoice_status_{sup_id}")
            if sup_invoices_shown.empty:
                st.caption("No invoices for this period/status.")
            else:
                st.dataframe(
                    sup_invoices_shown, hide_index=True,
                    column_config={
                        "invoice_date": st.column_config.DateColumn("Invoice Date"),
                        "due_date": st.column_config.DateColumn("Due Date"),
                        "gross_amount": st.column_config.NumberColumn("Gross", format="₱ %.2f"),
                        "amount": st.column_config.NumberColumn("Net Amount", format="₱ %.2f"),
                    },
                )

            st.markdown("**Checks issued**")
            sup_checks_shown = filter_by_status(sup_checks_f, "status", key=f"rpt_sup_check_status_{sup_id}")
            if sup_checks_shown.empty:
                st.caption("No checks issued for this period/status.")
            else:
                st.dataframe(
                    sup_checks_shown, hide_index=True,
                    column_config={
                        "check_date": st.column_config.DateColumn("Check Date"),
                        "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f"),
                    },
                )

            sup_slug = sup_options[sup_id].lower().replace(" ", "_")
            c1, c2, c3 = st.columns(3)
            c1.download_button("Download batches CSV", data=sup_statements_shown.to_csv(index=False), file_name=f"supplier_{sup_slug}_batches.csv", mime="text/csv", key="dl_sup_batches")
            c2.download_button("Download invoices CSV", data=sup_invoices_shown.to_csv(index=False), file_name=f"supplier_{sup_slug}_invoices.csv", mime="text/csv", key="dl_sup_invoices")
            c3.download_button("Download checks CSV", data=sup_checks_shown.to_csv(index=False), file_name=f"supplier_{sup_slug}_checks.csv", mime="text/csv", key="dl_sup_checks")

            st.divider()
            st.markdown("**PDF — Statement of Account**")
            sup_group_by_batch = st.checkbox("Break out each batch separately (instead of one combined statement)", key=f"rpt_sup_group_{sup_id}")
            if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="sup_pdf_btn"):
                pdf_preview_and_download(
                    generate_supplier_statement_pdf(
                        sup_options[sup_id], sup_statements_shown, sup_invoices_shown, sup_checks_shown,
                        sup_date_from, sup_date_to, group_by_batch=sup_group_by_batch,
                    ),
                    f"supplier_{sup_slug}_statement.pdf", key="sup_pdf_download", label="Supplier Statement of Account PDF",
                )

    elif report_choice == "Supplier — batch rundown (paid / partial / pending)":
        st.subheader("Supplier — batch rundown")
        st.caption("Every issued batch (order statement) with how much is paid, how much is covered by post-dated checks, and what is still open. Paid = checks whose date has arrived. Pick one supplier or all of them.")
        rd_options = creditor_options_all("SUPPLIER")
        rd_choice = st.selectbox("Supplier", options=[None] + list(rd_options), format_func=lambda cid: "All suppliers" if cid is None else rd_options[cid], key="rpt_rundown_pick")
        rd_all = get_payables_rundown(rd_choice)
        if not rd_all.empty and rd_all["first_check"].notna().any():
            rd_min, rd_max = date.fromisoformat(rd_all["first_check"].min()), date.fromisoformat(rd_all["last_check"].max())
        else:
            rd_min = rd_max = date.today()
        rd_from_col, rd_to_col = st.columns(2)
        rd_from = rd_from_col.date_input("From", value=rd_min, key=f"rpt_rundown_from_{rd_choice}", help="Batches with a check dated on or after this day.")
        rd_to = rd_to_col.date_input("To", value=rd_max, key=f"rpt_rundown_to_{rd_choice}", help="Batches with a check dated on or before this day. Amounts shown are for the whole batch.")
        rd_df = get_payables_rundown(rd_choice, rd_from, rd_to)
        if rd_df.empty:
            st.caption("No issued batches for this selection.")
        else:
            rd_status_order = ["Paid", "Partially Paid", "Pending"]
            rd_cols = st.columns(3)
            for col, status_name in zip(rd_cols, rd_status_order):
                part = rd_df[rd_df["status"] == status_name]
                col.metric(status_name, f"{len(part)} batch(es)", border=True)
                col.caption(f"Net total {php(part['net_total'].sum())} · still owed {php(part['balance'].sum())}")
            rd_picked = st.multiselect("Show", options=rd_status_order, default=rd_status_order, key="rpt_rundown_status")
            rd_view = rd_df[rd_df["status"].isin(rd_picked)].copy()
            rd_view["running_balance"] = rd_view["balance"].groupby(rd_view["supplier"]).cumsum()
            rd_money = {"first_check": st.column_config.DateColumn("First check"), "last_check": st.column_config.DateColumn("Last check")}
            rd_money.update({c: st.column_config.NumberColumn(label, format="₱ %.2f") for c, label in
                        [("net_total", "Net total"), ("paid", "Paid"), ("scheduled", "Post-dated"), ("open", "Not yet covered"), ("balance", "Still owed"), ("running_balance", "Running balance")]})
            if rd_choice is None:
                st.markdown("**Per supplier**")
                rd_sum = rd_view.pivot_table(index="supplier", columns="status", values="net_total", aggfunc="sum", fill_value=0.0)
                for name in rd_status_order:
                    if name not in rd_sum.columns:
                        rd_sum[name] = 0.0
                rd_sum = rd_sum[rd_status_order].reset_index()
                rd_sum["Total"] = rd_sum[rd_status_order].sum(axis=1)
                st.dataframe(rd_sum, hide_index=True, column_config={n: st.column_config.NumberColumn(n, format="₱ %.2f") for n in rd_status_order + ["Total"]})
            st.markdown("**Batches**")
            st.dataframe(
                rd_view[["supplier", "batch", "status", "first_check", "last_check", "net_total", "paid", "scheduled", "open", "balance", "running_balance"]],
                hide_index=True, column_config=rd_money,
            )
            st.download_button("Download rundown CSV", data=rd_view.to_csv(index=False), file_name="batch_rundown.csv", mime="text/csv", key="dl_rundown")
            if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="batch_rundown_pdf_btn"):
                rd_rows = [[r.supplier, r.batch, r.status, str(r.first_check or "—")[:10], str(r.last_check or "—")[:10], _pdf_money(r.net_total), _pdf_money(r.paid), _pdf_money(r.scheduled), _pdf_money(r.balance), _pdf_money(r.running_balance)] for r in rd_view.itertuples()]
                rd_summary = [(n, int((rd_view["status"] == n).sum()), float(rd_view.loc[rd_view["status"] == n, "balance"].sum())) for n in rd_status_order]
                pdf_preview_and_download(
                    generate_rundown_pdf(
                        "Batch rundown", "All suppliers" if rd_choice is None else rd_options[rd_choice],
                        ["Supplier", "Batch", "Status", "First check", "Last check", "Net total", "Paid", "Post-dated", "Still owed", "Running Balance"], rd_rows,
                        [0.16, 0.13, 0.08, 0.07, 0.07, 0.09, 0.09, 0.09, 0.10, 0.12], [5, 6, 7, 8, 9], rd_summary, f"Checks dated {rd_from} to {rd_to}  ·  amounts in the summary are still owed",
                    ),
                    "batch_rundown.pdf", key="batch_rundown_pdf_download", label="Batch Rundown PDF",
                )

    elif report_choice == "Supplier — invoice rundown (paid / partial / pending)":
        st.subheader("Supplier — invoice rundown")
        st.caption("Every invoice with its status — Paid, Partially Paid or Pending. Pick one supplier or all of them, and narrow by invoice date. For payments per batch, use the batch rundown.")
        ir_options = creditor_options_all("SUPPLIER")
        ir_choice = st.selectbox("Supplier", options=[None] + list(ir_options), format_func=lambda cid: "All suppliers" if cid is None else ir_options[cid], key="rpt_inv_rundown_pick")
        ir_all = get_invoice_rundown(ir_choice)
        if ir_all.empty:
            st.caption("No invoices on file for this selection. (Migrated history was loaded as batches only — see the batch rundown for those.)")
        else:
            ir_min, ir_max = date.fromisoformat(str(ir_all["invoice_date"].min())[:10]), date.fromisoformat(str(ir_all["invoice_date"].max())[:10])
            ir_from_col, ir_to_col = st.columns(2)
            ir_from = ir_from_col.date_input("From", value=ir_min, key=f"rpt_inv_from_{ir_choice}")
            ir_to = ir_to_col.date_input("To", value=ir_max, key=f"rpt_inv_to_{ir_choice}")
            ir_df = get_invoice_rundown(ir_choice, ir_from, ir_to)
            ir_order = ["Paid", "Partially Paid", "Pending"]
            for col, status_name in zip(st.columns(3), ir_order):
                part = ir_df[ir_df["status"] == status_name]
                col.metric(status_name, f"{len(part)} invoice(s)", border=True)
                col.caption(f"Amount {php(part['amount'].sum())}")
            ir_picked = st.multiselect("Show", options=ir_order, default=ir_order, key="rpt_inv_rundown_status")
            ir_view = ir_df[ir_df["status"].isin(ir_picked)].copy()
            # Running balance = what is still owed, adding up row by row within each supplier (Paid rows add nothing).
            ir_view["running_balance"] = ir_view["amount"].where(ir_view["status"] != "Paid", 0.0).groupby(ir_view["supplier"]).cumsum()
            if ir_choice is None and not ir_view.empty:
                st.markdown("**Per supplier**")
                ir_sum = ir_view.pivot_table(index="supplier", columns="status", values="amount", aggfunc="sum", fill_value=0.0)
                for name in ir_order:
                    if name not in ir_sum.columns:
                        ir_sum[name] = 0.0
                ir_sum = ir_sum[ir_order].reset_index()
                ir_sum["Total"] = ir_sum[ir_order].sum(axis=1)
                st.dataframe(ir_sum, hide_index=True, column_config={n: st.column_config.NumberColumn(n, format="₱ %.2f") for n in ir_order + ["Total"]})
            st.markdown("**Invoices**")
            st.dataframe(
                ir_view, hide_index=True,
                column_config={
                    "invoice_date": st.column_config.DateColumn("Invoice Date"),
                    "due_date": st.column_config.DateColumn("Due Date"),
                    "amount": st.column_config.NumberColumn("Net Amount", format="₱ %.2f"),
                    "running_balance": st.column_config.NumberColumn("Running Balance", format="₱ %.2f"),
                },
            )
            st.download_button("Download invoice rundown CSV", data=ir_view.to_csv(index=False), file_name="invoice_rundown.csv", mime="text/csv", key="dl_inv_rundown")
            if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="inv_rundown_pdf_btn"):
                ir_rows = [[r.supplier, r.invoice, r.batch or "—", str(r.invoice_date)[:10], str(r.due_date)[:10], _pdf_money(r.amount), r.status, _pdf_money(r.running_balance)] for r in ir_view.itertuples()]
                ir_summary = [(n, int((ir_view["status"] == n).sum()), float(ir_view.loc[ir_view["status"] == n, "amount"].sum())) for n in ir_order]
                pdf_preview_and_download(
                    generate_rundown_pdf(
                        "Invoice rundown", "All suppliers" if ir_choice is None else ir_options[ir_choice],
                        ["Supplier", "Invoice", "Batch", "Invoice Date", "Due Date", "Net Amount", "Status", "Running Balance"], ir_rows,
                        [0.20, 0.09, 0.20, 0.09, 0.09, 0.11, 0.09, 0.13], [5, 7], ir_summary, f"Invoice dates {ir_from} to {ir_to}",
                    ),
                    "invoice_rundown.pdf", key="inv_rundown_pdf_download", label="Invoice Rundown PDF",
                )

    elif report_choice == "Private lender — loan note & check history":
        st.subheader("Private lender — loan note & check history")
        st.caption("Every loan note and every check for one private lender — pick a lender, narrow by date/status, and pull a Statement of Account whenever you're asked for this lender's transaction history.")
        lender_options = creditor_options_all("LENDER")
        if not lender_options:
            st.caption("No private lenders on file yet.")
        else:
            lender_id = st.selectbox("Lender", options=list(lender_options), format_func=lambda cid: lender_options[cid], key="rpt_lender_pick")
            ln_notes, ln_checks = get_lender_history(lender_id)

            ln_date_from, ln_date_to = history_date_range(
                [
                    ln_notes["issue_date"] if not ln_notes.empty else None,
                    ln_checks["check_date"] if not ln_checks.empty else None,
                ],
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
                st.dataframe(
                    ln_notes_shown.drop(columns=["loan_note_id"]), hide_index=True,
                    column_config={
                        "issue_date": st.column_config.DateColumn("Issue Date"),
                        "principal_amount": st.column_config.NumberColumn("Principal", format="₱ %.2f"),
                        "total_interest": st.column_config.NumberColumn("Interest", format="₱ %.2f"),
                        "total_payable": st.column_config.NumberColumn("Total Payable", format="₱ %.2f"),
                        "outstanding_uncleared": st.column_config.NumberColumn("Outstanding", format="₱ %.2f"),
                    },
                )

            st.markdown("**Checks issued**")
            ln_checks_shown = filter_by_status(ln_checks_f, "status", key=f"rpt_len_check_status_{lender_id}")
            if ln_checks_shown.empty:
                st.caption("No checks issued for this period/status.")
            else:
                st.dataframe(
                    ln_checks_shown, hide_index=True,
                    column_config={
                        "check_date": st.column_config.DateColumn("Check Date"),
                        "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f"),
                    },
                )

            lender_slug = lender_options[lender_id].lower().replace(" ", "_")
            c1, c2 = st.columns(2)
            c1.download_button("Download loan notes CSV", data=ln_notes_shown.to_csv(index=False), file_name=f"lender_{lender_slug}_notes.csv", mime="text/csv", key="dl_len_notes")
            c2.download_button("Download checks CSV", data=ln_checks_shown.to_csv(index=False), file_name=f"lender_{lender_slug}_checks.csv", mime="text/csv", key="dl_len_checks")

            st.divider()
            st.markdown("**PDF — Statement of Account**")
            ln_group_by_note = st.checkbox("Break out each loan note separately (instead of one combined statement)", key=f"rpt_len_group_{lender_id}")
            if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="len_pdf_btn"):
                pdf_preview_and_download(
                    generate_lender_statement_pdf(
                        lender_options[lender_id], ln_notes_shown, ln_checks_shown,
                        ln_date_from, ln_date_to, group_by_note=ln_group_by_note,
                    ),
                    f"lender_{lender_slug}_statement.pdf", key="len_pdf_download", label="Lender Statement of Account PDF",
                )

    elif report_choice == "Bank loan — installment history":
        st.subheader("Bank loan — installment history")
        st.caption("Every installment for one bank loan, paid or outstanding — pick a loan, narrow by date/status, and pull a Statement of Account whenever you're asked for this loan's history.")
        bl_options = bank_loan_options()
        if not bl_options:
            st.caption("No bank loans on file yet.")
        else:
            bl_id = st.selectbox("Bank loan", options=list(bl_options), format_func=lambda lid: bl_options[lid], key="rpt_bank_loan_pick")
            bl_info, bl_installments = get_bank_loan_history(bl_id)
            bl_row = bl_info.iloc[0]

            i1, i2, i3, i4 = st.columns(4)
            i1.metric("Bank", bl_row["bank_name"], border=True)
            i2.metric("Principal", php(bl_row["principal_amount"]), border=True)
            i3.metric("Status", bl_row["status"], border=True)
            i4.metric("Maturity", bl_row["maturity_date"], border=True)

            bl_date_from, bl_date_to = history_date_range(
                [bl_installments["due_date"] if not bl_installments.empty else None],
                key_prefix=f"rpt_bl_date_{bl_id}",
            )
            bl_installments_f = filter_by_date(bl_installments, "due_date", bl_date_from, bl_date_to)

            st.markdown("**Installments**")
            bl_installments_shown = filter_by_status(bl_installments_f, "status", key=f"rpt_bl_status_{bl_id}")
            if bl_installments_shown.empty:
                st.caption("No installments for this period/status.")
            else:
                st.dataframe(
                    bl_installments_shown, hide_index=True,
                    column_config={
                        "due_date": st.column_config.DateColumn("Due Date"),
                        "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f"),
                    },
                )
                st.download_button("Download installments CSV", data=bl_installments_shown.to_csv(index=False), file_name=f"bank_loan_{bl_row['loan_ref']}_installments.csv", mime="text/csv", key="dl_bl_installments")

            st.divider()
            st.markdown("**PDF — Statement of Account**")
            if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="bl_pdf_btn"):
                pdf_preview_and_download(
                    generate_bank_loan_statement_pdf(bl_row, bl_installments_shown, bl_date_from, bl_date_to),
                    f"bank_loan_{bl_row['loan_ref']}_statement.pdf", key="bl_pdf_download", label="Bank Loan Statement of Account PDF",
                )

    elif report_choice == "Credit card — statement history":
        st.subheader("Credit card — statement history")
        st.caption("Grouped by whose card it is — one cardholder can have several cards. Pick a cardholder, then drill into one of their cards or combine all of them, narrow by date/status, and pull a Statement of Account whenever you're asked for this card's history.")
        cc_holder_options = cardholder_options_with_cards()
        if not cc_holder_options:
            st.caption("No credit cards on file yet.")
        else:
            cc_holder_id = st.selectbox("Cardholder", options=list(cc_holder_options), format_func=lambda hid: cc_holder_options[hid], key="rpt_card_holder_pick")
            cc_holder_slug = cc_holder_options[cc_holder_id].lower().replace(" ", "_")
            cc_hist_options = credit_card_options_for_holder(cc_holder_id)
            cc_picker_options = ["ALL"] + list(cc_hist_options)
            cc_hist_id = st.selectbox(
                "Credit card", options=cc_picker_options,
                format_func=lambda cid: f"All cards ({len(cc_hist_options)})" if cid == "ALL" else cc_hist_options[cid],
                key=f"rpt_card_pick_{cc_holder_id}",
            )

            if cc_hist_id == "ALL":
                cc_cards_info, cc_statements = get_cardholder_all_cards_history(cc_holder_id)

                cc_date_from, cc_date_to = history_date_range(
                    [cc_statements["due_date"] if not cc_statements.empty else None],
                    key_prefix=f"rpt_cc_all_date_{cc_holder_id}",
                )
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
                    st.dataframe(
                        cc_cards_info.drop(columns=["card_id"]), hide_index=True,
                        column_config={"credit_limit": st.column_config.NumberColumn("Credit Limit", format="₱ %.2f")},
                    )

                st.markdown("**Statements (all cards)**")
                cc_statements_shown = filter_by_status(cc_statements_f, "status", key=f"rpt_cc_all_status_{cc_holder_id}")
                if cc_statements_shown.empty:
                    st.caption("No statements for this period/status.")
                else:
                    st.dataframe(
                        cc_statements_shown, hide_index=True,
                        column_config={
                            "card_name": "Card",
                            "card_number_last4": "Last 4",
                            "due_date": st.column_config.DateColumn("Due Date"),
                            "total_amount_due": st.column_config.NumberColumn("Amount Due", format="₱ %.2f"),
                            "payment_date": st.column_config.DateColumn("Payment Date"),
                        },
                    )
                    st.download_button("Download statements CSV", data=cc_statements_shown.to_csv(index=False), file_name=f"cardholder_{cc_holder_slug}_all_cards_statements.csv", mime="text/csv", key="dl_cc_all_statements")

                st.divider()
                st.markdown("**PDF — Statement of Account**")
                cc_group_by_card = st.checkbox("Break out each card separately (instead of one combined statement)", key=f"rpt_cc_group_{cc_holder_id}")
                if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="cc_all_pdf_btn"):
                    pdf_preview_and_download(
                        generate_cardholder_statement_pdf(
                            cc_holder_options[cc_holder_id], cc_cards_info, cc_statements_shown,
                            cc_date_from, cc_date_to, group_by_card=cc_group_by_card,
                        ),
                        f"cardholder_{cc_holder_slug}_all_cards_statement.pdf", key="cc_all_pdf_download", label="Cardholder Statement of Account PDF",
                    )
            else:
                cc_info, cc_statements = get_credit_card_history(cc_hist_id)
                cc_row = cc_info.iloc[0]

                i1, i2, i3 = st.columns(3)
                i1.metric("Issuer", cc_row["issuer"], border=True)
                i2.metric("Credit limit", php(cc_row["credit_limit"]), border=True)
                i3.metric("Cardholder", cc_row["cardholder"] or "—", border=True)

                cc_date_from, cc_date_to = history_date_range(
                    [cc_statements["due_date"] if not cc_statements.empty else None],
                    key_prefix=f"rpt_cc_date_{cc_hist_id}",
                )
                cc_statements_f = filter_by_date(cc_statements, "due_date", cc_date_from, cc_date_to)

                st.markdown("**Statements**")
                cc_statements_shown = filter_by_status(cc_statements_f, "status", key=f"rpt_cc_status_{cc_hist_id}")
                if cc_statements_shown.empty:
                    st.caption("No statements for this period/status.")
                else:
                    st.dataframe(
                        cc_statements_shown, hide_index=True,
                        column_config={
                            "due_date": st.column_config.DateColumn("Due Date"),
                            "total_amount_due": st.column_config.NumberColumn("Amount Due", format="₱ %.2f"),
                            "payment_date": st.column_config.DateColumn("Payment Date"),
                        },
                    )
                    st.download_button("Download statements CSV", data=cc_statements_shown.to_csv(index=False), file_name=f"card_{cc_row['card_name']}_{cc_row['card_number_last4']}_statements.csv", mime="text/csv", key="dl_cc_statements")

                st.divider()
                st.markdown("**PDF — Statement of Account**")
                if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="cc_pdf_btn"):
                    pdf_preview_and_download(
                        generate_credit_card_statement_pdf(cc_row, cc_statements_shown, cc_date_from, cc_date_to),
                        f"card_{cc_row['card_name']}_{cc_row['card_number_last4']}_statement.pdf", key="cc_pdf_download", label="Credit Card Statement of Account PDF",
                    )

    elif report_choice == "Master rollup — supplier AP aging":
        st.subheader("Master rollup — supplier AP aging")
        st.caption("Every supplier's open invoices, bucketed by how overdue they are, across the whole company.")
        aging_df = get_supplier_ap_aging_report()
        if aging_df.empty:
            st.caption("No open supplier invoices.")
        else:
            st.dataframe(
                aging_df, hide_index=True,
                column_config={
                    "total_unpaid": st.column_config.NumberColumn("Total Unpaid", format="₱ %.2f"),
                    "current_due": st.column_config.NumberColumn("Current", format="₱ %.2f"),
                    "days_1_30": st.column_config.NumberColumn("1-30 Days", format="₱ %.2f"),
                    "days_31_60": st.column_config.NumberColumn("31-60 Days", format="₱ %.2f"),
                    "days_over_60": st.column_config.NumberColumn("60+ Days", format="₱ %.2f"),
                },
            )
            st.download_button("Download CSV", data=aging_df.to_csv(index=False), file_name="supplier_ap_aging.csv", mime="text/csv", key="dl_aging")
            if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="aging_pdf_btn"):
                pdf_preview_and_download(
                    generate_rundown_pdf(
                        "Supplier AP aging", "All suppliers",
                        ["Supplier", "Open", "Total Unpaid", "Current", "1-30 Days", "31-60 Days", "60+ Days"],
                        [[r.supplier_name, int(r.open_invoices), _pdf_money(r.total_unpaid), _pdf_money(r.current_due), _pdf_money(r.days_1_30), _pdf_money(r.days_31_60), _pdf_money(r.days_over_60)] for r in aging_df.itertuples()],
                        [0.32, 0.07, 0.14, 0.12, 0.12, 0.12, 0.11], [1, 2, 3, 4, 5, 6], [], f"As of {date.today()}",
                    ),
                    "supplier_ap_aging.pdf", key="aging_pdf_download", label="Supplier AP Aging PDF",
                )

    elif report_choice == "Master rollup — private lender exposure":
        st.subheader("Master rollup — private lender exposure")
        st.caption("Every private lender's loan notes and current uncleared exposure, across the whole company.")
        lender_master_df = get_private_lender_master_report()
        if lender_master_df.empty:
            st.caption("No private lender notes on file.")
        else:
            st.dataframe(
                lender_master_df, hide_index=True,
                column_config={
                    "issue_date": st.column_config.DateColumn("Issue Date"),
                    "principal_amount": st.column_config.NumberColumn("Principal", format="₱ %.2f"),
                    "total_interest": st.column_config.NumberColumn("Interest", format="₱ %.2f"),
                    "total_payable": st.column_config.NumberColumn("Total Payable", format="₱ %.2f"),
                    "outstanding_uncleared": st.column_config.NumberColumn("Outstanding", format="₱ %.2f"),
                },
            )
            st.download_button("Download CSV", data=lender_master_df.to_csv(index=False), file_name="private_lender_exposure.csv", mime="text/csv", key="dl_lender_master")
            if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="lender_master_pdf_btn"):
                pdf_preview_and_download(
                    generate_rundown_pdf(
                        "Private lender exposure", "All private lenders",
                        ["Lender", "Note", "Issue Date", "Principal", "Interest", "Total Payable", "Outstanding", "Status"],
                        [[r.lender_name, r.note_ref, str(r.issue_date)[:10], _pdf_money(r.principal_amount), _pdf_money(r.total_interest), _pdf_money(r.total_payable), _pdf_money(r.outstanding_uncleared), r.status] for r in lender_master_df.itertuples()],
                        [0.2, 0.13, 0.09, 0.11, 0.1, 0.12, 0.11, 0.14], [3, 4, 5, 6], [], f"As of {date.today()}",
                    ),
                    "private_lender_exposure.pdf", key="lender_master_pdf_download", label="Private Lender Exposure PDF",
                )

    elif report_choice == "Master rollup — bank loan servicing":
        st.subheader("Master rollup — bank loan servicing")
        st.caption("Every bank loan and its current outstanding balance, across the whole company.")
        bl_master_df = get_bank_loan_servicing_report()
        if bl_master_df.empty:
            st.caption("No bank loans on file.")
        else:
            st.dataframe(
                bl_master_df, hide_index=True,
                column_config={
                    "start_date": st.column_config.DateColumn("Start Date"),
                    "maturity_date": st.column_config.DateColumn("Maturity Date"),
                    "principal_amount": st.column_config.NumberColumn("Principal", format="₱ %.2f"),
                    "outstanding": st.column_config.NumberColumn("Outstanding", format="₱ %.2f"),
                },
            )
            st.download_button("Download CSV", data=bl_master_df.to_csv(index=False), file_name="bank_loan_servicing.csv", mime="text/csv", key="dl_bl_master")
            if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="bl_master_pdf_btn"):
                pdf_preview_and_download(
                    generate_rundown_pdf(
                        "Bank loan servicing", "All bank loans",
                        ["Bank", "Loan Ref", "Start", "Maturity", "Principal", "Outstanding", "Status"],
                        [[r.bank_name, r.loan_ref, str(r.start_date or "—")[:10], str(r.maturity_date or "—")[:10], _pdf_money(r.principal_amount) if pd.notna(r.principal_amount) else "—", _pdf_money(r.outstanding), r.status] for r in bl_master_df.itertuples()],
                        [0.16, 0.32, 0.09, 0.09, 0.12, 0.12, 0.10], [4, 5], [], f"As of {date.today()}",
                    ),
                    "bank_loan_servicing.pdf", key="bl_master_pdf_download", label="Bank Loan Servicing PDF",
                )
