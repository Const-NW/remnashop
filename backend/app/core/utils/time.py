from calendar import monthrange
from datetime import datetime, time, timedelta
from typing import Literal, Optional, overload

from app.core.constants import TIMEZONE


def datetime_now() -> datetime:
    return datetime.now(tz=TIMEZONE)


def truncate_to_ms(value: datetime) -> datetime:
    # Remnawave stores timestamps with millisecond precision
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


@overload
def range_start_utc(value: datetime) -> datetime: ...
@overload
def range_start_utc(value: None) -> None: ...
def range_start_utc(value: Optional[datetime]) -> Optional[datetime]:
    # a bare "YYYY-MM-DD" parses naive; asyncpg would read that as server
    # local time, so it's pinned to UTC
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=TIMEZONE)


@overload
def range_end_utc(value: datetime) -> datetime: ...
@overload
def range_end_utc(value: None) -> None: ...
def range_end_utc(value: Optional[datetime]) -> Optional[datetime]:
    # Exclusive upper bound, compared with "<". Same UTC assumption as
    # range_start_utc(), but a bare date is the *whole* day being asked for,
    # so it becomes the next day's midnight; a naive time of day stays as given.
    if value is None or value.tzinfo is not None:
        return value
    end = value.replace(tzinfo=TIMEZONE)
    return end + timedelta(days=1) if end.time() == time(0) else end


def period_parts(days: int) -> tuple[Literal["unlimited", "year", "month", "day"], int]:
    """A plan duration as it reads: years or months when it divides evenly, else days."""
    if days == 0:
        return "unlimited", 0
    if days % 365 == 0:
        return "year", days // 365
    if days % 30 == 0:
        return "month", days // 30
    return "day", days


def anchored_day(start: datetime, anchor_day: Optional[int]) -> int:
    """The day of the month a calendar term from `start` lands on: the anchor while
    `start` still sits on it (or on its month's last day when the month is shorter),
    else `start`'s own day, the anchor lost to an edit or a plain-day extension."""
    if anchor_day is not None and start.day == min(
        anchor_day, monthrange(start.year, start.month)[1]
    ):
        return anchor_day
    return start.day


def is_calendar_term(days: int) -> bool:
    return period_parts(days)[0] in ("year", "month")


def add_term(start: datetime, days: int, anchor_day: Optional[int] = None) -> datetime:
    """`days` as period_parts reads them: years and months are calendar ones, landing
    on the anchor day of the month or on its last day when the month is shorter."""
    unit, count = period_parts(days)
    if unit == "year":
        months = 12 * count
    elif unit == "month":
        months = count
    else:
        return start + timedelta(days=days)
    year, month = divmod(start.month - 1 + months, 12)
    year += start.year
    day = anchored_day(start, anchor_day)
    return start.replace(year=year, month=month + 1, day=min(day, monthrange(year, month + 1)[1]))
