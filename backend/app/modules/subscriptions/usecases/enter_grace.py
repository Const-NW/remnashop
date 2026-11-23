from contextlib import suppress
from datetime import datetime
from typing import Final, Optional

import httpx
from loguru import logger
from remnapy import RemnawaveSDK
from remnapy.exceptions import NotFoundError as RemnawaveNotFoundError

from app.core.actor import Actor
from app.core.db.uow import UnitOfWork
from app.core.enums import SubscriptionStatus
from app.core.exceptions import NotFoundError, RetryableTaskError
from app.core.ports import EventPublisher
from app.core.usecase import UseCase
from app.core.utils.time import datetime_now, truncate_to_ms
from app.modules.plans import PlanDto, PlanRepository
from app.modules.settings import GraceSettingsDto, SettingsRepository
from app.modules.subscriptions.dto import SubscriptionDto
from app.modules.subscriptions.eligibility import subscription_owner
from app.modules.subscriptions.events import (
    SubscriptionExpiredEvent,
    SubscriptionGraceStartedEvent,
    SubscriptionStatusSyncedEvent,
)
from app.modules.subscriptions.grace import LIVE_STATUSES, grace_entry_allowed, grace_until_for
from app.modules.subscriptions.lifecycle import SubscriptionLifecycleService
from app.modules.subscriptions.panel import (
    panel_refused,
    panel_user_gone,
    restore,
    revert_panel_to_owner,
)
from app.modules.subscriptions.panel_sync import from_panel
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.subscriptions.usecases.sync_subscription_status import (
    SyncSubscriptionStatusUseCase,
)
from app.modules.users import UserRepository

_NEVER_SENT: Final[tuple[type[Exception], ...]] = (httpx.ConnectError, httpx.ConnectTimeout)


