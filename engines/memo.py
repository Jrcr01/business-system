"""Remember a read until the database changes (2026-10-02, speed pass). Many pages ask the same heavy question several times per click (the payment calendar,
the payment-run stages ...). `@until_saved(module)` keeps the last answer per arguments, and throws it away as soon as anything is saved to the database file
(its write-ahead log changes) or the day changes. It never serves a value older than the last save, so it is safe for figures that must be current.

Pure Python, no Streamlit: the same in tests, scripts and the app. The answer is shared by everyone using the app on this computer; callers must not change
the DataFrame they get back (they get a copy)."""
from __future__ import annotations

import functools

import pandas as pd


def until_saved(module_name: str, max_entries: int = 32):
    """Decorator. module_name = the module whose DB_PATH the function reads (tests point each module at their own database)."""
    def deco(fn):
        store: dict = {}

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            import sys

            from engines.clock import manila_today
            from engines.settings_store import _stamp
            path = getattr(sys.modules[module_name], "DB_PATH")
            key = (path, args, tuple(sorted(kwargs.items())))
            stamp = (manila_today(), _stamp(path))
            hit = store.get(key)
            if hit is None or hit[0] != stamp:
                if len(store) >= max_entries:
                    store.clear()
                hit = (stamp, fn(*args, **kwargs))
                store[key] = hit
            value = hit[1]
            return value.copy() if isinstance(value, (pd.DataFrame, dict, list)) else value

        wrapper.forget = store.clear
        return wrapper
    return deco
