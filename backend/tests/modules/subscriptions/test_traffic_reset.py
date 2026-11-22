from datetime import datetime, timezone

import pytest
from remnapy.enums import TrafficLimitStrategy

from app.modules.subscriptions import next_traffic_reset

_NOW = datetime(2026, 11, 12, 15, 30, tzinfo=timezone.utc)  # a Thursday


@pytest.mark.parametrize(
    ("strategy", "created", "expected"),
    [
        (TrafficLimitStrategy.NO_RESET, None, None),
        (TrafficLimitStrategy.DAY, None, datetime(2026, 11, 13, tzinfo=timezone.utc)),
        (TrafficLimitStrategy.WEEK, None, datetime(2026, 11, 16, tzinfo=timezone.utc)),
        (TrafficLimitStrategy.MONTH, None, datetime(2026, 12, 1, tzinfo=timezone.utc)),
        (
            TrafficLimitStrategy.MONTH_ROLLING,
            datetime(2026, 1, 20, tzinfo=timezone.utc),
            datetime(2026, 11, 20, tzinfo=timezone.utc),
        ),
        (
            TrafficLimitStrategy.MONTH_ROLLING,
            datetime(2026, 1, 5, tzinfo=timezone.utc),
            datetime(2026, 12, 5, tzinfo=timezone.utc),
        ),
        # the 31st in a 30-day month falls on its last day
        (
            TrafficLimitStrategy.MONTH_ROLLING,
            datetime(2026, 1, 31, tzinfo=timezone.utc),
            datetime(2026, 11, 30, tzinfo=timezone.utc),
        ),
    ],
)
def test_next_traffic_reset(
    strategy: TrafficLimitStrategy, created: object, expected: object
) -> None:
    assert next_traffic_reset(strategy, created, _NOW) == expected  # type: ignore[arg-type]
