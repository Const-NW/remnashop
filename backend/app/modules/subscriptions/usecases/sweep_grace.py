from typing import Final

from loguru import logger

from app.core.actor import Actor
from app.core.db.uow import UnitOfWork
from app.core.usecase import UseCase
from app.core.utils.time import datetime_now
from app.modules.settings import SettingsRepository
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.subscriptions.usecases.end_grace import EndGraceUseCase
from app.modules.subscriptions.usecases.enter_grace import EnterGraceUseCase

_BATCH: Final[int] = 100


class SweepGraceUseCase(UseCase[None, None]):
    """Enters and ends grace for subscriptions whose expiry webhook was lost."""

    # system-only: the minute cron
    required_permission = None

    def __init__(
        self,
        subscription_repository: SubscriptionRepository,
        settings_repository: SettingsRepository,
        uow: UnitOfWork,
        enter_grace_use_case: EnterGraceUseCase,
        end_grace_use_case: EndGraceUseCase,
    ) -> None:
        self._subscription_repository = subscription_repository
        self._settings_repository = settings_repository
        self._uow = uow
        self._enter_grace_use_case = enter_grace_use_case
        self._end_grace_use_case = end_grace_use_case

    async def _execute(self, actor: Actor, data: None) -> None:
        now = datetime_now()
        async with self._uow:
            settings = await self._settings_repository.get_grace_settings()
            # grace already given still ends after the feature is switched off
            entering = (
                await self._subscription_repository.list_grace_entry_candidates(now, _BATCH)
                # an entry that claimed and died before it finished
                + await self._subscription_repository.list_grace_unstarted(now, _BATCH)
                if settings.enabled
                else []
            )
            ending = await self._subscription_repository.list_grace_ended(now, _BATCH)
            # a row that keeps failing must not hold the rest of the queue back
            await self._subscription_repository.mark_grace_attempted(
                [subscription.id for subscription in entering + ending], now
            )

        for subscription in entering:
            try:
                await self._enter_grace_use_case.system(subscription.id)
            except Exception:
                logger.exception(f"Failed to enter grace for subscription '{subscription.id}'")
        for subscription in ending:
            try:
                await self._end_grace_use_case.system(subscription.id)
            except Exception:
                logger.exception(f"Failed to end grace of subscription '{subscription.id}'")
