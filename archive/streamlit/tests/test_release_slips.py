"""The check release slip (2026-10-03): the statement printed for hand-over carries a slip number; the release is recorded by that number, all the checks on it at once,
with who received them and when. A reprint of an unchanged batch keeps its number; only printed checks can be handed over; the Releaser may do it."""
import sqlite3
from datetime import date

import app
from conftest import fetchone, make_creditor
from engines import release_slip_service as rs
from engines.acting_user import acting_as
from engines.check_rules import issue_check
from engines.statement_rules import add_invoice


def _batch_with_checks(db, n=2, printed=True):
    cid = make_creditor(db, name="Westlake")
    sid = app.create_order_statement(cid)
    add_invoice(cid, "SI-1", date(2026, 10, 1), date(2026, 11, 1), 4000.0, [], sid)         # the batch must hold the money the checks pay (a check above the balance is refused)
    ids = []
    with acting_as("owner@example.com", "owner"):
        for i in range(n):
            ok, msg, chk = issue_check(cid, 1000.0 * (i + 1), date(2026, 11, 8 + i), statement_id=sid)
            assert ok, msg
            ids.append(chk)
    if printed:
        con = sqlite3.connect(db)
        con.execute(f"UPDATE checks_issued SET printed_at = '2026-10-10 10:00:00', check_number = printf('50%02d', check_id) WHERE check_id IN ({','.join(map(str, ids))})")
        con.commit()
        con.close()
    return cid, sid, ids


def test_a_slip_takes_the_next_number_and_lists_the_batchs_checks(db):
    cid, sid, ids = _batch_with_checks(db)
    with acting_as("clerk@example.com", "payables"):
        ok, msg, slip = rs.create_slip(sid)
    assert ok and slip["slip_no"] == f"RS-{date.today().year}-0001" or slip["slip_no"].endswith("-0001")
    assert slip["checks"] == 2 and slip["total"] == 3000.0
    assert fetchone(db, "SELECT COUNT(*) FROM release_slip_checks")[0] == 2
    assert list(rs.open_slips()["slip_no"]) == [slip["slip_no"]]


def test_printing_the_statement_again_reuses_the_slip_unless_the_batch_changed(db):
    cid, sid, ids = _batch_with_checks(db)
    with acting_as("owner@example.com", "owner"):
        first = rs.create_slip(sid)[2]
        assert rs.create_slip(sid)[2]["slip_no"] == first["slip_no"]                       # a reprint does not use up a number
        ok, msg, chk = issue_check(cid, 500.0, date(2026, 12, 1), statement_id=sid)
        assert ok, msg
        assert rs.create_slip(sid)[2]["slip_no"] != first["slip_no"]                       # another check on the batch: a new slip


def test_releasing_by_slip_marks_every_check_released_with_who_and_when(db):
    cid, sid, ids = _batch_with_checks(db)
    with acting_as("mother@example.com", "releaser"):
        slip = rs.create_slip(sid)[2]
        ok, msg = rs.release_slip(slip["slip_id"], "R. Cruz (Westlake)", date(2026, 10, 12))
    assert ok, msg
    rows = sqlite3.connect(db).execute("SELECT released_date, released_to FROM checks_issued WHERE check_id IN (%s)" % ",".join(map(str, ids))).fetchall()
    assert rows == [("2026-10-12", "R. Cruz (Westlake)")] * 2
    assert rs.open_slips().empty and list(rs.recent_slips()["received_by"]) == ["R. Cruz (Westlake)"]
    assert fetchone(db, "SELECT released_by FROM release_slips")[0] == "mother@example.com"
    with acting_as("mother@example.com", "releaser"):
        assert not rs.release_slip(slip["slip_id"], "someone", date(2026, 10, 13))[0]       # already collected


def test_checks_that_were_never_printed_cannot_be_handed_over(db):
    cid, sid, ids = _batch_with_checks(db, printed=False)
    with acting_as("mother@example.com", "releaser"):
        slip = rs.create_slip(sid)[2]
        ok, msg = rs.release_slip(slip["slip_id"], "R. Cruz", date(2026, 10, 12))
    assert not ok and fetchone(db, "SELECT COUNT(*) FROM checks_issued WHERE released_date IS NOT NULL")[0] == 0
    assert len(rs.open_slips()) == 1                                                       # the slip stays open


def test_a_name_is_required_and_the_roles_that_may_not_release_are_refused(db):
    cid, sid, ids = _batch_with_checks(db)
    with acting_as("buyer@example.com", "purchasing"):
        assert not rs.create_slip(sid)[0]
    with acting_as("mother@example.com", "releaser"):
        slip = rs.create_slip(sid)[2]
        assert not rs.release_slip(slip["slip_id"], "  ", date(2026, 10, 12))[0]
    with acting_as("viewer@example.com", "viewer"):
        assert not rs.release_slip(slip["slip_id"], "X", date(2026, 10, 12))[0]


def test_slips_waiting_longer_than_a_week_are_counted(db):
    cid, sid, ids = _batch_with_checks(db)
    with acting_as("owner@example.com", "owner"):
        rs.create_slip(sid)
    assert rs.waiting_over(7) == 0
    con = sqlite3.connect(db)
    con.execute("UPDATE release_slips SET printed_at = '2026-01-01 08:00:00'")
    con.commit()
    con.close()
    assert rs.waiting_over(7) == 1


