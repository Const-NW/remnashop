from typing import Final

from loguru import logger
from remnapy import RemnawaveSDK

from app.core.actor import Actor
from app.core.db.uow import UnitOfWork
from app.core.enums import SubscriptionStatus
from app.core.usecase import UseCase
from app.core.utils.time import datetime_now
from app.modules.settings import SettingsRepository
from app.modules.subscriptions.panel import restore, revert_panel_to_owner
from app.modules.subscriptions.repository import SubscriptionRepository

_BATCH: Final[int] = 100


class RestoreDuePanelConfigsUseCase(UseCase[None, None]):
    """Pushes again the panel configs whose push failed after a commit: leaving grace,
    a renewal or a squad edit racing grace entry. Reads each row as it is now."""

    # system-only: the minute cron
    required_permission = None

    def __init__(
        self,
        subscription_repository: SubscriptionRepository,
        settings_repository: SettingsRepository,
        remnawave_sdk: RemnawaveSDK,
        uow: UnitOfWork,
    ) -> None:
        self._subscription_repository = subscription_repository
        self._settings_repository = settings_repository
        self._remnawave_sdk = remnawave_sdk
        self._uow = uow

    async def _execute(self, actor: Actor, data: None) -> None:
        async with self._uow:
            settings = await self._settings_repository.get_grace_settings()
            due = await self._subscription_repository.list_panel_restore_due(_BATCH)

        for subscription in due:
            marked_at = subscription.panel_restore_due_at
            assert marked_at is not None
            try:
                if subscription.status is SubscriptionStatus.DELETED:
                    # a plan change took the panel user over: its new row's config
                    await revert_panel_to_owner(
                        self._remnawave_sdk,
                        self._subscription_repository,
                        self._uow,
                        subscription,
                        settings,
                    )
                else:
                    await self._remnawave_sdk.users.update_user(
                        restore(subscription, grace=settings, left_grace=settings)
                    )
            except Exception:
                logger.exception(f"Failed to restore the panel config of '{subscription.id}'")
                async with self._uow:
                    # to the back of the queue
                    await self._subscription_repository.mark_panel_restore_due(
                        subscription.id, datetime_now()
                    )
                continue
            async with self._uow:
                await self._subscription_repository.clear_panel_restore_due(
                    subscription.id, marked_at
                )
            logger.info(f"Restored the panel config of subscription '{subscription.id}'")
