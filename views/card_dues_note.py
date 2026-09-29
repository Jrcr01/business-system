"""Card dues next to typed check dates (owner, 2026-09-29: "the objective here is to help me plan for checks"). Under every grid where a
check date is typed -- Issue checks, Edit parked checks, Corrections, and the same for loan notes -- the card dues due in the week of
each date (on statements + estimated, engines/cc_forecast_service.py), with a warning when it is a heavy card week. A heads-up only:
nothing is blocked."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from engines.cc_forecast_service import HEAVY_FACTOR, card_week_info
from engines.common import php


def render_card_week_notes(check_dates) -> None:
    dates = [d for d in (pd.to_datetime(pd.Series(list(check_dates)), errors="coerce").dt.date if len(check_dates) else []) if pd.notna(d)]
    try:
        info = card_week_info(dates)
    except Exception:                          # a database without card data yet: say nothing rather than break the grid
        return
    if not info:
        return
    quiet = []
    for w in info:
        when = ", ".join(f"{d:%b %d}" for d in w["dates"])
        detail = (f"card dues about {php(w['total'])}"
                  + (f" ({php(w['recorded'])} on statements + {php(w['estimated'])} estimated)" if w["recorded"] and w["estimated"]
                     else " (estimated)" if w["estimated"] else " (on statements)" if w["recorded"] else ""))
        if w["heavy"]:
            st.warning(f"Check date {when}: the week of {w['week_start']:%b %d} is a **heavy credit card week** — {detail}, about "
                       f"{w['total'] / w['average']:.1f}× a normal week ({php(w['average'])}). Consider another week for a big check.",
                       icon=":material/credit_card:")
        else:
            quiet.append(f"{when}: {detail if w['total'] else 'no card dues'}")
    if quiet:
        st.caption(":material/credit_card: Card dues in the week of each check date — " + " · ".join(quiet)
                   + f". A week is flagged when it is at least {HEAVY_FACTOR:g}× a normal week.")
