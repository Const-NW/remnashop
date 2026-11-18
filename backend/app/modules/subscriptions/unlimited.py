from calendar import monthrange
from datetime import datetime, timedelta
from typing import Final

from app.core.utils.time import add_term

# the panel needs a date; this one reads as "forever" and matches subscriptions
# migrated from the old version
UNLIMITED_EXPIRE_YEAR: Final[int] = 2099


def expiry_after(start: datetime, days: int, *, exact_days: bool) -> datetime:
    """The expiry a term of `days` gives from `start`; 0 is an unlimited term. A plan
    term counts whole months and years by the calendar; `exact_days` adds plain days."""
    if days < 0:
        raise ValueError(f"A term can't be {days} days")
    if days > 0:
        return start + timedelta(days=days) if exact_days else add_term(start, days)
    last_day = monthrange(UNLIMITED_EXPIRE_YEAR, start.month)[1]
    return start.replace(year=UNLIMITED_EXPIRE_YEAR, day=min(start.day, last_day))


def term_days(start: datetime, expire_at: datetime, days: int) -> int:
    """The days a term of `days` actually added from `start`; 0 stays unlimited."""
    return 0 if days == 0 else round((expire_at - start) / timedelta(days=1))


def is_unlimited_expiry(expire_at: datetime) -> bool:
    return expire_at.year >= UNLIMITED_EXPIRE_YEAR
