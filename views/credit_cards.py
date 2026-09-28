"""Credit cards screen (moved out of app.py unchanged, 2026-09-27). app.py calls render() only while this page is open."""
from __future__ import annotations

import io
import pandas as pd
import streamlit as st

from datetime import date
from datetime import timedelta
from engines.card_rules import add_cardholder
from engines.card_rules import add_cc_statement
from engines.card_rules import add_credit_card
from engines.card_rules import confirm_cc_payment_from_next_statement
from engines.card_rules import deactivate_cardholder
from engines.card_rules import deactivate_credit_card
from engines.card_rules import delete_cc_statement
from engines.card_rules import edit_cardholder
from engines.card_rules import edit_cc_statement
from engines.card_rules import edit_credit_card
from engines.cc_dates import statement_month_for
from engines.card_rules import reactivate_cardholder
from engines.card_rules import reactivate_credit_card
from engines.cc_rules import cc_owed_case
from engines.cc_rules import superseded_sql
from engines.cc_service import get_annual_fee_alerts
from engines.cc_service import get_statement_coverage
from engines.cc_service import get_statement_gap_alerts
from engines.cc_service import mark_no_due
from engines.cc_service import no_due_marks
from engines.cc_service import undo_no_due
from engines.common import php
from engines.common import q_live
from engines.creditor_rules import get_or_create_card_issuer
from engines.duplicate_service import cc_statement_month_warnings
from engines.duplicate_service import cc_statement_warnings
from engines.duplicate_service import credit_card_warnings
from engines.validators import money_amount_error

from views.shared import (  # noqa: F401
    MONEY_FORMAT,
    confirm_create_dialog,
    creditor_options,
    date_col,
    dup_needs_confirm,
    dup_note,
    dup_resume,
    guard_duplicate_submit,
    money_col,
    page_header,
    q,
    refresh,
    section,
    status_badge,
    status_text,
)


def _card_label(cid: int, df: pd.DataFrame) -> str:
    """'Gold - 8000 (Issuer)' label for a card picker; df is the card list the picker shows (both callers pass it)."""
    d = df.set_index("card_id").loc[cid]
    suffix = "" if d.get("is_active", 1) else " — inactive"
    return f"{d['card_name']} - {d['card_number_last4']} ({d['issuer']}){suffix}"


def _card_frames():
    """Cardholders and cards on file (all, and active only) -- used by this page and by the Add data page."""
    cardholders_df = q("SELECT * FROM cardholders ORDER BY name")
    cardholders_df["is_active"] = pd.to_numeric(cardholders_df["is_active"], errors="coerce").fillna(1).astype(int)
    active_cardholders_df = cardholders_df[cardholders_df["is_active"] == 1]
    cards_df = q(
        """SELECT cc.card_id, cc.creditor_id, c.creditor_name AS issuer, ch.name AS cardholder, cc.card_name, cc.card_number_last4, cc.credit_limit,
                  cc.annual_fee_due_month, cc.statement_day, cc.cardholder_id, cc.is_active
           FROM credit_cards cc JOIN creditors c ON cc.creditor_id = c.creditor_id
           LEFT JOIN cardholders ch ON cc.cardholder_id = ch.cardholder_id
           ORDER BY LOWER(c.creditor_name), LOWER(COALESCE(ch.name, '')), LOWER(cc.card_name), cc.card_number_last4"""
    )
    # q() coerces every NULL to '' app-wide, which mixes '' (str) with real numbers (float) in a
    # nullable numeric column — pyarrow can't serialize that mix for st.dataframe once a real
    # value exists alongside a blank one. Normalize back to a clean numeric dtype for display.
    # credit_limit joined the nullable list in migration 034 (Phase 8a: legacy card import).
    cards_df["annual_fee_due_month"] = pd.to_numeric(cards_df["annual_fee_due_month"], errors="coerce")
    cards_df["statement_day"] = pd.to_numeric(cards_df["statement_day"], errors="coerce")
    cards_df["credit_limit"] = pd.to_numeric(cards_df["credit_limit"], errors="coerce")
    cards_df["is_active"] = pd.to_numeric(cards_df["is_active"], errors="coerce").fillna(1).astype(int)
    active_cards_df = cards_df[cards_df["is_active"] == 1]
    return cardholders_df, active_cardholders_df, cards_df, active_cards_df


def _month_label(month: str) -> str:
    return date(int(month[:4]), int(month[5:7]), 1).strftime("%B %Y")


