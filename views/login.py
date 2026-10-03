"""The sign-in gate (B8 phase 1). Until someone is signed in, the app shows only this: the first-run owner setup, the sign-in form, or "choose your own password". Once in, every
change is recorded under that person's email (the audit log), and the menu and the sidebar are cut down to their role (engines/permissions.py).

A signed-in session lives in the browser tab: reloading the page asks for the password again. That is deliberate for now; phase 2 (Google sign-in at hosting) keeps people signed in."""
from __future__ import annotations

import base64
import os

import streamlit as st

from engines import auth_service as au
from engines.acting_user import OWNER_EMAIL, register_fallback, set_acting


def _tab_user() -> dict | None:
    """Who is signed in to this browser tab (for button callbacks and pop-ups, which run before gate())."""
    return st.session_state.get("auth_user") if st.runtime.exists() else None


register_fallback(_tab_user)

from engines.permissions import ROLE_LABEL


def current_user() -> dict | None:
    return st.session_state.get("auth_user")


def current_email() -> str | None:
    user = current_user()
    return (user or {}).get("email") or None


def can_now(action: str, created_by: str | None = None) -> bool:
    """May the person signed in to this tab do it? The one check every screen uses to show or hide a button (the engines check again when it is pressed).
    Outside a real page run (tests, scripts) it is the owner."""
    from engines.permissions import can
    return can(current_role(), action, created_by=created_by, me=current_email())


def current_role() -> str | None:
    """The signed-in role. Outside a real page run (tests, scripts) there is no sign-in, so the System admin's."""
    user = current_user()
    return user["role"] if user else ("admin" if not st.runtime.exists() else None)


BACKGROUND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "login_bg.jpg")


@st.cache_resource
def _background_css() -> str:
    """The shop's photo behind the sign-in screens (assets/login_bg.jpg), darkened a little so the form reads well; the form sits on a white card. No file: plain page."""
    try:
        with open(BACKGROUND, "rb") as fh:
            encoded = base64.b64encode(fh.read()).decode()
    except OSError:
        return ""
    return ("<style>.stApp{background:linear-gradient(rgba(10,25,50,.45),rgba(10,25,50,.45)),url(data:image/jpeg;base64," + encoded + ") center/cover no-repeat fixed;}"
            "[data-testid='stHeader']{background:transparent;}"
            ".st-key-login_card{background:rgba(255,255,255,.95);border-radius:14px;padding:1.6rem 1.8rem;box-shadow:0 10px 40px rgba(0,0,0,.35);margin-top:6vh;}</style>")


def _centered():
    css = _background_css()
    if css:
        st.markdown(css, unsafe_allow_html=True)
    _, mid, _ = st.columns([1, 1.4, 1])
    with mid:
        return st.container(key="login_card")


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


def _sign_in_off() -> bool:
    """The test copy can skip the sign-in (run_test_streamlit.bat sets AP_SKIP_LOGIN=1) so trying things out is no hassle. Only ever on a database whose file name says
    _TEST: on any other file the setting is ignored and the sign-in stays."""
    return os.environ.get("AP_SKIP_LOGIN") == "1" and "_test" in os.path.basename(os.environ.get("AP_DB_PATH", "")).lower()


def _apply_view_as(user: dict) -> dict:
    """Developer view: the System admin can look at the system as another role (menu, pages, buttons and the rules' permission checks all behave as that role) without
    changing anyone's role. The real person stays the admin: changes are still recorded under their email. Only a real System admin can do this."""
    from engines.permissions import ROLES
    choice = st.session_state.get("view_as")
    if user.get("role") == "admin" and choice in ROLES and choice != "admin":
        return {**user, "role": choice, "real_role": "admin"}
    return user


def _set_view_as(role: str) -> None:
    if role == "admin":
        st.session_state.pop("view_as", None)
    else:
        st.session_state["view_as"] = role


def gate() -> dict:
    """Returns the signed-in person, or shows the right screen and stops the run. Outside a real page run (tests importing the app) there is nobody to ask."""
    if not st.runtime.exists():
        return {"user_id": 0, "email": OWNER_EMAIL, "name": "System admin", "role": "admin", "is_active": 1, "must_change_password": 0}
    if _sign_in_off():
        user = st.session_state["auth_user"] = {"user_id": 0, "email": OWNER_EMAIL, "name": "System admin (sign-in is off)", "role": "admin", "is_active": 1, "must_change_password": 0}
        user = st.session_state["auth_user"] = _apply_view_as(user)
        set_acting(user["email"], user["role"])
        return user
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
    user = st.session_state["auth_user"] = _apply_view_as(user)
    set_acting(user["email"], user["role"])                           # every change this run is recorded under their email, and the permission guards know their role
    return user


def render_account_box(user: dict) -> None:
    """In the sidebar: who is signed in, change my password, sign out."""
    st.caption(f":material/person: **{user['name']}** · {ROLE_LABEL.get(user['role'], user['role'])}")
    if user.get("real_role") == "admin" or user.get("role") == "admin":
        from engines.permissions import ROLES
        viewing = user["role"] if user.get("real_role") == "admin" else "admin"
        with st.expander("Developer view" + (f": as {ROLE_LABEL[viewing]}" if viewing != "admin" else ""), icon=":material/visibility:", expanded=viewing != "admin"):
            st.caption("Look at the system as another role, to see what they see. Nothing about anyone's role changes, and the rules refuse what that role may not do.")
            pick = st.selectbox("See it as", list(ROLES), index=list(ROLES).index(viewing), format_func=lambda r: ROLE_LABEL[r] + (" (me)" if r == "admin" else ""),
                                key="view_as_pick")
            if pick != viewing:
                _set_view_as(pick)
                st.rerun()
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
