from datetime import datetime, timedelta
from typing import Optional

from app.core.exceptions import CooldownError
from app.modules.subscriptions.repository import ResetColumn, SubscriptionRepository


async def claim_reset(
    subscription_repository: SubscriptionRepository,
    subscription_id: int,
    column: ResetColumn,
    cooldown: timedelta,
    now: datetime,
) -> Optional[datetime]:
    """Stamps the reset before the panel call, under the row lock: a concurrent
    tap waits here and then sees the new stamp. Returns the old one, to put back
    if the panel call fails."""
    locked = await subscription_repository.get_by_id_for_update(subscription_id)
    previous: Optional[datetime] = getattr(locked, column)
    if previous is not None and now < previous + cooldown:
        raise CooldownError(previous + cooldown)
    await subscription_repository.set_reset_at(subscription_id, column, now)
    return previous
