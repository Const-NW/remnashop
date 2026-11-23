from datetime import datetime
from typing import Optional

from loguru import logger
from remnapy import RemnawaveSDK

from app.core.actor import Actor
from app.core.db.uow import UnitOfWork
from app.core.enums import SubscriptionStatus
from app.core.exceptions import RetryableTaskError
from app.core.ports import EventPublisher
from app.core.usecase import UseCase
from app.core.utils.time import datetime_now
from app.modules.subscriptions.eligibility import subscription_owner
from app.modules.subscriptions.events import SubscriptionExpiredEvent
from app.modules.subscriptions.panel import restore
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.users import UserRepository


class EndGraceUseCase(UseCase[int, None]):
    """Puts the plan's config back into the panel once grace has run out; the
    panel user stays expired, as it expired by itself at the grace end."""

    # system-only: the expiry webhook and the grace sweep
    required_permission = None

    def __init__(
        self,
        subscription_repository: SubscriptionRepository,
        user_repository: UserRepository,
        remnawave_sdk: RemnawaveSDK,
        uow: UnitOfWork,
        event_publisher: EventPublisher,
    ) -> None:
        self._subscription_repository = subscription_repository
        self._user_repository = user_repository
        self._remnawave_sdk = remnawave_sdk
        self._uow = uow
        self._event_publisher = event_publisher

    async def _execute(self, actor: Actor, data: int) -> None:
        async with self._uow:
            subscription = await self._subscription_repository.get_by_id(data)
        if not _ended(subscription.grace_until) or (
            subscription.status is SubscriptionStatus.DELETED
        ):
            return
        try:
            # without grace settings: the row's own config, its past expiry left out
            await self._remnawave_sdk.users.update_user(restore(subscription))
        except Exception as error:
            raise RetryableTaskError(f"Grace end push of subscription '{data}' failed") from error

        async with self._uow:
            subscription = await self._subscription_repository.get_by_id_for_update(data)
            # an admin's extension or a parallel run cleared it meanwhile
            if not _ended(subscription.grace_until):
                return
            await self._subscription_repository.end_grace(data)
            # an admin's switch-off during grace: access ends, but it didn't expire
            if subscription.status is SubscriptionStatus.EXPIRED:
                owner = await subscription_owner(self._user_repository, subscription)
                await self._event_publisher.publish(
                    SubscriptionExpiredEvent(user=owner, subscription_id=data)
                )
        logger.info(f"Grace of subscription '{data}' ended")


def _ended(grace_until: Optional[datetime]) -> bool:
    return grace_until is not None and grace_until <= datetime_now()
