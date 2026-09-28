"""The one clock (engines/clock.py) as time-zone-aware datetimes, for Django fields (USE_TZ = True stores them as UTC)."""
from datetime import datetime

from engines.clock import MANILA, manila_now


def now_aware() -> datetime:
    """Now in Manila, aware -- follows engines.clock.set_fake_now in tests. Use for every Django DateTimeField default."""
    return manila_now().replace(tzinfo=MANILA)