class EnterGraceUseCase(UseCase[int, bool]):
    """Keeps an expired paid subscription's panel user going on the grace config;
    True while the subscription is in grace or once a panel refusal gave grace up
    and told the customer of the expiry: either way, no plain expiry notice is due."""

    # system-only: the expiry webhook and the grace sweep
    required_permission = None

    def __init__(
        self,
        subscription_repository: SubscriptionRepository,
        plan_repository: PlanRepository,
        settings_repository: SettingsRepository,
        user_repository: UserRepository,
        lifecycle_service: SubscriptionLifecycleService,
        remnawave_sdk: RemnawaveSDK,
        uow: UnitOfWork,
        event_publisher: EventPublisher,
        sync_subscription_status_use_case: SyncSubscriptionStatusUseCase,
    ) -> None:
        self._subscription_repository = subscription_repository
        self._plan_repository = plan_repository
        self._sync_subscription_status_use_case = sync_subscription_status_use_case
        self._settings_repository = settings_repository
        self._user_repository = user_repository
        self._lifecycle_service = lifecycle_service
        self._remnawave_sdk = remnawave_sdk
        self._uow = uow
        self._event_publisher = event_publisher

    async def _execute(self, actor: Actor, data: int) -> bool:
        if await self._extended_in_panel(data):
            return False
        now = datetime_now()
        async with self._uow:
            settings = await self._settings_repository.get_grace_settings()
            if not settings.enabled:
                return False
            subscription = await self._subscription_repository.get_by_id_for_update(data)
            claimed = subscription.grace_until is None
            if subscription.grace_until is not None:
                if subscription.grace_until <= now:
                    return False
            else:
                claim = await self._claim(subscription, settings, now)
                if claim is None:
                    return False
                subscription = claim

        grace_until = subscription.grace_until
        assert grace_until is not None
        # a repeat run (redelivery, sweep, duplicate webhook) finds the claim: it pushes
        # the grace config again, and finishes the entry if the run that claimed didn't
        try:
            # restore keeps an admin's switch-off made after the claim
            await self._remnawave_sdk.users.update_user(restore(subscription, grace=settings))
        except Exception as error:
            if not await self._recover_failed_push(
                subscription, grace_until, error, claimed=claimed
            ):
                return True

        if not await self._still_in_grace(data, grace_until, settings):
            return False
        if subscription.grace_started_at is None:
            await self._finish_entry(subscription, grace_until, settings)
        return True

    async def _finish_entry(
        self, subscription: SubscriptionDto, grace_until: datetime, settings: GraceSettingsDto
    ) -> None:
        data = subscription.id
        # grace traffic counts from zero; a failed reset leaves the old counter running
        try:
            await self._remnawave_sdk.users.reset_user_traffic(subscription.remnawave_user_id)
        except Exception:
            logger.exception(f"Failed to reset traffic of subscription '{data}' entering grace")
        async with self._uow:
            current = await self._subscription_repository.get_by_id_for_update(data)
            # a parallel run finished it, or the row left grace meanwhile
            if (
                current.grace_until != grace_until
                or current.grace_started_at is not None
                or current.status is SubscriptionStatus.DELETED
            ):
                return
            await self._subscription_repository.mark_grace_started(data, datetime_now())
            # switched off by an admin: no access to announce
            if current.status is SubscriptionStatus.DISABLED:
                return
            owner = await subscription_owner(self._user_repository, current)
            await self._event_publisher.publish(
                SubscriptionGraceStartedEvent(
                    user=owner,
                    subscription_id=data,
                    grace_until=grace_until.isoformat(),
                    traffic_limit_mb=settings.traffic_limit_mb,
                )
            )
        logger.info(f"Subscription '{data}' entered grace until {grace_until.isoformat()}")

    async def _extended_in_panel(self, data: int) -> bool:
        """A row left ACTIVE or LIMITED past its expiry lost its expiry webhook, and maybe the
        panel extension after it: the panel's date is read before grace cuts it short.
        True when the row isn't to enter grace now."""
        async with self._uow:
            settings = await self._settings_repository.get_grace_settings()
            subscription = await self._subscription_repository.get_by_id(data)
        if not (
            settings.enabled
            and subscription.status in LIVE_STATUSES
            and subscription.grace_until is None
            and subscription.expire_at <= datetime_now()
        ):
            return False
        try:
            panel_user = await self._remnawave_sdk.users.get_user_by_id(
                subscription.remnawave_user_id
            )
        except Exception:
            # the sweep's attempt rotation comes back to it
            logger.exception(f"Failed to read the panel user of subscription '{data}'")
            return True
        if panel_user.expire_at <= datetime_now():
            return False
        await self._sync_subscription_status_use_case.system(
            from_panel(panel_user, subscription.remnawave_user_id, "grace_sweep")
        )
        logger.info(f"Subscription '{data}' was extended in the panel, no grace")
        return True

    async def _claim(
        self, subscription: SubscriptionDto, settings: GraceSettingsDto, now: datetime
    ) -> Optional[SubscriptionDto]:
        plan: Optional[PlanDto] = None
        with suppress(NotFoundError):
            plan = await self._plan_repository.get_by_id(subscription.plan_snapshot.plan_id)
        if not grace_entry_allowed(subscription, plan, settings, now):
            return None
        if subscription.status in LIVE_STATUSES:
            # the expiry webhook was lost: its sync would have set this
            expired = await self._lifecycle_service.sync_status(
                subscription.id, SubscriptionStatus.EXPIRED
            )
            await self._event_publisher.publish(
                SubscriptionStatusSyncedEvent(
                    subscription_id=subscription.id,
                    old_status=subscription.status,
                    new_status=expired.status,
                    source="grace_sweep",
                    user_id=subscription.user_id,
                )
            )
        return await self._subscription_repository.set_grace_until(
            subscription.id, grace_until_for(settings, now)
        )

    async def _recover_failed_push(
        self,
        subscription: SubscriptionDto,
        grace_until: datetime,
        error: Exception,
        *,
        claimed: bool,
    ) -> bool:
        """True when the push landed after all, False when the panel refused it and
        grace was given up; raises a retryable error otherwise."""
        failed = f"Grace push of subscription '{subscription.id}' failed"
        if not claimed:
            if not panel_user_gone(error):
                raise RetryableTaskError(failed) from error
            # deleted in the panel, its webhook lost: a retry would never land
            await self._give_up(subscription.id, grace_until)
            logger.warning(f"{failed}: the panel user is gone, grace given up")
            return False
        if panel_refused(error):
            await self._give_up(subscription.id, grace_until)
            logger.warning(f"{failed}: the panel refused it, the subscription gets no grace")
            return False
        # a response timeout or a 5xx may still have applied the write; a request
        # that never reached the panel surely didn't
        if isinstance(error, _NEVER_SENT) or not await self._push_landed(subscription, grace_until):
            async with self._uow:
                await self._subscription_repository.set_grace_until(subscription.id, None)
            raise RetryableTaskError(failed) from error
        return True

    async def _give_up(self, data: int, grace_until: datetime) -> None:
        """A refusal won't pass on a retry: the expiry is told as it would be without grace."""
        async with self._uow:
            current = await self._subscription_repository.get_by_id_for_update(data)
            # a renewal or a plan change took the row out of grace meanwhile
            if current.grace_until != grace_until or current.status is SubscriptionStatus.DELETED:
                return
            await self._subscription_repository.give_up_grace(data)
            owner = await subscription_owner(self._user_repository, current)
            await self._event_publisher.publish(
                SubscriptionExpiredEvent(user=owner, subscription_id=data)
            )

    async def _push_landed(self, subscription: SubscriptionDto, grace_until: datetime) -> bool:
        try:
            panel_user = await self._remnawave_sdk.users.get_user_by_id(
                subscription.remnawave_user_id
            )
        except RemnawaveNotFoundError:
            return False
        except Exception as error:
            # unknown: the claim stays, and the webhook's retry pushes the same grace
            # again; a sweep run has no retry, the grace end restores the plan config
            raise RetryableTaskError(
                f"Grace push of subscription '{subscription.id}' failed, the panel is unreadable"
            ) from error
        return truncate_to_ms(panel_user.expire_at) == truncate_to_ms(grace_until)

    async def _still_in_grace(
        self, data: int, grace_until: datetime, settings: GraceSettingsDto
    ) -> bool:
        """A renewal or an admin may have left grace while the push ran: the row wins."""
        async with self._uow:
            current = await self._subscription_repository.get_by_id(data)
        if current.grace_until == grace_until and current.status is not SubscriptionStatus.DELETED:
            return True
        if current.status is SubscriptionStatus.DELETED:
            # a plan change replaced the row: the panel user is the new row's now
            await revert_panel_to_owner(
                self._remnawave_sdk, self._subscription_repository, self._uow, current, settings
            )
        else:
            try:
                await self._remnawave_sdk.users.update_user(
                    restore(current, grace=settings, left_grace=settings)
                )
            except Exception:
                logger.exception(f"Failed to put subscription '{data}' back after grace entry")
                async with self._uow:
                    # the minute cron pushes it again
                    await self._subscription_repository.mark_panel_restore_due(data, datetime_now())
        logger.info(f"Subscription '{data}' left grace while it was being pushed")
        return False
