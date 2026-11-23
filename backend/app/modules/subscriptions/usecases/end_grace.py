from datetime import datetime
from typing import Optional

from loguru import logger
from remnapy import RemnawaveSDK
from remnapy.enums import ErrorCode as RemnawaveErrorCode
from remnapy.exceptions import NotFoundError as RemnawaveNotFoundError

from app.core.actor import Actor
from app.core.db.uow import UnitOfWork
from app.core.enums import SubscriptionStatus
from app.core.exceptions import RetryableTaskError
from app.core.ports import EventPublisher
from app.core.usecase import UseCase
from app.core.utils.time import datetime_now
from app.modules.settings import SettingsRepository
from app.modules.subscriptions.eligibility import subscription_owner
from app.modules.subscriptions.events import SubscriptionExpiredEvent
from app.modules.subscriptions.panel import restore, revert_panel_to_owner
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
        settings_repository: SettingsRepository,
        remnawave_sdk: RemnawaveSDK,
        uow: UnitOfWork,
        event_publisher: EventPublisher,
    ) -> None:
        self._subscription_repository = subscription_repository
        self._user_repository = user_repository
        self._settings_repository = settings_repository
        self._remnawave_sdk = remnawave_sdk
        self._uow = uow
        self._event_publisher = event_publisher

    async def _execute(self, actor: Actor, data: int) -> None:
        async with self._uow:
            subscription = await self._subscription_repository.get_by_id(data)
            settings = await self._settings_repository.get_grace_settings()
        if not _ended(subscription.grace_until) or (
            subscription.status is SubscriptionStatus.DELETED
        ):
            return
        try:
            # the row's own config, its past expiry left out
            await self._remnawave_sdk.users.update_user(restore(subscription, left_grace=settings))
        except RemnawaveNotFoundError as error:
            # a missing squad or a proxy's bare 404 may pass on a retry
            if error.code != RemnawaveErrorCode.USER_NOT_FOUND:
                raise RetryableTaskError(
                    f"Grace end push of subscription '{data}' failed"
                ) from error
            # deleted in the panel, its webhook lost: nothing holds the grace config now
            logger.warning(f"Panel user of subscription '{data}' is gone, ending its grace as is")
        except Exception as error:
            raise RetryableTaskError(f"Grace end push of subscription '{data}' failed") from error

        async with self._uow:
            current = await self._subscription_repository.get_by_id_for_update(data)
            replaced = current.status is SubscriptionStatus.DELETED
            # a renewal or an admin's extension cleared it meanwhile: the push above went
            # out from the row before it and may have landed over theirs
            stale = not replaced and not _ended(current.grace_until)
            if not replaced and not stale:
                await self._subscription_repository.end_grace(data)
                # an admin's switch-off during grace: access ends, but it didn't expire
                if current.status is SubscriptionStatus.EXPIRED:
                    owner = await subscription_owner(self._user_repository, current)
                    await self._event_publisher.publish(
                        SubscriptionExpiredEvent(user=owner, subscription_id=data)
                    )
        if replaced:
            # a plan change took the panel user over while the push ran
            await revert_panel_to_owner(
                self._remnawave_sdk, self._subscription_repository, self._uow, current
            )
            return
        if stale:
            # the grace end is recorded either way; the next sync or push sets it right
            try:
                await self._remnawave_sdk.users.update_user(
                    restore(current, grace=settings, left_grace=settings)
                )
            except Exception:
                logger.exception(f"Failed to put subscription '{data}' back after its grace end")
                async with self._uow:
                    # the minute cron pushes it again
                    await self._subscription_repository.mark_panel_restore_due(data, datetime_now())
                return
            logger.info(f"Subscription '{data}' left grace while its end was being pushed")
            return
        logger.info(f"Grace of subscription '{data}' ended")


def _ended(grace_until: Optional[datetime]) -> bool:
    return grace_until is not None and grace_until <= datetime_now()
