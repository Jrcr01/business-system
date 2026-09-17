"""guard_duplicate_submit() -- fingerprints submitted values and swallows an
identical resubmission within a cooldown window. Uses st.session_state, which
works fine outside a real Streamlit run (verified: a plain dict-like proxy),
but persists across tests in the same process, so every test here relies on
the `db` fixture's st.session_state.clear() even though it doesn't touch the
database itself."""
import time

import app


def test_first_submission_always_goes_through(db):
    assert app.guard_duplicate_submit("action_a", "x", 1) is True


def test_identical_resubmission_within_cooldown_is_blocked(db):
    assert app.guard_duplicate_submit("action_b", "x", 1) is True
    assert app.guard_duplicate_submit("action_b", "x", 1) is False


def test_different_values_are_not_blocked(db):
    assert app.guard_duplicate_submit("action_c", "x", 1) is True
    assert app.guard_duplicate_submit("action_c", "x", 2) is True


def test_different_action_keys_are_independent(db):
    """Two different forms submitting the same values shouldn't interfere --
    the fingerprint is scoped per action_key."""
    assert app.guard_duplicate_submit("action_d1", "same", 1) is True
    assert app.guard_duplicate_submit("action_d2", "same", 1) is True


def test_resubmission_after_cooldown_expires_goes_through(db):
    assert app.guard_duplicate_submit("action_e", "x", cooldown_seconds=0.05) is True
    time.sleep(0.1)
    assert app.guard_duplicate_submit("action_e", "x", cooldown_seconds=0.05) is True


def test_same_values_after_a_different_intervening_submission_goes_through(db):
    """A -> B -> A again is three genuinely distinct submissions in a row, not
    a double-click -- only back-to-back *identical* submissions get swallowed."""
    assert app.guard_duplicate_submit("action_f", "x") is True
    assert app.guard_duplicate_submit("action_f", "y") is True
    assert app.guard_duplicate_submit("action_f", "x") is True
