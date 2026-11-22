from contextlib import suppress
from typing import Optional

from loguru import logger
from remnapy import RemnawaveSDK

from app.core.actor import Actor
from app.core.db.uow import UnitOfWork
from app.core.exceptions import NotFoundError, RetryableTaskError
from app.core.ports import EventPublisher
from app.core.usecase import UseCase
from app.core.utils.time import datetime_now
from app.modules.plans import PlanDto, PlanRepository
from app.modules.settings import SettingsRepository
from app.modules.subscriptions.eligibility import subscription_owner
from app.modules.subscriptions.events import SubscriptionGraceStartedEvent
from app.modules.subscriptions.grace import grace_entry_allowed, grace_until_for
from app.modules.subscriptions.panel import grace_panel_update
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.users import UserRepository


class EnterGraceUseCase(UseCase[int, bool]):
    """Keeps an expired paid subscription's panel user going on the grace config;
    True while the subscription is in grace."""

    # system-only: the expiry webhook and the grace sweep
    required_permission = None

    def __init__(
        self,
        subscription_repository: SubscriptionRepository,
        plan_repository: PlanRepository,
        settings_repository: SettingsRepository,
        user_repository: UserRepository,
        remnawave_sdk: RemnawaveSDK,
        uow: UnitOfWork,
        event_publisher: EventPublisher,
    ) -> None:
        self._subscription_repository = subscription_repository
        self._plan_repository = plan_repository
        self._settings_repository = settings_repository
        self._user_repository = user_repository
        self._remnawave_sdk = remnawave_sdk
        self._uow = uow
        self._event_publisher = event_publisher

    async def _execute(self, actor: Actor, data: int) -> bool:
        now = datetime_now()
        async with self._uow:
            settings = await self._settings_repository.get_grace_settings()
            subscription = await self._subscription_repository.get_by_id_for_update(data)
            claimed = subscription.grace_until is None
            if subscription.grace_until is not None:
                if subscription.grace_until <= now:
                    return False
            else:
                plan: Optional[PlanDto] = None
                with suppress(NotFoundError):
                    plan = await self._plan_repository.get_by_id(subscription.plan_snapshot.plan_id)
                if not grace_entry_allowed(subscription, plan, settings, now):
                    return False
                subscription = await self._subscription_repository.set_grace_until(
                    data, grace_until_for(settings, now)
                )

        grace_until = subscription.grace_until
        assert grace_until is not None
        # a redelivered run finds its own claim: it pushes again, nothing more
        try:
            await self._remnawave_sdk.users.update_user(
                grace_panel_update(
                    subscription, settings, grace_until, id=subscription.remnawave_user_id
                )
            )
        except Exception as error:
            if claimed:
                async with self._uow:
                    await self._subscription_repository.set_grace_until(data, None)
            raise RetryableTaskError(f"Grace push of subscription '{data}' failed") from error
        # grace traffic counts from zero; a failed reset leaves the old counter running
        try:
            await self._remnawave_sdk.users.reset_user_traffic(subscription.remnawave_user_id)
        except Exception:
            logger.exception(f"Failed to reset traffic of subscription '{data}' entering grace")

        if claimed:
            async with self._uow:
                owner = await subscription_owner(self._user_repository, subscription)
                await self._event_publisher.publish(
                    SubscriptionGraceStartedEvent(
                        user=owner,
                        subscription_id=data,
                        grace_until=grace_until.isoformat(),
                        traffic_limit_mb=settings.traffic_limit_mb,
                    )
                )
            logger.info(f"Subscription '{data}' entered grace until {grace_until.isoformat()}")
        return True
