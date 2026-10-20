"""The one page of the system that opens with no sign-in (owner, 2026-10-14): the supplier's QR link. /s/<code>/ shows the supplier copy of the one
payment statement that code belongs to, as it stands now (so a supplier sees which checks have cleared). Nothing else is reachable from it. The
rules -- the code, how long it lives, switching it off, recording each opening -- are engines/statement_links.py."""
from __future__ import annotations

import time

from django.http import HttpResponse

GONE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>{name}</title></head>
<body style="font-family:system-ui,sans-serif;max-width:420px;margin:15vh auto;padding:0 20px;text-align:center;color:#1c2733">
<h2 style="color:#0f2a4a">{name}</h2><p>{text}</p><p style="color:#64748b;font-size:.9rem">Please ask {name} for a new copy.</p></body></html>"""
WORDS = {"expired": "This link has expired.", "revoked": "This link is no longer in use.", "off": "This link is not in use.", "none": "This link is not valid."}


def _plain(text: str, status: int) -> HttpResponse:
    from html import escape
    from engines.company import name
    resp = HttpResponse(GONE.format(text=text, name=escape(name())), status=status)
    resp["X-Robots-Tag"] = "noindex, nofollow"
    resp["Cache-Control"] = "no-store"
    return resp


def supplier_link(request, token: str):
    from engines import statement_links as sl
    from engines.batch_statement_service import generate_batch_statement_pdf
    m = request.META
    ip = (m.get("HTTP_CF_CONNECTING_IP") or m.get("REMOTE_ADDR") or "")[:45]
    now = time.time()
    if sl.too_many_misses(ip, now):
        return _plain("Too many tries. Please try again later.", 429)
    sid, why = sl.resolve(token)
    if sid is None:
        if why == "none":
            sl.note_miss(ip, now)
        return _plain(WORDS.get(why, WORDS["none"]), 410 if why in ("expired", "revoked") else 404)
    data = generate_batch_statement_pdf(sid, True, "supplier")
    if not data:
        return _plain(WORDS["none"], 404)
    from core.views import _browser
    sl.log_open(sid, ip, m.get("HTTP_CF_IPCOUNTRY", ""), _browser(m.get("HTTP_USER_AGENT", "")))
    resp = HttpResponse(data, content_type="application/pdf")
    resp["Content-Disposition"] = 'inline; filename="payment_statement.pdf"'
    resp["X-Robots-Tag"] = "noindex, nofollow"
    resp["Cache-Control"] = "no-store"
    resp["Referrer-Policy"] = "no-referrer"
    return resp