def _release_app():
    import streamlit as st

    from views import release

    st.session_state.setdefault("auth_user", {"user_id": 1, "email": "mother@example.com", "name": "Mama", "role": st.session_state.get("_role", "releaser")})
    release.render()


def test_the_release_page_lists_the_slips_to_collect_and_releases_one(db):
    from streamlit.testing.v1 import AppTest
    cid, sid, ids = _batch_with_checks(db)
    with acting_as("mother@example.com", "releaser"):
        slip = rs.create_slip(sid)[2]
    at = AppTest.from_function(_release_app, default_timeout=30).run()
    assert not at.exception, at.exception
    assert any(b.key == "slip_go" for b in at.button)
    at.session_state["slip_pick"] = slip["slip_id"]
    at.session_state["slip_who"] = "R. Cruz"
    at.session_state["slip_date"] = date(2026, 10, 12)                                   # after the checks were printed (the test prints them on Oct 10)
    at = at.run()
    go = next(b for b in at.button if b.key == "slip_go")
    assert not go.proto.disabled
    at = go.click().run()
    assert not at.exception, at.exception
    assert rs.open_slips().empty and fetchone(db, "SELECT COUNT(*) FROM checks_issued WHERE released_to = 'R. Cruz'")[0] == 2


def test_home_warns_when_a_slip_has_waited_over_a_week(db):
    from views.home import _notices
    cid, sid, ids = _batch_with_checks(db)
    with acting_as("owner@example.com", "owner"):
        rs.create_slip(sid)
        assert not any("release slip" in n for n in _notices("owner"))
    con = sqlite3.connect(db)
    con.execute("UPDATE release_slips SET printed_at = '2026-01-01 08:00:00'")
    con.commit()
    con.close()
    with acting_as("owner@example.com", "owner"):
        assert any("1 release slip(s)" in n for n in _notices("owner"))


def _loan_note_with_printed_checks(db, n=2):
    from engines.loan_note_rules import create_loan_note, promote_draft_loan_note
    from engines.loan_schedule_service import generate_schedule, issue_all_pdcs
    cid = make_creditor(db, name="Slip Lender", creditor_type="LENDER", terms="0")
    ok, msg, lnid = create_loan_note(cid, date(2026, 10, 1), 20000.0, 0.0)
    promote_draft_loan_note(lnid)
    generate_schedule(lnid, date(2026, 11, 1), n)
    with acting_as("clerk@example.com", "payables"):
        assert issue_all_pdcs(lnid)[0]
    con = sqlite3.connect(db)
    con.execute("UPDATE checks_issued SET printed_at = '2026-10-10 10:00:00', check_number = printf('70%02d', check_id) WHERE loan_note_id = ?", (lnid,))
    con.commit()
    con.close()
    return cid, lnid


def test_a_loan_note_gets_a_slip_that_lists_its_checks_and_is_released_the_same_way(db):
    cid, lnid = _loan_note_with_printed_checks(db)
    with acting_as("mother@example.com", "releaser"):
        ok, msg, slip = rs.create_slip(loan_note_id=lnid)
        assert ok and slip["checks"] == 2 and slip["total"] == 20000.0, msg
        assert rs.create_slip(loan_note_id=lnid)[2]["slip_no"] == slip["slip_no"]                         # printing again keeps the number
        assert not rs.create_slip()[0] and not rs.create_slip(1, loan_note_id=lnid)[0]                      # one batch or one loan note, not neither or both
        assert rs.open_slips().iloc[0]["statement"].startswith("20")                                         # shown with the loan note's own reference
        ok, msg = rs.release_slip(slip["slip_id"], "Richie", date(2026, 10, 12))
        assert ok, msg
    assert fetchone(db, "SELECT COUNT(*) FROM checks_issued WHERE loan_note_id = ? AND released_to = 'Richie'", (lnid,))[0] == 2


def test_the_loan_note_slip_prints_three_copies_with_the_checks_the_total_and_the_signature(db):
    from engines.thermal_print_service import print_loan_release_slip
    from engines.thermal_statement import COLUMNS, build_loan_release_slip, get_printer_config, render_text
    cid, lnid = _loan_note_with_printed_checks(db)
    text = render_text(build_loan_release_slip(lnid, "FINANCE", "RS-2026-0042"))
    assert "FINANCE COPY" in text and "Release slip: RS-2026-0042" in text and "TOTAL (2 checks)" in text and "Please issue a collection receipt." in text
    assert "Signature:" in text and max(len(line) for line in text.splitlines()) <= COLUMNS
    res = print_loan_release_slip(lnid, ["SUPPLIER", "FILE", "FINANCE"], "dummy", get_printer_config(), slip_no="RS-2026-0042")
    assert res["done"] == ["SUPPLIER", "FILE", "FINANCE"] and all(res["jobs"].values())
    assert build_loan_release_slip(99999, "FILE") is None


def test_payment_run_shows_the_release_stage_after_statements(db):
    from test_workflows_pages import _open
    cid, sid, ids = _batch_with_checks(db)
    with acting_as("owner@example.com", "owner"):
        rs.create_slip(sid)
    for role in ("owner", "payables"):
        at = _open("payment-run", role)
        assert not at.exception, (role, at.exception)
        assert any(b.key == "pr_release_btn" for b in at.button), role                         # the last stage of the cycle: slips printed, not yet collected
