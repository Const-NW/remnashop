from calendar import monthrange
from datetime import datetime, timedelta
from typing import Final, Optional

from app.core.utils.time import add_term, anchored_day, is_calendar_term

# the panel needs a date; this one reads as "forever" and matches subscriptions
# migrated from the old version
UNLIMITED_EXPIRE_YEAR: Final[int] = 2099


def expiry_after(
    start: datetime, days: int, *, exact_days: bool, anchor_day: Optional[int] = None
) -> datetime:
    """The expiry a term of `days` gives from `start`; 0 is an unlimited term. A plan
    term counts whole months and years by the calendar, towards the subscription's
    `anchor_day`; `exact_days` adds plain days."""
    if days < 0:
        raise ValueError(f"A term can't be {days} days")
    if days > 0:
        return start + timedelta(days=days) if exact_days else add_term(start, days, anchor_day)
    last_day = monthrange(UNLIMITED_EXPIRE_YEAR, start.month)[1]
    return start.replace(year=UNLIMITED_EXPIRE_YEAR, day=min(start.day, last_day))


def term_anchor(
    start: datetime, days: int, *, exact_days: bool, anchor_day: Optional[int] = None
) -> Optional[int]:
    """The anchor day a term from `start` leaves on the row; None when it isn't a
    calendar term and the row keeps what it had."""
    if days <= 0 or exact_days or not is_calendar_term(days):
        return None
    return anchored_day(start, anchor_day)


def term_days(start: datetime, expire_at: datetime, days: int) -> int:
    """The days a term of `days` actually added from `start`, a calendar month being
    28-31 of them; 0 stays unlimited."""
    return 0 if days == 0 else round((expire_at - start) / timedelta(days=1))


def is_unlimited_expiry(expire_at: datetime) -> bool:
    return expire_at.year >= UNLIMITED_EXPIRE_YEAR
