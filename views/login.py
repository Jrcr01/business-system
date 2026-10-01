"""The sign-in gate (B8 phase 1). Until someone is signed in, the app shows only this: the first-run owner setup, the sign-in form, or "choose your own password". Once in, every
change is recorded under that person's email (the audit log), and the menu and the sidebar are cut down to their role (engines/permissions.py).

A signed-in session lives in the browser tab: reloading the page asks for the password again. That is deliberate for now; phase 2 (Google sign-in at hosting) keeps people signed in."""
from __future__ import annotations

import streamlit as st

from engines import auth_service as au
from engines.acting_user import OWNER_EMAIL, set_acting

ROLE_LABEL = {"owner": "Payables (owner)", "purchasing": "Purchasing"}


def current_user() -> dict | None:
    return st.session_state.get("auth_user")


def current_role() -> str | None:
    """The signed-in role. Outside a real page run (tests, scripts) there is no sign-in, so the owner's."""
    user = current_user()
    return user["role"] if user else ("owner" if not st.runtime.exists() else None)


def _centered():
    _, mid, _ = st.columns([1, 1.4, 1])
    return mid


def _setup() -> None:
    with _centered():
        st.header("Welcome")
        st.write("This is the first time the system is opened with sign-in. Create the owner account: it can add other people and sees everything.")
        with st.form("setup_owner"):
            email = st.text_input("Your email", value=OWNER_EMAIL)
            name = st.text_input("Your name")
            pw = st.text_input("Choose a password", type="password", help="At least 8 characters, with letters and digits.")
            pw2 = st.text_input("Type it again", type="password")
            go = st.form_submit_button("Create the owner account", type="primary")
        if go:
            if pw != pw2:
                st.error("The two passwords are not the same.")
            else:
                ok, msg, _ = au.create_owner(email, name, pw)
                if not ok:
                    st.error(msg)
                else:
                    ok, user, msg = au.authenticate(email, pw)
                    if ok:
                        st.session_state["auth_user"] = user
                        st.rerun()
                    st.error(msg)


def _sign_in() -> None:
    with _centered():
        st.header("Sign in")
        with st.form("sign_in"):
            email = st.text_input("Email")
            pw = st.text_input("Password", type="password")
            go = st.form_submit_button("Sign in", type="primary")
        if go:
            ok, user, msg = au.authenticate(email, pw)
            if ok:
                st.session_state["auth_user"] = user
                st.rerun()
            st.error(msg)
        st.caption("Forgot your password? Ask the owner for a new temporary one.")


def _must_change(user: dict) -> None:
    with _centered():
        st.header("Choose your own password")
        st.write(f"Hello {user['name']}. The password you were given is temporary: choose one only you know.")
        with st.form("must_change"):
            old = st.text_input("The temporary password", type="password")
            new = st.text_input("Your new password", type="password", help="At least 8 characters, with letters and digits.")
            new2 = st.text_input("Type the new password again", type="password")
            go = st.form_submit_button("Save and continue", type="primary")
        if go:
            if new != new2:
                st.error("The two new passwords are not the same.")
            else:
                ok, msg = au.change_password(user["user_id"], old, new)
                if ok:
                    st.session_state["auth_user"] = au.get_user(user["user_id"])
                    st.rerun()
                st.error(msg)
        if st.button("Sign out", key="must_change_out"):
            sign_out()


def sign_out() -> None:
    for k in ("auth_user",):
        st.session_state.pop(k, None)
    st.rerun()


def _blank() -> None:
    pass


def _hide_menu() -> None:
    """While nobody is signed in, replace the sidebar menu with an empty one: otherwise the menu of the person who just signed out stays on screen."""
    st.navigation([st.Page(_blank, title="Sign in", url_path="sign-in")], position="hidden")


def gate() -> dict:
    """Returns the signed-in person, or shows the right screen and stops the run. Outside a real page run (tests importing the app) there is nobody to ask."""
    if not st.runtime.exists():
        return {"user_id": 0, "email": OWNER_EMAIL, "name": "Owner", "role": "owner", "is_active": 1, "must_change_password": 0}
    user = current_user()
    if user:
        fresh = au.get_user(user["user_id"])                         # the owner may have switched them off, or changed their role, since they signed in
        if fresh is None or not fresh["is_active"]:
            st.session_state.pop("auth_user", None)
            user = None
        else:
            user = st.session_state["auth_user"] = fresh
    if user is None:
        _hide_menu()
        _setup() if au.needs_setup() else _sign_in()
        st.stop()
    if user["must_change_password"]:
        _hide_menu()
        _must_change(user)
        st.stop()
    set_acting(user["email"])                                         # every change this run is recorded under their email
    return user


def render_account_box(user: dict) -> None:
    """In the sidebar: who is signed in, change my password, sign out."""
    st.caption(f":material/person: **{user['name']}** · {ROLE_LABEL.get(user['role'], user['role'])}")
    with st.expander("My account", icon=":material/lock:"):
        with st.form("change_my_password", clear_on_submit=True):
            old = st.text_input("Current password", type="password")
            new = st.text_input("New password", type="password")
            go = st.form_submit_button("Change password")
        if go:
            ok, msg = au.change_password(user["user_id"], old, new)
            (st.success if ok else st.error)(msg)
        if st.button("Sign out", icon=":material/logout:", key="sign_out_btn", width="stretch"):
            sign_out()
