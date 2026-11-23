from dataclasses import replace
from typing import Optional

from loguru import logger
from remnapy import RemnawaveSDK

from app.core.actor import Actor
from app.core.db.uow import UnitOfWork
from app.core.enums import SubscriptionStatus
from app.core.ports import EventPublisher
from app.core.usecase import UseCase
from app.core.utils.time import datetime_now, truncate_to_ms
from app.modules.settings import GraceSettingsDto, SettingsRepository
from app.modules.subscriptions.dto import (
    PanelConfigDto,
    SubscriptionDto,
    SyncSubscriptionStatusDto,
)
from app.modules.subscriptions.events import (
    StatusSyncSource,
    SubscriptionConfigSyncedEvent,
    SubscriptionStatusSyncedEvent,
)
from app.modules.subscriptions.grace import grace_end_echo
from app.modules.subscriptions.lifecycle import SubscriptionLifecycleService
from app.modules.subscriptions.panel import restore
from app.modules.subscriptions.panel_config import changed_config_fields, config_update
from app.modules.subscriptions.repository import SubscriptionRepository


class SyncSubscriptionStatusUseCase(UseCase[SyncSubscriptionStatusDto, Optional[SubscriptionDto]]):
    required_permission = None

    def __init__(
        self,
        subscription_repository: SubscriptionRepository,
        uow: UnitOfWork,
        event_publisher: EventPublisher,
        lifecycle_service: SubscriptionLifecycleService,
        remnawave_sdk: RemnawaveSDK,
        settings_repository: SettingsRepository,
    ) -> None:
        self._subscription_repository = subscription_repository
        self._settings_repository = settings_repository
        self._uow = uow
        self._event_publisher = event_publisher
        self._lifecycle_service = lifecycle_service
        self._remnawave_sdk = remnawave_sdk

    async def _execute(
        self, actor: Actor, data: SyncSubscriptionStatusDto
    ) -> Optional[SubscriptionDto]:
        async with self._uow:
            subscription = await self._subscription_repository.find_by_remnawave_user_id_for_update(
                data.remnawave_user_id
            )
            if subscription is None:
                return None

            # the row is the live one when there is one: the writes below skip
            # DELETED rows, so a loaded DELETED row means there is nothing to write
            live = subscription.status is not SubscriptionStatus.DELETED
            if live:
                await self._sync_panel_dates(subscription, data)

            left_grace: Optional[GraceSettingsDto] = None
            if live and subscription.grace_until is not None:
                if not _leaves_grace(subscription, data):
                    # the panel holds the grace config, not the row's terms
                    subscription = await self._sync_grace_status(subscription, data)
                    return await self._sync_url(subscription, data)
                left_grace = await self._settings_repository.get_grace_settings()
                subscription = await self._subscription_repository.set_grace_until(
                    subscription.id, None
                )
            elif _awaits_restore(subscription, data):
                return await self._sync_awaiting_restore(subscription, data)

            subscription = await self._sync_status(subscription, data)
            if subscription.status is SubscriptionStatus.DELETED:
                return subscription
            if left_grace is not None:
                # only the expiry and status are the panel's: its config is still grace's
                subscription = await self._sync_url(subscription, data)
            elif data.config is not None:
                subscription = await self._sync_config(subscription, data.config, data.source)

        if left_grace is not None:
            await self._push_out_of_grace(subscription, left_grace)
        return subscription

    async def _sync_awaiting_restore(
        self, subscription: SubscriptionDto, data: SyncSubscriptionStatusDto
    ) -> SubscriptionDto:
        if SubscriptionStatus.DISABLED in (subscription.status, data.status):
            # an admin's switch-off, or its undoing, is the panel's even now; a stale
            # EXPIRED or LIMITED would cut a paid row off
            subscription = await self._sync_status(subscription, replace(data, expire_at=None))
        return await self._sync_url(subscription, data)

    async def _sync_panel_dates(
        self, subscription: SubscriptionDto, data: SyncSubscriptionStatusDto
    ) -> None:
        if data.first_connected_at is not None and subscription.first_connected_at is None:
            await self._subscription_repository.set_first_connected_at(
                data.remnawave_user_id, data.first_connected_at
            )
        if (
            data.panel_created_at is not None
            and subscription.panel_created_at != data.panel_created_at
        ):
            await self._subscription_repository.set_panel_created_at(
                data.remnawave_user_id, data.panel_created_at
            )

    async def _push_out_of_grace(
        self, subscription: SubscriptionDto, grace: GraceSettingsDto
    ) -> None:
        # after the commit: a retried sync would take the grace config into the row,
        # so a failed push is left to the restore cron
        try:
            await self._remnawave_sdk.users.update_user(restore(subscription, left_grace=grace))
        except Exception:
            logger.exception(f"Failed to take subscription '{subscription.id}' out of grace")
            async with self._uow:
                # the minute cron pushes it again
                await self._subscription_repository.mark_panel_restore_due(
                    subscription.id, datetime_now()
                )

    async def _sync_grace_status(
        self, subscription: SubscriptionDto, data: SyncSubscriptionStatusDto
    ) -> SubscriptionDto:
        """In grace the panel user runs ACTIVE on the grace config while the row stays
        EXPIRED: only a switch-off in the panel, or its undoing, is the row's."""
        target = (
            SubscriptionStatus.DISABLED
            if data.status is SubscriptionStatus.DISABLED
            else SubscriptionStatus.EXPIRED
        )
        if subscription.status is target:
            return subscription
        old_status = subscription.status
        subscription = await self._lifecycle_service.sync_status(subscription.id, target)
        await self._event_publisher.publish(
            SubscriptionStatusSyncedEvent(
                subscription_id=subscription.id,
                old_status=old_status,
                new_status=subscription.status,
                source=data.source,
                user_id=subscription.user_id,
            )
        )
        return subscription

    async def _sync_url(
        self, subscription: SubscriptionDto, data: SyncSubscriptionStatusDto
    ) -> SubscriptionDto:
        if data.config is None or data.config.url == subscription.url:
            return subscription
        subscription = await self._subscription_repository.set_url(subscription.id, data.config.url)
        await self._event_publisher.publish(
            SubscriptionConfigSyncedEvent(
                subscription_id=subscription.id,
                source=data.source,
                changed_fields=["url"],
                removed_addon_types=[],
                user_id=subscription.user_id,
            )
        )
        return subscription

    async def _sync_status(
        self, subscription: SubscriptionDto, data: SyncSubscriptionStatusDto
    ) -> SubscriptionDto:
        old_status = subscription.status
        status_drifted = subscription.status is not data.status
        expire_at_drifted = (
            data.expire_at is not None
            and subscription.status is not SubscriptionStatus.DELETED
            and truncate_to_ms(subscription.expire_at) != truncate_to_ms(data.expire_at)
            and not grace_end_echo(subscription, data.expire_at)
        )
        if not status_drifted and not expire_at_drifted:
            return subscription

        if status_drifted:
            subscription = await self._lifecycle_service.sync_status(subscription.id, data.status)
        if expire_at_drifted and subscription.status is not SubscriptionStatus.DELETED:
            # the panel owns expire_at: this pulls in a renewal that landed
            # there but not in our commit; status is whatever the panel
            # reported, a renewal arrives as ACTIVE
            assert data.expire_at is not None
            subscription = await self._lifecycle_service.sync_expire_at(
                subscription.id, data.expire_at
            )

        if subscription.status is old_status and not expire_at_drifted:
            # sync_status() was a no-op (already DELETED) and nothing else moved
            return subscription

        await self._event_publisher.publish(
            SubscriptionStatusSyncedEvent(
                subscription_id=subscription.id,
                old_status=old_status,
                new_status=subscription.status,
                source=data.source,
                user_id=subscription.user_id,
            )
        )
        return subscription

    async def _sync_config(
        self, subscription: SubscriptionDto, config: PanelConfigDto, source: StatusSyncSource
    ) -> SubscriptionDto:
        update = config_update(subscription, config)
        changed = changed_config_fields(subscription, update)
        if not changed:
            return subscription
        subscription = await self._subscription_repository.apply_panel_config(update)
        await self._event_publisher.publish(
            SubscriptionConfigSyncedEvent(
                subscription_id=subscription.id,
                source=source,
                changed_fields=changed,
                removed_addon_types=sorted(update.removed_addon_types),
                user_id=subscription.user_id,
            )
        )
        return subscription


def _leaves_grace(subscription: SubscriptionDto, data: SyncSubscriptionStatusDto) -> bool:
    """A deletion, or an admin moving the panel's date off the grace end: the
    panel's terms are real again."""
    assert subscription.grace_until is not None
    return data.status is SubscriptionStatus.DELETED or (
        data.expire_at is not None
        and truncate_to_ms(data.expire_at) != truncate_to_ms(subscription.grace_until)
        and data.expire_at > datetime_now()
    )


def _awaits_restore(subscription: SubscriptionDto, data: SyncSubscriptionStatusDto) -> bool:
    """A push of the row's config failed and waits for the restore cron: the panel's
    terms are stale until it lands; only a deletion is the panel's."""
    return (
        subscription.status is not SubscriptionStatus.DELETED
        and subscription.panel_restore_due_at is not None
        and data.status is not SubscriptionStatus.DELETED
    )
