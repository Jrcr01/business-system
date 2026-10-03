"""Fewer reports (2026-10-03): the supplier, lender, bank loan and card statement-of-account reports are one report with a 'whose' switch; an old report name (a saved link, a Home action)
still opens the right one."""
from streamlit.testing.v1 import AppTest


def _app():
    import streamlit as st

    from views import reports

    st.session_state.setdefault("auth_user", {"user_id": 1, "email": "a@b.c", "name": "A", "role": "admin"})
    reports.render()


def _run(**state) -> AppTest:
    at = AppTest.from_function(_app, default_timeout=60)
    for k, v in state.items():
        at.session_state[k] = v
    return at.run()


def test_the_report_list_is_shorter_and_has_one_statement_of_account_report(db):
    from views.reports import REPORT_OPTIONS, SOA_REPORT
    assert len(REPORT_OPTIONS) == 8 and REPORT_OPTIONS.count(SOA_REPORT) == 1
    assert not [o for o in REPORT_OPTIONS if o.startswith(("Supplier —", "Private lender —", "Bank loan —", "Credit card —"))]


def test_every_old_report_name_opens_the_matching_view_of_the_new_report(db):
    from views.reports import OLD_REPORTS, SOA_REPORT, SOA_VIEWS
    expect = {"Supplier — invoice & check history": "Supplier history", "Supplier — rundown (paid / partial / pending)": "Supplier rundown",
              "Private lender — loan note & check history": "Private lender", "Bank loan — installment history": "Bank loan", "Credit card — statement history": "Credit card"}
    for old, view in expect.items():
        at = _run(report_type_picker=old)
        assert not at.exception, (old, at.exception)
        assert at.session_state["report_type_picker"] == SOA_REPORT and at.session_state["rpt_soa_view"] == view
    assert set(expect.values()) == set(SOA_VIEWS) and all(OLD_REPORTS[o][0] == SOA_REPORT for o in expect)


def test_the_old_schedule_of_payments_report_name_goes_to_payables_due(db):
    for old in ("Schedule of payments — all tracks", "Parked checks (not yet printed)"):
        at = _run(report_type_picker=old)
        assert not at.exception, (old, at.exception)
        assert at.session_state["_global_jump"] == {"page": "payables-due", "state": {"pd_tab": "Everything owed"}}
