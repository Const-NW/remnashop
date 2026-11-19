from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from app.core.utils.time import (
    add_term,
    range_end_utc,
    range_start_utc,
)


def test_range_start_utc_treats_naive_datetime_as_utc() -> None:
    result = range_start_utc(datetime(2026, 8, 18))  # noqa: DTZ001

    assert result == datetime(2026, 8, 18, tzinfo=timezone.utc)


def test_range_start_utc_leaves_aware_datetime_untouched() -> None:
    aware = datetime(2026, 8, 18, 12, tzinfo=timezone(timedelta(hours=5)))

    assert range_start_utc(aware) == aware


def test_range_start_utc_passes_through_none() -> None:
    assert range_start_utc(None) is None


def test_range_end_utc_moves_naive_date_to_next_midnight() -> None:
    result = range_end_utc(datetime(2026, 8, 18))  # noqa: DTZ001

    assert result == datetime(2026, 8, 19, tzinfo=timezone.utc)


def test_range_end_utc_includes_the_whole_selected_day_exclusively() -> None:
    date_to = range_end_utc(datetime(2026, 8, 18))  # noqa: DTZ001
    event_late_in_the_day = datetime(2026, 8, 18, 23, 30, tzinfo=timezone.utc)
    next_midnight = datetime(2026, 8, 19, tzinfo=timezone.utc)

    assert date_to is not None
    assert event_late_in_the_day < date_to
    assert not next_midnight < date_to


def test_range_end_utc_leaves_aware_datetime_untouched() -> None:
    aware = datetime(2026, 8, 18, 12, tzinfo=timezone(timedelta(hours=5)))

    assert range_end_utc(aware) == aware


def test_range_end_utc_passes_through_none() -> None:
    assert range_end_utc(None) is None


def test_range_end_utc_keeps_a_naive_time_of_day() -> None:
    result = range_end_utc(datetime(2026, 8, 18, 15, 0))  # noqa: DTZ001

    assert result == datetime(2026, 8, 18, 15, 0, tzinfo=timezone.utc)


def _utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("start", "days", "expected"),
    [
        (_utc(2026, 1, 15, 9), 30, _utc(2026, 2, 15, 9)),
        # a shorter month ends on its last day, a leap February on the 29th
        (_utc(2026, 1, 31), 30, _utc(2026, 2, 28)),
        (_utc(2028, 1, 31), 30, _utc(2028, 2, 29)),
        (_utc(2026, 3, 31), 30, _utc(2026, 4, 30)),
        (_utc(2026, 11, 30), 90, _utc(2027, 2, 28)),
        (_utc(2026, 12, 31), 360, _utc(2027, 12, 31)),
        (_utc(2026, 5, 10), 365, _utc(2027, 5, 10)),
        (_utc(2028, 2, 29), 365, _utc(2029, 2, 28)),
        (_utc(2026, 5, 10), 730, _utc(2028, 5, 10)),
        # not a whole month: plain days
        (_utc(2026, 1, 31), 31, _utc(2026, 3, 3)),
        (_utc(2026, 1, 31), 7, _utc(2026, 2, 7)),
    ],
)
def test_add_term_counts_months_and_years_by_the_calendar(
    start: datetime, days: int, expected: datetime
) -> None:
    assert add_term(start, days) == expected


@pytest.mark.parametrize(
    ("start", "anchor", "expected"),
    [
        # cut to a shorter month's end, back on the anchor the month after
        (_utc(2027, 2, 28), 31, _utc(2027, 3, 31)),
        (_utc(2027, 4, 30), 31, _utc(2027, 5, 31)),
        (_utc(2028, 2, 29), 30, _utc(2028, 3, 30)),
        # an end moved off the anchor counts from its own day
        (_utc(2027, 2, 15), 31, _utc(2027, 3, 15)),
        (_utc(2027, 3, 30), 31, _utc(2027, 4, 30)),
        (_utc(2027, 2, 28), None, _utc(2027, 3, 28)),
    ],
)
def test_add_term_comes_back_to_the_anchor_day(
    start: datetime, anchor: Optional[int], expected: datetime
) -> None:
    assert add_term(start, 30, anchor) == expected


def test_plain_days_ignore_the_anchor() -> None:
    assert add_term(_utc(2027, 2, 28), 7, 31) == _utc(2027, 3, 7)
