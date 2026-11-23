import time
from typing import Final, Optional

from loguru import logger
from remnapy import RemnawaveSDK

from app.core.actor import Actor
from app.core.db.uow import UnitOfWork
from app.core.enums import SubscriptionStatus
from app.core.usecase import UseCase
from app.core.utils.time import datetime_now
from app.modules.settings import GraceSettingsDto, SettingsRepository
from app.modules.subscriptions.dto import SubscriptionDto
from app.modules.subscriptions.panel import panel_refused, panel_user_gone, restore
from app.modules.subscriptions.repository import SubscriptionRepository

_BATCH: Final[int] = 100
# short of the cron's lock, so the next run never pushes the same rows alongside
_TIME_BUDGET_S: Final[float] = 45.0


class RestoreDuePanelConfigsUseCase(UseCase[None, None]):
    """Pushes again the panel configs whose push failed after a commit: leaving grace,
    a renewal or a squad edit racing grace. Each row is pushed as it is at that moment."""

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
        deadline = time.monotonic() + _TIME_BUDGET_S
        async with self._uow:
            settings = await self._settings_repository.get_grace_settings()
            due = await self._subscription_repository.list_panel_restore_due(_BATCH)

        for listed in due:
            if time.monotonic() > deadline:
                return
            try:
                await self._restore(listed, settings)
            except Exception:
                logger.exception(f"Failed to restore the panel config of '{listed.id}'")

    async def _restore(self, listed: SubscriptionDto, settings: GraceSettingsDto) -> None:
        async with self._uow:
            # a plan change hands the panel user to a new row: that one's config. Its lock
            # comes first, as a plan change's rollback takes them
            owner = await self._subscription_repository.find_by_remnawave_user_id_for_update(
                listed.remnawave_user_id
            )
            marked = await self._subscription_repository.get_by_id_for_update(listed.id)
            if marked.panel_restore_due_at is None:
                return
            # pushed under the row lock: a renewal, edit or grace step on this row waits,
            # so the panel can't get a config older than the row's last one
            if await self._push(marked.id, owner, settings):
                await self._subscription_repository.clear_panel_restore_due(
                    marked.id, marked.panel_restore_due_at
                )
            else:
                # to the back of the queue
                await self._subscription_repository.mark_panel_restore_due(
                    marked.id, datetime_now()
                )

    async def _push(
        self, marked_id: int, owner: Optional[SubscriptionDto], settings: GraceSettingsDto
    ) -> bool:
        """False when a retry may still land it."""
        if owner is None or owner.status is SubscriptionStatus.DELETED:
            return True
        try:
            await self._remnawave_sdk.users.update_user(
                restore(owner, grace=settings, left_grace=settings)
            )
        except Exception as error:
            if panel_user_gone(error) or panel_refused(error):
                logger.warning(f"Panel refused restoring the config of '{marked_id}', dropped")
                return True
            logger.exception(f"Failed to restore the panel config of '{marked_id}'")
            return False
        logger.info(f"Restored the panel config of subscription '{marked_id}'")
        return True