def render_missing_statements(missing_df: pd.DataFrame, key: str) -> None:
    """The cards whose statement may be missing, each with a "No due" button (owner, 2026-09-29: "there are also times that recording
    statement is not necessary because it has no due so I opt not to record this"). Shared by Credit cards and Payment run."""
    today = date.today()
    this_key, prev_key = today.strftime("%Y-%m"), (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    for _, r in missing_df.iterrows():
        both = r.get("status") == "Missing both months"
        why = (f"no statement recorded for {_month_label(prev_key)} or {_month_label(this_key)}" if both
               else f"the {_month_label(this_key)} statement is not recorded" + (f" (expected by {r['expected_by']})" if r.get("expected_by") else ""))
        c1, c2 = st.columns([5, 1.6], vertical_alignment="center")
        c1.warning(f"{r['card_name']} ({r['card_number_last4']}) — {r['cardholder_name'] or 'no cardholder set'}: {why}"
                   + (f" · last due date on file {r['last_due_date']}" if pd.notna(r["last_due_date"]) else ""))
        months = [prev_key, this_key] if both else [this_key]
        if c2.button("No due (both months)" if both else "No due this month", icon=":material/event_busy:", key=f"{key}_nodue_{int(r['card_id'])}",
                     width="stretch", help="Nothing was due on this card for the month, so there is no statement to record. The warning stops; "
                                           "nothing is added to payables. You can undo it on the Credit cards page."):
            for m in months:
                mark_no_due(int(r["card_id"]), m)
            refresh(done=f"{r['card_name']}: marked No due for " + " and ".join(_month_label(m) for m in months) + ".")


def render_add_cc_statement_form() -> None:
    """The Add statement form (cardholder -> issuer -> card -> month, total due, payments seen, finance charge). Shared by Credit cards and Add data."""
    cardholders_df, active_cardholders_df, cards_df, active_cards_df = _card_frames()
    with st.container(border=True):
        st.markdown("**Add statement**")
        st.caption("Pick whose card first — one cardholder can have several cards.")
        if active_cardholders_df.empty:
            st.caption("Add a cardholder first, in Cards & cardholders.")
            stmt_holder_cards_df = pd.DataFrame(columns=cards_df.columns)
        else:
            stmt_holder = st.selectbox(
                "Cardholder", options=active_cardholders_df["cardholder_id"],
                format_func=lambda hid: active_cardholders_df.set_index("cardholder_id").loc[hid, "name"],
                key="stmt_holder_pick",
            )
            stmt_holder_cards_df = active_cards_df[active_cards_df["cardholder_id"] == stmt_holder]

        if stmt_holder_cards_df.empty:
            st.caption("No cards assigned to this cardholder yet — assign one via \"Edit a card\" in Cards & cardholders.")
        else:
            stmt_issuer_names = sorted(stmt_holder_cards_df["issuer"].unique(), key=str.lower)
            stmt_issuer = st.selectbox("Issuer (bank)", options=stmt_issuer_names, key=f"stmt_issuer_pick_{stmt_holder}")
            stmt_issuer_cards_df = stmt_holder_cards_df[stmt_holder_cards_df["issuer"] == stmt_issuer].sort_values(
                ["card_name", "card_number_last4"], key=lambda col: col.astype(str).str.lower()
            )
            stmt_card = st.selectbox(
                "Card", options=stmt_issuer_cards_df["card_id"],
                format_func=lambda cid: _card_label(cid, stmt_issuer_cards_df),
                key=f"stmt_card_pick_{stmt_holder}_{stmt_issuer}",
            )
            stmt_due = st.date_input("Due date", value=date.today(), key="stmt_due_date", help='The payment due date printed on the statement.')
            # Owner, 2026-09-29: the statement month is the month of the card's statement date -- usually the month before the due
            # date, sometimes the same month (engines/cc_dates.py). Pre-filled from the due date; can be changed.
            stmt_card_day = stmt_issuer_cards_df.set_index("card_id").loc[stmt_card, "statement_day"]
            stmt_card_day = None if pd.isna(stmt_card_day) else int(stmt_card_day)
            default_month_text = statement_month_for(stmt_due, stmt_card_day)
            default_year, default_month = int(default_month_text[:4]), int(default_month_text[5:7])
            sm_key = f"{int(stmt_card)}_{default_year}_{default_month}"
            sm1, sm2 = st.columns([3, 2])
            stmt_month_num = sm1.selectbox(
                "Statement month", options=list(range(1, 13)), index=default_month - 1,
                format_func=lambda m: date(2000, m, 1).strftime("%B"), key=f"stmt_month_{sm_key}",
                help="The month of the statement date. Filled in from the due date and the card's Statement day (usually the month before the "
                     "due date, sometimes the same month) — change it if the statement says otherwise.",
            )
            stmt_year_num = sm2.number_input("Year", min_value=2000, max_value=2100, step=1, value=default_year, key=f"stmt_year_{sm_key}")

            if st.session_state.pop("_clear_stmt_form", False):
                for _k in [k for k in st.session_state if str(k).startswith(("stmt_total_direct", "stmt_finance_direct", "stmt_payments_"))]:
                    st.session_state.pop(_k, None)

            # The statement's total already includes everything unpaid before it, so this is all you type:
            # what is due, what was paid since the last statement (optional), and any finance charge.
            stmt_last = q_live(
                """SELECT cc_statement_id AS id, due_date, total_amount_due AS total, paid_confirmed,
                          """ + cc_owed_case("cs") + """ AS owed
                   FROM cc_statements cs WHERE card_id = ? AND status != 'Cancelled' ORDER BY due_date DESC, cc_statement_id DESC LIMIT 1""",
                (int(stmt_card),),
            )
            stmt_earlier = stmt_last.iloc[0].to_dict() if not stmt_last.empty and str(stmt_last.iloc[0]["due_date"]) < str(stmt_due) else None
            stmt_total_direct = st.number_input(
                "Total amount due (from the statement)", min_value=0.0, step=100.0, key="stmt_total_direct",
                help="The amount the statement asks you to pay. It already includes any balance carried over, so it is counted once.",
            )
            stmt_payments = None
            if stmt_earlier:
                stmt_payments = st.number_input(
                    f"Payments received since the earlier statement (optional) — earlier total on file {php(stmt_earlier['total'])}",
                    min_value=0.0, step=100.0, value=None, key=f"stmt_payments_{stmt_earlier['id']}",
                    help="Add up the payment lines on this statement (the ones ending in C). Type 0 if nothing was paid. Leave empty if you don't know — the statement is still recorded and counted once.",
                )
            stmt_interest = st.number_input(
                "Finance charge / interest on this statement (optional)", min_value=0.0, step=10.0, key="stmt_finance_direct",
                help="The interest / finance charge the statement lists, if any. It is part of the total, and feeds the cost-of-financing figures.",
            )
            stmt_total_due = stmt_total_direct
            stmt_math_ok = True
            if stmt_earlier and stmt_payments is not None:
                stmt_carried_calc = max(float(stmt_earlier["total"]) - float(stmt_payments), 0.0)
                stmt_new_calc = round(stmt_total_direct - stmt_carried_calc - stmt_interest, 2)
                if stmt_new_calc < -1.0:
                    stmt_math_ok = False
                    st.error(f"These don't add up: earlier statement {php(stmt_earlier['total'])} − payments {php(stmt_payments)} + interest {php(stmt_interest)} is already "
                             f"{php(stmt_carried_calc + stmt_interest)}, more than the total due {php(stmt_total_direct)}. Check the payments, or the earlier statement's amount on file.")
                    stmt_previous, stmt_new_charges = stmt_carried_calc, 0.0
                else:
                    if stmt_new_calc < 0:      # a few centavos of rounding on the bank's side
                        stmt_carried_calc, stmt_new_calc = max(stmt_total_direct - stmt_interest, 0.0), 0.0
                    stmt_previous, stmt_new_charges = round(stmt_carried_calc, 2), stmt_new_calc
                    st.caption(f"Earlier {php(stmt_earlier['total'])} − payments {php(stmt_payments)} = {php(stmt_previous)} carried over · "
                               f"+ new charges {php(stmt_new_charges)} (worked out) + interest {php(stmt_interest)} = **{php(stmt_total_due)}**")
            else:
                stmt_previous, stmt_new_charges = 0.0, max(stmt_total_direct - stmt_interest, 0.0)

            if stmt_total_direct > 0:
                if stmt_earlier and float(stmt_earlier["owed"]) > 0.005:
                    st.info(f"Payables for this card after saving: **{php(stmt_total_due)}** — it replaces the earlier {php(stmt_earlier['owed'])} that is counted now, so nothing is counted twice.")
                else:
                    st.info(f"Payables for this card after saving: **{php(stmt_total_due)}**.")

            if st.button("Add statement", icon=":material/receipt_long:", type="primary", key="add_cc_statement_btn", disabled=not stmt_math_ok) or dup_resume("add_cc_statement"):
                stmt_month_text = f"{int(stmt_year_num)}-{int(stmt_month_num):02d}"
                if dup_needs_confirm("add_cc_statement", cc_statement_warnings(int(stmt_card), stmt_due, stmt_total_due)
                                     + cc_statement_month_warnings(int(stmt_card), stmt_month_text, stmt_total_due, due_date=stmt_due)):
                    pass
                elif guard_duplicate_submit("add_cc_statement", stmt_card, stmt_due, stmt_total_due, stmt_interest, stmt_payments):
                    add_cc_statement(int(stmt_card), stmt_due, stmt_previous, stmt_new_charges, stmt_interest, 0.0,
                                     statement_month=stmt_month_text, paid_confirmed=0)
                    if stmt_earlier and stmt_payments is not None and int(stmt_earlier["paid_confirmed"]) == 0:
                        earlier_paid, earlier_status = confirm_cc_payment_from_next_statement(int(stmt_earlier["id"]), stmt_previous)
                        st.toast(f"Earlier statement confirmed: {php(earlier_paid)} paid ({earlier_status}).")
                    st.session_state["_clear_stmt_form"] = True
                    refresh(done="Statement recorded.")
                else:
                    st.warning("Duplicate submission ignored — identical to the click just before it.")


def render() -> None:
    page_header("Credit cards")
    st.caption("Scoped to what matters: due dates, amounts due, whose card it is, making sure every statement gets recorded, and the annual fee waiver reminder.")

    cardholders_df, active_cardholders_df, cards_df, active_cards_df = _card_frames()

    def _blank_to_none_int(v):
        # q() coerces every NULL to '' app-wide (engines/db_engine.py df_query) — nullable
        # numeric fields read back through it need this before int() or pd.notna() checks.
        return None if v in (None, "") or (isinstance(v, float) and pd.isna(v)) else int(v)

    # ----------------------------------------------------------------------
    # Needs attention — surfaced first, like the Dashboard, instead of buried
    # between setup forms and data tables further down.
    # ----------------------------------------------------------------------
    gap_alerts_df = get_statement_gap_alerts()
    fee_alerts_df = get_annual_fee_alerts()
    # "No statement on file yet" (a card with zero statement history, typically not in active use)
    # is routine and can be a large pile after a legacy import -- split it from "Possibly missing a
    # statement" (a card that HAD statements but seems to have skipped a cycle), which is the one
    # worth surfacing as a real warning. Otherwise 40+ routine rows bury the rare actionable one.
    if gap_alerts_df.empty:
        never_had_df = missing_recent_df = gap_alerts_df
    else:
        never_had_df = gap_alerts_df[gap_alerts_df["alert"] == "No statement on file yet"]
        missing_recent_df = gap_alerts_df[gap_alerts_df["alert"] == "Possibly missing a statement"]

    if missing_recent_df.empty and fee_alerts_df.empty and never_had_df.empty:
        st.caption("Nothing needs attention — every card has a recent statement on file, and no annual fee months are coming up.")
    else:
        if not missing_recent_df.empty or not fee_alerts_df.empty:
            with st.container(border=True):
                st.markdown("**Needs attention**")
                render_missing_statements(missing_recent_df, "cc_attention")
                if not fee_alerts_df.empty:
                    st.caption("Annual fee month coming up — month only, since the exact day is the card's issuance date and isn't on file. Request the waiver before it posts.")
                    st.dataframe(fee_alerts_df[["card_name", "card_number_last4", "cardholder_name", "when"]], hide_index=True)
        if not never_had_df.empty:
            with st.expander(f"{len(never_had_df)} card(s) have never had a statement recorded"):
                st.caption("Routine for a card not in active use yet, or one just brought over from the legacy system. Add its first statement in the Statements tab below once a bill comes in.")
                st.dataframe(
                    never_had_df[["card_name", "card_number_last4", "cardholder_name"]].rename(
                        columns={"card_name": "Card", "card_number_last4": "Last 4", "cardholder_name": "Cardholder"}
                    ),
                    hide_index=True,
                )
    marks = sorted(no_due_marks(), key=lambda m: (m[1], m[0]), reverse=True)
    if marks:
        with st.expander(f"Marked \"No due\" ({len(marks)})", icon=":material/event_busy:"):
            st.caption("Months you marked as having nothing due on a card, so no statement was recorded. Undo if a statement turns up.")
            names = {int(c): f"{n} ({l4})" for c, n, l4 in q_live("SELECT card_id, card_name, card_number_last4 FROM credit_cards").itertuples(index=False)}
            for card_id, month in marks:
                m1, m2 = st.columns([5, 1.2], vertical_alignment="center")
                m1.markdown(f"{names.get(card_id, card_id)} — **{_month_label(month)}**: no due")
                if m2.button("Undo", icon=":material/undo:", key=f"nodue_undo_{card_id}_{month}", width="stretch"):
                    undo_no_due(card_id, month)
                    refresh(done=f"{names.get(card_id, card_id)}: {_month_label(month)} is no longer marked No due.")

    sub_statements, sub_cards, sub_check = st.tabs([
        ":material/receipt_long: Statements", ":material/credit_card: Cards & cardholders", ":material/fact_check: Missing statement check",
    ])

    # ----------------------------------------------------------------------
    with sub_statements:
        st.caption("Whose card, how much, and when it's due. A statement with a real unpaid balance stays Unpaid/Partially Paid even past its due date — only a statement with nothing left owing (or one without rolling-balance tracking at all) auto-shows as Paid once its due date passes.")
        cc_statements_df = q(
            f"""SELECT cs.cc_statement_id, cs.card_id, COALESCE(cs.statement_month, SUBSTR(cs.due_date, 1, 7)) AS statement_month, cc.card_name, cc.card_number_last4, ch.name AS cardholder, cs.due_date, cs.total_amount_due,
                      cs.previous_balance, cs.new_charges, cs.interest_charged, cs.amount_paid, cs.unpaid_balance_next,
                      cs.status AS recorded_status,
                      CASE WHEN cs.paid_confirmed = 0 AND {superseded_sql('cs')} THEN 'Replaced by newer statement'
                           WHEN cs.paid_confirmed = 0 THEN 'Not yet confirmed' ELSE 'Confirmed' END AS payment_check,
                      CASE WHEN cs.status IN ('Paid', 'Cancelled') THEN cs.status
                           WHEN cs.unpaid_balance_next > 0 AND cs.amount_paid > 0.005 THEN 'Partially Paid'
                           WHEN cs.unpaid_balance_next > 0 THEN 'Unpaid'
                           WHEN cs.due_date < DATE('now', 'localtime') THEN 'Paid'
                           ELSE cs.status END AS effective_status,
                      cs.payment_method, {cc_owed_case('cs')} AS still_owed
               FROM cc_statements cs JOIN credit_cards cc ON cs.card_id = cc.card_id
               LEFT JOIN cardholders ch ON cc.cardholder_id = ch.cardholder_id
               ORDER BY cs.due_date ASC"""
        )
        sf1, sf2 = st.columns([1, 3])
        stmt_outstanding_only = sf1.toggle("Outstanding only", value=True, key="stmt_outstanding_only",
                                           help="Show only statements that still count as owed (the same rule as the payable totals). Turn off to see every statement.")
        stmt_search = sf2.text_input("Search statements", placeholder="Type a card, cardholder, bank, last 4 digits, month (2026-09) or status — several words narrow it down",
                                     key="stmt_search", label_visibility="collapsed")

        def filter_statements(df: pd.DataFrame) -> pd.DataFrame:
            """The 'Outstanding only' toggle + the search words (every word must appear somewhere in the row)."""
            out = df[df["still_owed"].astype(float) > 0.005] if stmt_outstanding_only else df
            words = (stmt_search or "").lower().split()
            if words and not out.empty:
                blob = out[["card_name", "card_number_last4", "cardholder", "statement_month", "due_date", "effective_status", "payment_method"]].astype(str).agg(" ".join, axis=1).str.lower()
                out = out[blob.apply(lambda s: all(w in s for w in words))]
            return out

        shown_statements_df = filter_statements(cc_statements_df)
        st.caption(f"Showing {len(shown_statements_df)} of {len(cc_statements_df)} statements.")
        st.dataframe(
            shown_statements_df.drop(columns=["card_id"]),
            hide_index=True,
            column_config={
                "still_owed": st.column_config.NumberColumn("Still owed", format=MONEY_FORMAT),
                "total_amount_due": st.column_config.NumberColumn("Amount due", format=MONEY_FORMAT),
                "previous_balance": st.column_config.NumberColumn("Previous balance", format=MONEY_FORMAT),
                "new_charges": st.column_config.NumberColumn("New charges", format=MONEY_FORMAT),
                "interest_charged": st.column_config.NumberColumn("Interest", format=MONEY_FORMAT),
                "amount_paid": st.column_config.NumberColumn("Paid", format=MONEY_FORMAT),
                "unpaid_balance_next": st.column_config.NumberColumn("Unpaid → next", format=MONEY_FORMAT),
            },
        )

        col_new_stmt = st.container()
        with col_new_stmt:
            render_add_cc_statement_form()
        with st.expander("Edit / delete a statement"):
            st.caption("Only the most recent statement for a card can be changed or removed here — an older one already had its balance carried forward into a later statement, so changing it would leave that later one out of sync.")
            if cc_statements_df.empty:
                st.caption("No statements recorded yet.")
            else:
                latest_stmts_df = (
                    cc_statements_df.sort_values(["due_date", "cc_statement_id"])
                    .groupby("card_id", as_index=False).tail(1)
                )
                latest_stmts_df = filter_statements(latest_stmts_df)
                st.caption("Follows the 'Outstanding only' switch and the search box above the table. You can also type in the box below to jump to a statement.")
            if not cc_statements_df.empty and latest_stmts_df.empty:
                st.caption("No statement matches — turn off 'Outstanding only' or clear the search above the table.")
            elif not cc_statements_df.empty:
                edit_stmt_id = st.selectbox(
                    "Statement", options=latest_stmts_df["cc_statement_id"],
                    format_func=lambda sid: (
                        f"{latest_stmts_df.set_index('cc_statement_id').loc[sid, 'card_name']} - "
                        f"{latest_stmts_df.set_index('cc_statement_id').loc[sid, 'card_number_last4']} — "
                        f"due {latest_stmts_df.set_index('cc_statement_id').loc[sid, 'due_date']} "
                        f"({php(latest_stmts_df.set_index('cc_statement_id').loc[sid, 'total_amount_due'])})"
                    ),
                    key="edit_stmt_pick",
                )
                cur_stmt = latest_stmts_df.set_index("cc_statement_id").loc[edit_stmt_id]
                stored_prev, stored_new, stored_int = float(cur_stmt["previous_balance"]), float(cur_stmt["new_charges"]), float(cur_stmt["interest_charged"])
                stored_total = float(cur_stmt["total_amount_due"])
                # Statements from the 2026-09-17 legacy CC import were inserted as a lump
                # total_amount_due with previous_balance/new_charges/interest_charged left at their
                # 0 default (migration 027's rolling-balance breakdown is "going-forward only, no
                # backfill" -- see add_cc_statement()). Prefilling "New charges" with 0 here would
                # silently recompute total_amount_due as 0 the moment this got saved, even from
                # just fixing the due date. Folding the whole stored total into "New charges" by
                # default keeps the recomputed total matching what was already on file.
                is_legacy_lump_sum = round(stored_prev + stored_new + stored_int, 2) != round(stored_total, 2)
                if is_legacy_lump_sum:
                    st.info(f"This statement was imported as a lump total ({php(stored_total)}) with no previous balance / new charges / interest breakdown on file. That's folded into \"New charges\" below so saving won't change the total unless you edit it.")
                    default_new = round(stored_total - stored_prev - stored_int, 2)
                else:
                    default_new = stored_new
                edit_stmt_due = st.date_input("Due date", value=date.fromisoformat(cur_stmt["due_date"]), key=f"edit_stmt_due_{edit_stmt_id}")
                es1, es2, es3 = st.columns(3)
                with es1:
                    edit_stmt_prev = st.number_input("Previous balance", min_value=0.0, step=100.0, value=stored_prev, key=f"edit_stmt_prev_{edit_stmt_id}", help='The balance carried over from the last statement, as printed.')
                with es2:
                    edit_stmt_new = st.number_input("New charges", min_value=0.0, step=100.0, value=default_new, key=f"edit_stmt_new_{edit_stmt_id}", help='Purchases and fees on this statement.')
                with es3:
                    edit_stmt_int = st.number_input("Interest charged", min_value=0.0, step=10.0, value=stored_int, key=f"edit_stmt_int_{edit_stmt_id}", help='Finance charges on this statement — also counted in the interest reports.')
                edit_stmt_paid = st.number_input("Amount paid", min_value=0.0, step=100.0, value=float(cur_stmt["amount_paid"]), key=f"edit_stmt_paid_{edit_stmt_id}", help="What was paid against this statement. If you don't know yet, the next statement's previous balance confirms it.")
                st.metric(
                    "Total amount due (computed)", php(round(edit_stmt_prev + edit_stmt_new + edit_stmt_int, 2)),
                    help="This is what Save will actually record — check it matches the real statement total before saving.",
                )

                ec1, ec2 = st.columns(2)
                with ec1:
                    if st.button("Save statement changes", icon=":material/save:", key=f"save_stmt_{edit_stmt_id}"):
                        ok, msg = edit_cc_statement(int(edit_stmt_id), edit_stmt_due, edit_stmt_prev, edit_stmt_new, edit_stmt_int, edit_stmt_paid)
                        (st.success if ok else st.error)(msg)
                        if ok:
                            refresh(done=msg)
                with ec2:
                    confirm_delete_stmt = st.checkbox("Yes, delete this statement", key=f"confirm_delete_stmt_{edit_stmt_id}")
                    if st.button("Delete this statement", icon=":material/delete:", key=f"delete_stmt_{edit_stmt_id}", disabled=not confirm_delete_stmt):
                        ok, msg = delete_cc_statement(int(edit_stmt_id))
                        (st.success if ok else st.error)(msg)
                        if ok:
                            refresh(done=msg)

    # ----------------------------------------------------------------------
    with sub_cards:
        st.subheader("Cards on file")
        st.dataframe(
            cards_df.drop(columns=["cardholder_id", "creditor_id"]),
            hide_index=True,
            column_config={
                "credit_limit": st.column_config.NumberColumn("Credit limit", format=MONEY_FORMAT),
                "annual_fee_due_month": st.column_config.NumberColumn("Annual fee month"),
                "statement_day": st.column_config.NumberColumn("Statement day"),
                "is_active": st.column_config.CheckboxColumn("Active"),
            },
        )

        if not cards_df.empty:
            with st.expander("Edit a card (name / issuer / cardholder / credit limit / annual fee month / statement day)"):
                # Pick the cardholder first, then narrow "Card" to just their cards -- faster than
                # scanning every card in the system when you already know whose card you want.
                UNASSIGNED_HOLDER = -1
                edit_card_holder_norm = cards_df["cardholder_id"].apply(_blank_to_none_int)
                holder_filter_ids = sorted(
                    {hid for hid in edit_card_holder_norm if hid is not None},
                    key=lambda hid: str(cardholders_df.set_index("cardholder_id").loc[hid, "name"]).lower(),
                )
                has_unassigned_cards = any(hid is None for hid in edit_card_holder_norm)
                holder_filter_options = [None] + holder_filter_ids + ([UNASSIGNED_HOLDER] if has_unassigned_cards else [])

                def _holder_filter_label(hid):
                    if hid is None:
                        return "All cardholders"
                    if hid == UNASSIGNED_HOLDER:
                        return "(no cardholder)"
                    row = cardholders_df.set_index("cardholder_id").loc[hid]
                    return row["name"] + ("" if row["is_active"] else " — inactive")

                edit_card_holder_filter = st.selectbox(
                    "Cardholder", options=holder_filter_options, format_func=_holder_filter_label, key="edit_card_holder_filter",
                )
                if edit_card_holder_filter is None:
                    cards_for_pick_df = cards_df
                elif edit_card_holder_filter == UNASSIGNED_HOLDER:
                    cards_for_pick_df = cards_df[edit_card_holder_norm.isnull()]
                else:
                    cards_for_pick_df = cards_df[edit_card_holder_norm == edit_card_holder_filter]

                if cards_for_pick_df.empty:
                    st.caption("No cards for this cardholder.")
                else:
                    edit_card_id = st.selectbox(
                        "Card", options=cards_for_pick_df["card_id"],
                        format_func=lambda cid: _card_label(cid, cards_for_pick_df),
                        # Keyed by the cardholder filter + card_id so switching either forces fresh
                        # widgets instead of showing a previous card's stale values after Save
                        # (silent wrong-card overwrite risk).
                        key=f"edit_card_pick_{edit_card_holder_filter}",
                    )
                    current = cards_df.set_index("card_id").loc[edit_card_id]
                    with st.form("edit_card_form", border=False):
                        edit_card_name = st.text_input("Card name", value=str(current["card_name"]), key=f"edit_card_name_{edit_card_id}")
                        issuer_names = sorted(set(creditor_options("CREDIT_CARD").values()) | {str(current["issuer"])}, key=str.lower)
                        edit_issuer = st.selectbox(
                            "Card issuer (bank)", options=issuer_names, index=issuer_names.index(str(current["issuer"])),
                            accept_new_options=True, key=f"edit_card_issuer_{edit_card_id}",
                            help="Pick the bank, or type a new name to add it.",
                        )
                        current_holder = _blank_to_none_int(current["cardholder_id"])
                        # Active cardholders only, like "Add a new card" below -- but always keep the
                        # card's currently assigned cardholder pickable (labeled inactive) even if
                        # they've since been deactivated, so reopening this form doesn't silently drop
                        # the assignment (same pattern as edit_issuer's current-value union above).
                        holder_pick_ids = list(active_cardholders_df["cardholder_id"])
                        if current_holder is not None and current_holder not in holder_pick_ids:
                            holder_pick_ids.append(current_holder)
                        holder_options = [None] + holder_pick_ids
                        edit_holder = st.selectbox(
                            "Cardholder", options=holder_options,
                            index=holder_options.index(current_holder) if current_holder in holder_options else 0,
                            format_func=lambda hid: "—" if hid is None else (
                                cardholders_df.set_index("cardholder_id").loc[hid, "name"]
                                + ("" if cardholders_df.set_index("cardholder_id").loc[hid, "is_active"] else " — inactive")
                            ),
                            key=f"edit_card_holder_{edit_card_id}",
                        )
                        edit_limit = st.number_input(
                            "Credit limit", min_value=0.0, step=10000.0,
                            value=float(current["credit_limit"]) if pd.notna(current["credit_limit"]) else 0.0,
                            help="0 shows here for a card imported with no credit limit on file — enter the real limit to save it.",
                            key=f"edit_card_limit_{edit_card_id}",
                        )
                        current_month = _blank_to_none_int(current["annual_fee_due_month"])
                        month_options = [None] + list(range(1, 13))
                        edit_af_month = st.selectbox(
                            "Annual fee due — month", options=month_options,
                            index=month_options.index(current_month) if current_month in month_options else 0,
                            format_func=lambda m: "—" if m is None else date(2000, m, 1).strftime("%B"),
                            key=f"edit_card_af_month_{edit_card_id}",
                            help='The month the annual fee is charged — you are reminded up to 2 months ahead, to ask the bank for a waiver.',
                        )
                        edit_statement_day = st.number_input(
                            "Statement day", min_value=0, max_value=31, step=1,
                            value=_blank_to_none_int(current["statement_day"]) or 0, help="0 = not set",
                            key=f"edit_card_stmt_day_{edit_card_id}",
                        )
                        if st.form_submit_button("Save card changes", icon=":material/save:"):
                            ok, msg = edit_credit_card(int(edit_card_id), edit_card_name, get_or_create_card_issuer(edit_issuer), edit_holder, edit_limit, edit_af_month, edit_statement_day or None)
                            (st.success if ok else st.error)(msg)
                            if ok:
                                refresh(done=msg)

                    if int(current["is_active"]):
                        if st.button("Deactivate this card", icon=":material/block:", key=f"deactivate_card_{edit_card_id}"):
                            deactivate_credit_card(int(edit_card_id))
                            refresh(done="Card deactivated — its statement history stays intact, but it won't appear when adding a new statement.")
                    else:
                        st.caption("This card is currently inactive.")
                        if st.button("Reactivate this card", icon=":material/refresh:", key=f"reactivate_card_{edit_card_id}"):
                            reactivate_credit_card(int(edit_card_id))
                            refresh(done="Card reactivated.")

        with st.expander("Add a new card"):
            with st.form("new_cc_card_form", border=False, clear_on_submit=True):
                st.markdown("**Add card**")
                cc_issuer_names = sorted(set(creditor_options("CREDIT_CARD").values()), key=str.lower)
                cc_issuer = st.selectbox(
                    "Card issuer (bank)", options=cc_issuer_names, index=None, accept_new_options=True, key="cc_issuer_name",
                    placeholder="Pick the bank, or type a new one", help="Type a name that isn't listed to add a new bank.",
                )
                if active_cardholders_df.empty:
                    st.caption("Add a cardholder below first.")
                    cc_holder = None
                else:
                    cc_holder = st.selectbox("Cardholder", options=active_cardholders_df["cardholder_id"], format_func=lambda hid: active_cardholders_df.set_index("cardholder_id").loc[hid, "name"])
                cc_name = st.text_input("Card name")
                cc_last4 = st.text_input("Last 4 digits", max_chars=4, help='Only the last 4 digits are kept — never type the full card number.')
                cc_limit = st.number_input("Credit limit", min_value=0.0, step=10000.0, help="The card's credit limit, from the bank.")
                cc_af_month = st.selectbox(
                    "Annual fee due — month (optional)", options=[None] + list(range(1, 13)),
                    format_func=lambda m: "—" if m is None else date(2000, m, 1).strftime("%B"),
                    help="Month only — the exact day is the card's original issuance date, which isn't tracked here.",
                )
                cc_statement_day = st.number_input(
                    "Statement day (optional)", min_value=0, max_value=31, step=1, value=0,
                    help="The nth day of the month this card's statement generates — a fixed property of the card, so it's not re-entered per statement. 0 = not set.",
                )
                if st.form_submit_button("Add card", icon=":material/credit_card:"):
                    if cc_issuer and cc_name and cc_limit > 0:
                        err = money_amount_error(cc_limit, "Credit limit")
                        if err:
                            st.error(err)
                        else:
                            confirm_create_dialog(
                                f"Add card **{cc_name}** (last 4: {cc_last4 or '—'}) from "
                                f"**{cc_issuer.strip()}** with a credit limit of **{php(cc_limit)}**?"
                                + dup_note(credit_card_warnings(cc_issuer, cc_name, cc_last4)),
                                "add_credit_card",
                                lambda a=(cc_issuer, cc_holder, cc_name, cc_last4, cc_limit, cc_af_month, cc_statement_day or None): add_credit_card(get_or_create_card_issuer(a[0]), *a[1:]),
                            )
                    else:
                        st.error("Issuer, card name, and a positive credit limit are required.")

        with st.expander("Cardholder registry"):
            st.dataframe(
                cardholders_df, hide_index=True,
                column_config={"is_active": st.column_config.CheckboxColumn("Active")},
            )
            with st.form("new_cardholder_form", border=False):
                ch_name = st.text_input("Name")
                ch_dept = st.text_input("Department (optional)")
                ch_contact = st.text_input("Contact number (optional)")
                if st.form_submit_button("Add cardholder", icon=":material/person_add:"):
                    if ch_name:
                        if guard_duplicate_submit("add_cardholder", ch_name, ch_dept, ch_contact):
                            add_cardholder(ch_name, ch_dept, ch_contact)
                            refresh(done=f"Added cardholder {ch_name}.")
                        else:
                            st.warning("Duplicate submission ignored — identical to the click just before it.")
                    else:
                        st.error("Name is required.")

            if not cardholders_df.empty:
                st.markdown("**Edit a cardholder**")
                show_inactive_holders = st.checkbox("Include inactive cardholders", key="show_inactive_holders")
                holders_pickable_df = cardholders_df if show_inactive_holders else active_cardholders_df
                if holders_pickable_df.empty:
                    st.caption("No cardholders to show.")
                else:
                    edit_holder_id = st.selectbox(
                        "Cardholder", options=holders_pickable_df["cardholder_id"],
                        format_func=lambda hid: holders_pickable_df.set_index("cardholder_id").loc[hid, "name"]
                        + ("" if holders_pickable_df.set_index("cardholder_id").loc[hid, "is_active"] else " — inactive"),
                        key="edit_holder_pick",
                    )
                    current_holder_row = holders_pickable_df.set_index("cardholder_id").loc[edit_holder_id]
                    with st.form("edit_cardholder_form", border=False):
                        edit_holder_name = st.text_input("Name", value=str(current_holder_row["name"]), key=f"edit_holder_name_{edit_holder_id}")
                        edit_holder_dept = st.text_input("Department (optional)", value=current_holder_row["department"] or "", key=f"edit_holder_dept_{edit_holder_id}")
                        edit_holder_contact = st.text_input("Contact number (optional)", value=current_holder_row["contact_number"] or "", key=f"edit_holder_contact_{edit_holder_id}")
                        if st.form_submit_button("Save cardholder changes", icon=":material/save:"):
                            ok, msg = edit_cardholder(int(edit_holder_id), edit_holder_name, edit_holder_dept, edit_holder_contact)
                            (st.success if ok else st.error)(msg)
                            if ok:
                                refresh(done=msg)

                    if int(current_holder_row["is_active"]):
                        if st.button("Deactivate this cardholder", icon=":material/block:", key=f"deactivate_holder_{edit_holder_id}"):
                            deactivate_cardholder(int(edit_holder_id))
                            refresh(done="Cardholder deactivated — their card/statement history stays intact, but they won't appear when adding a new card or statement.")
                    else:
                        st.caption("This cardholder is currently inactive.")
                        if st.button("Reactivate this cardholder", icon=":material/refresh:", key=f"reactivate_holder_{edit_holder_id}"):
                            reactivate_cardholder(int(edit_holder_id))
                            refresh(done="Cardholder reactivated.")

    # ----------------------------------------------------------------------
    with sub_check:
        cov_df = get_statement_coverage()
        cov_today = date.today()
        cov_this = cov_today.strftime("%B %Y")
        cov_prev = (cov_today.replace(day=1) - timedelta(days=1)).strftime("%B %Y")
        st.caption(
            f"Compares the {cov_prev} and {cov_this} statements of every active card, so a card that had a statement last month "
            "but has none recorded yet this month stands out. A statement's month is its statement date's month (the card's "
            "Statement day, usually the month before the due date). A month you marked \"No due\" counts as done. A card shows "
            "\"No statement ever\" until its first one is entered."
        )
        if cov_df.empty:
            st.caption("No active cards on file.")
        else:
            counts = cov_df["status"].value_counts()
            with st.container(horizontal=True):
                st.metric("Missing this month", int(counts.get("Missing this month", 0)), border=True, help=f"Had a statement in {cov_prev}, none recorded for {cov_this}.")
                st.metric("Missing both months", int(counts.get("Missing both months", 0)), border=True, help=f"Older history, but nothing in {cov_prev} or {cov_this}.")
                st.metric("No statement ever", int(counts.get("No statement ever", 0)), border=True, help='Active cards with no statement recorded at all — often cards not in use yet.')
                st.metric("Not yet expected", int(counts.get("Not yet expected", 0)), border=True, help="Had one last month, but this card's statement isn't due to arrive yet this month (based on its Statement day, or last month's due day).")
                st.metric("No due", int(counts.get("No due", 0)), border=True, help=f"You marked {cov_this} as having nothing due on this card.")
                st.metric("OK", int(counts.get("OK", 0)), border=True, help=f"A statement is recorded for {cov_this}.")
            only_problems = st.toggle("Show only cards that need attention", value=True, key="cov_only_problems")
            cov_show = cov_df[~cov_df["status"].isin(["OK", "Not yet expected", "No due"])] if only_problems else cov_df
            report_df = cov_show.rename(columns={
                "status": "Status", "cardholder": "Card holder", "issuer": "Issuer", "card_name": "Card",
                "card_number_last4": "Last 4", "prev_month_due": f"{cov_prev} statement: due", "prev_month_amount": f"{cov_prev} statement: amount",
                "this_month_due": f"{cov_this} statement: due", "this_month_amount": f"{cov_this} statement: amount", "last_due_date": "Latest due date on file",
                "expected_by": "Expected by",
            }).drop(columns=["card_id", "statement_day"])
            report_df = report_df.sort_values(["Card holder", "Status", "Issuer", "Card"]).reset_index(drop=True)
            st.dataframe(
                report_df, hide_index=True,
                column_config={
                    f"{cov_prev} statement: amount": st.column_config.NumberColumn(format=MONEY_FORMAT),
                    f"{cov_this} statement: amount": st.column_config.NumberColumn(format=MONEY_FORMAT),
                },
            )
            if report_df.empty:
                st.success("Every active card has a statement recorded for this month.")
            else:
                with st.expander("Follow-up list by card holder (copy and send)", expanded=True):
                    st.caption("One message per person, listing only the cards still missing a statement — send it so the bill can be paid on time and charges avoided.")
                    for holder, grp in report_df.groupby("Card holder"):
                        first_name = holder.split()[0] if holder and holder != "(no holder)" else "there"
                        card_lines = "\n".join(
                            f"- {r['Issuer']} {r['Card']} (ending {r['Last 4']})"
                            + (f" — last month's statement was due {r[f'{cov_prev} statement: due']}" if pd.notna(r[f"{cov_prev} statement: due"]) else "")
                            for _, r in grp.iterrows()
                        )
                        st.markdown(f"**{holder}** — {len(grp)} card(s)")
                        st.code(
                            f"Hi {first_name}, I don't have the {cov_this} statement yet for these cards:\n{card_lines}\n"
                            "Please send me a photo or PDF of the statement (or the amount due and due date) so we can pay on time and avoid charges. Thank you!",
                            language=None,
                        )
                xl = io.BytesIO()
                with pd.ExcelWriter(xl, engine="openpyxl") as writer:
                    report_df.to_excel(writer, index=False, sheet_name="Missing statements")
                    ws = writer.sheets["Missing statements"]
                    for col in ws.columns:
                        ws.column_dimensions[col[0].column_letter].width = min(max(len(str(c.value or "")) for c in col) + 2, 40)
                stamp = cov_today.strftime("%Y-%m-%d")
                d1, d2 = st.columns(2)
                d1.download_button("Download Excel report", data=xl.getvalue(), file_name=f"missing_cc_statements_{stamp}.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", icon=":material/download:", key="cov_dl_xlsx")
                d2.download_button("Download CSV", data=report_df.to_csv(index=False), file_name=f"missing_cc_statements_{stamp}.csv",
                                   mime="text/csv", icon=":material/download:", key="cov_dl_csv")
