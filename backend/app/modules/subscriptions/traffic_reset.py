import calendar
from datetime import datetime, time, timedelta
from typing import Optional

from remnapy.enums import TrafficLimitStrategy

from app.core.constants import TIMEZONE


def next_traffic_reset(
    strategy: TrafficLimitStrategy, created_at: Optional[datetime], now: datetime
) -> Optional[datetime]:
    midnight = datetime.combine(now.date(), time(), tzinfo=TIMEZONE)
    if strategy is TrafficLimitStrategy.DAY:
        return midnight + timedelta(days=1)
    if strategy is TrafficLimitStrategy.WEEK:
        return midnight + timedelta(days=7 - now.weekday())
    if strategy is TrafficLimitStrategy.MONTH:
        year, month = divmod(now.year * 12 + now.month, 12)
        return midnight.replace(year=year, month=month + 1, day=1)
    if strategy is TrafficLimitStrategy.MONTH_ROLLING and created_at is not None:
        # on the day of the month the panel user was created, the month's last day if shorter
        year, month = now.year, now.month
        for _ in range(2):
            day = min(created_at.day, calendar.monthrange(year, month)[1])
            reset = midnight.replace(year=year, month=month, day=day)
            if reset > now:
                return reset
            year, month = divmod(year * 12 + month, 12)
            month += 1
    return None
