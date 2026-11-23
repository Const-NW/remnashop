from collections.abc import Mapping
from functools import partial
from typing import Optional
from uuid import UUID

from loguru import logger
from redis.asyncio import Redis
from remnapy import RemnawaveSDK
from remnapy.models import UserResponseDto

from app.core.actor import Actor
from app.core.config import AppConfig
from app.core.db.uow import UnitOfWork
from app.core.enums import PanelSyncDirection, SubscriptionStatus
from app.core.exceptions import RemnawavePanelUnavailableError, ValidationError
from app.core.ports import EventPublisher
from app.core.usecase import UseCase
from app.core.utils.time import datetime_now
from app.modules.settings import GraceSettingsDto, SettingsRepository
from app.modules.subscriptions.dto import (
    PanelSyncResultDto,
    SubscriptionDto,
    SyncToPanelOptionsDto,
    UpdateSubscriptionSquadsDto,
)
from app.modules.subscriptions.events import PanelSyncCompletedEvent
from app.modules.subscriptions.panel_squads import replace_squads
from app.modules.subscriptions.panel_sync import (
    finish_panel_sync,
    holding_panel_sync,
    match_shop_user,
    named_shop_user,
    panel_lags_behind,
    push_to_panel,
    recreate_in_panel,
    scan_panel,
)
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.users import UserDto, UserRepository


