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
