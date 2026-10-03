"""Read-only timing of the heavy pieces behind the pages (2026-10-02). Run against the sandbox copy:

    set AP_DB_PATH=data\\ap_disbursement_TEST.db
    .venv\\Scripts\\python.exe tools\\time_pages.py

Prints milliseconds per piece, so a speed change can be measured before and after. It only reads."""
import os
import sys
import time
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
assert os.environ.get("AP_DB_PATH"), "set AP_DB_PATH to the database to time (use the sandbox copy)"

from engines import attention_service, data_health_service, home_tiles, payment_run_service, po_rules, price_template_service, product_catalog  # noqa: E402
from engines.cash_insights_service import scheduled_outflows  # noqa: E402
from engines.clock import manila_today  # noqa: E402
from engines.reports_service import get_payment_calendar_daily_totals  # noqa: E402


def timed(label, fn, *args, **kwargs):
    start = time.perf_counter()
    fn(*args, **kwargs)
    ms = (time.perf_counter() - start) * 1000
    print(f"{label:45s} {ms:9.1f} ms")
    return ms


today = manila_today()
pay = today + timedelta(days=150)
supplier = int(os.environ.get("TIME_SUPPLIER", "28"))
total = 0.0
total += timed("Dashboard tile: attention_count", attention_service.attention_count)
total += timed("Data health tile: open_count", data_health_service.open_count)
total += timed("Payment run badge: waiting_count", lambda: payment_run_service.waiting_count(payment_run_service.get_run_stages()))
total += timed("Sidebar mini calendar data", get_payment_calendar_daily_totals)
total += timed("scheduled_outflows 120 days", scheduled_outflows, today, today + timedelta(days=120))
total += timed("New order: weeks_strip", po_rules.weeks_strip, pay)
total += timed("New order: week_schedule", po_rules.week_schedule, pay)
total += timed("New order: days_strip", po_rules.days_strip, pay)
total += timed("New order: delivery_for_week x8", lambda: [po_rules.delivery_for_week(supplier, pay - timedelta(days=pay.weekday()) + timedelta(weeks=w), today) for w in range(-3, 5)])
total += timed("Price lists: supplier_prices (Tires)", product_catalog.supplier_prices, None, "Tires")
total += timed("Price lists: build_template (supplier)", price_template_service.build_template, supplier)
for tile in getattr(home_tiles, "TILES", []):
    if getattr(tile, "figure", None):
        total += timed(f"Home tile {tile.page_id}", tile.figure)
print(f"{'TOTAL':45s} {total:9.1f} ms")