class SyncAllToPanelUseCase(UseCase[SyncToPanelOptionsDto, PanelSyncResultDto]):
    """Puts every live subscription's configuration into the panel where it
    differs; a subscription whose panel user is gone gets a new one, and a shop
    user's panel user that no subscription stands for is deleted, each unless
    the options turn it off."""

    required_permission = None

    def __init__(
        self,
        subscription_repository: SubscriptionRepository,
        user_repository: UserRepository,
        remnawave_sdk: RemnawaveSDK,
        uow: UnitOfWork,
        redis: Redis,
        event_publisher: EventPublisher,
        config: AppConfig,
        settings_repository: SettingsRepository,
    ) -> None:
        self._subscription_repository = subscription_repository
        self._user_repository = user_repository
        self._remnawave_sdk = remnawave_sdk
        self._uow = uow
        self._redis = redis
        self._event_publisher = event_publisher
        self._prefix = config.remnawave.username_prefix
        self._settings_repository = settings_repository

    async def _execute(self, actor: Actor, data: SyncToPanelOptionsDto) -> PanelSyncResultDto:
        async with holding_panel_sync(self._redis, PanelSyncDirection.TO_PANEL):
            return await self._sync(actor, data)

    async def _sync(self, actor: Actor, options: SyncToPanelOptionsDto) -> PanelSyncResultDto:
        async with self._uow:
            await self._replace_squads(options.squad_replacements)
            grace = await self._settings_repository.get_grace_settings()
            subscriptions = await self._subscription_repository.list_non_terminal()
            linked = await self._subscription_repository.list_linked_remnawave_user_ids()
            owners = {
                user.id: user
                for user in await self._user_repository.get_many_by_ids(
                    [s.user_id for s in subscriptions]
                )
            }
        updated = failed = recreated = deleted = 0
        try:
            by_panel_id = {s.remnawave_user_id: s for s in subscriptions}
            listing = await scan_panel(
                self._remnawave_sdk, linked, partial(_lags_behind, by_panel_id, owners, grace)
            )
            for position, subscription in enumerate(subscriptions):
                lags = listing.tracked.get(subscription.remnawave_user_id)
                owner = owners[subscription.user_id]
                if lags is False:
                    continue
                try:
                    # the row as it is now, not as listed before the stream
                    async with self._uow:
                        current = await self._subscription_repository.get_by_id(subscription.id)
                    if current.status is SubscriptionStatus.DELETED:
                        continue  # deleted while the sync ran
                    if lags is None:
                        if not options.recreate_missing:
                            continue
                        await recreate_in_panel(
                            self._remnawave_sdk,
                            self._subscription_repository,
                            self._user_repository,
                            self._uow,
                            current,
                            self._prefix,
                        )
                        recreated += 1
                        continue
                    await push_to_panel(self._remnawave_sdk, current, owner, grace)
                except ValidationError:
                    continue  # deleted while the sync ran
                except RemnawavePanelUnavailableError:
                    failed += len(subscriptions) - position
                    logger.warning(f"{actor.log} Panel sync to the panel stopped: panel down")
                    break
                except Exception:
                    logger.exception(
                        f"Push to the panel failed for subscription '{subscription.id}'"
                    )
                    failed += 1
                    continue
                updated += 1
            else:
                deleted, delete_failed = await self._delete_orphans(
                    actor, listing.orphans, options.delete_orphans
                )
                failed += delete_failed
        except Exception:
            logger.exception(f"{actor.log} Panel sync to the panel failed")
            failed = len(subscriptions) - updated - recreated
        result = PanelSyncResultDto(
            direction=PanelSyncDirection.TO_PANEL,
            checked=len(subscriptions),
            updated=updated,
            failed=failed,
            missing=0,
            recreated=recreated,
            deleted=deleted,
            finished_at=datetime_now(),
        )
        await finish_panel_sync(self._redis, PanelSyncDirection.TO_PANEL, result)
        async with self._uow:
            await self._event_publisher.publish(
                PanelSyncCompletedEvent(
                    direction=result.direction,
                    checked=result.checked,
                    updated=result.updated,
                    failed=result.failed,
                    missing=result.missing,
                    recreated=result.recreated,
                    deleted=result.deleted,
                )
            )
        logger.info(f"{actor.log} Synced subscriptions to the panel: {result}")
        return result

    async def _replace_squads(self, replacements: Mapping[UUID, Optional[UUID]]) -> None:
        # the replaced rows then lag behind the panel, so the loop pushes them
        if not replacements:
            return
        for subscription in await self._subscription_repository.list_non_terminal():
            internal, external = replace_squads(
                subscription.internal_squads, subscription.external_squad, replacements
            )
            if (internal, external) != (subscription.internal_squads, subscription.external_squad):
                await self._subscription_repository.update_squads(
                    UpdateSubscriptionSquadsDto(
                        subscription_id=subscription.id,
                        internal_squads=internal,
                        external_squad=external,
                    )
                )

    async def _delete_orphans(
        self,
        actor: Actor,
        orphans: list[UserResponseDto],
        enabled: bool,
    ) -> tuple[int, int]:
        """(deleted, failed) for the shop users' panel users no subscription
        stands for; a panel user the shop didn't name is someone else's."""
        if not enabled:
            return 0, 0
        deleted = failed = 0
        for position, user in enumerate(orphans):
            try:
                async with self._uow:
                    owner = await match_shop_user(self._user_repository, user, self._prefix)
                    if owner is None:
                        continue
                    named = await named_shop_user(
                        self._user_repository, user.username, self._prefix
                    )
                    if named is None or named.id != owner.id:
                        # a hand-made panel user the shop only matched by contact
                        logger.warning(
                            f"{actor.log} Kept panel user {user.id} '{user.username}': "
                            f"not named by the shop"
                        )
                        continue
                    # linked while the sync ran
                    existing = await self._subscription_repository.find_by_remnawave_user_id(
                        user.id
                    )
                    if existing is not None and existing.status is not SubscriptionStatus.DELETED:
                        continue
                await self._remnawave_sdk.users.delete_user(user.id)
            except RemnawavePanelUnavailableError:
                failed += len(orphans) - position
                logger.warning(f"{actor.log} Panel cleanup stopped: panel down")
                break
            except Exception:
                logger.exception(f"Deleting panel user {user.id} failed")
                failed += 1
                continue
            deleted += 1
            logger.info(f"{actor.log} Deleted panel user {user.id} of user '{owner.id}'")
        return deleted, failed


def _lags_behind(
    by_panel_id: dict[int, SubscriptionDto],
    owners: dict[int, UserDto],
    grace: GraceSettingsDto,
    user: UserResponseDto,
) -> Optional[bool]:
    subscription = by_panel_id.get(user.id)
    if subscription is None:
        return None
    return panel_lags_behind(subscription, user, owners[subscription.user_id], grace)
