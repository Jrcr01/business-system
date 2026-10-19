"""A memory that lasts one page view (2026-10-19, speed): a page that only reads (GET) and works out the same thing several times -- the payment
schedule on the order calendar was read 5 times for one screen -- works it out once. Saves (POST) never use it, so they always see fresh figures.
Outside a page view (scripts, tests, the scheduler) nothing is kept: every call works it out again, as before."""
from __future__ import annotations

import contextvars

_store: contextvars.ContextVar = contextvars.ContextVar("slicks_page_memo", default=None)


def begin():
    return _store.set({})


def end(token) -> None:
    _store.reset(token)


def memo(key, fn):
    """fn() once per page view for this key; the same object after that (callers that change the result must copy it)."""
    d = _store.get()
    if d is None:
        return fn()
    if key not in d:
        d[key] = fn()
    return d[key]
