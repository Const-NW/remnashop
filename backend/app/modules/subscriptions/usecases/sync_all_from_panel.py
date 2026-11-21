from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import Final, Optional

from loguru import logger
from redis.asyncio import Redis
from remnapy import RemnawaveSDK
from remnapy.exceptions import NotFoundError as RemnawaveNotFoundError
from remnapy.models import UserResponseDto

from app.core.actor import Actor
from app.core.config import AppConfig
from app.core.db.uow import UnitOfWork
from app.core.enums import Locale, PanelSyncDirection, SubscriptionStatus
from app.core.exceptions import RemnawavePanelUnavailableError, ValidationError
from app.core.ports import EventPublisher
from app.core.usecase import UseCase
from app.core.utils.time import datetime_now
from app.modules.blacklist import BlacklistRepository
from app.modules.plans import PlanRepository
from app.modules.subscriptions.dto import (
    PanelSyncResultDto,
    SubscriptionDto,
    SyncFromPanelOptionsDto,
)
from app.modules.subscriptions.eligibility import PurchaseEligibility
from app.modules.subscriptions.events import PanelSyncCompletedEvent, StatusSyncSource
from app.modules.subscriptions.panel_sync import (
    differs_from_panel,
    finish_panel_sync,
    from_panel,
    holding_panel_sync,
    imported_row,
    match_shop_user,
    named_shop_user,
    scan_panel,
)
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.subscriptions.usecases.sync_subscription_status import (
    SyncSubscriptionStatusUseCase,
)
from app.modules.translations import TranslationRepository
from app.modules.users import CreateUserDto, UserDto, UserRepository

_SOURCE: Final[StatusSyncSource] = "panel_sync"


class SyncAllFromPanelUseCase(UseCase[SyncFromPanelOptionsDto, PanelSyncResultDto]):
    """Takes the panel's state into every live subscription that differs.

    Each candidate from the panel stream is read again before it is written, so
    a row changed meanwhile is judged by the panel's current state. Panel users
    without a subscription are imported, subject to the options.
    """

    required_permission = None

    def __init__(
        self,
        subscription_repository: SubscriptionRepository,
        user_repository: UserRepository,
        plan_repository: PlanRepository,
        remnawave_sdk: RemnawaveSDK,
        sync_use_case: SyncSubscriptionStatusUseCase,
        uow: UnitOfWork,
        redis: Redis,
        event_publisher: EventPublisher,
        eligibility: PurchaseEligibility,
        config: AppConfig,
        blacklist_repository: BlacklistRepository,
        translation_repository: TranslationRepository,
    ) -> None:
        self._subscription_repository = subscription_repository
        self._user_repository = user_repository
        self._plan_repository = plan_repository
        self._remnawave_sdk = remnawave_sdk
        self._sync_use_case = sync_use_case
        self._uow = uow
        self._redis = redis
        self._event_publisher = event_publisher
        self._eligibility = eligibility
        self._prefix = config.remnawave.username_prefix
        self._blacklist_repository = blacklist_repository
        self._translation_repository = translation_repository

    async def _execute(self, actor: Actor, data: SyncFromPanelOptionsDto) -> PanelSyncResultDto:
        async with holding_panel_sync(self._redis, PanelSyncDirection.FROM_PANEL):
            return await self._sync(actor, data)

    async def _fill_reset_anchors(
        self, subscriptions: list[SubscriptionDto], listed: dict[int, "_Listed"]
    ) -> None:
        """The panel's createdAt for rows without it or with another one, read off the
        listing: not counted as an update, nothing else on the row moves."""
        anchors = {
            s.remnawave_user_id: entry.created_at
            for s in subscriptions
            if (entry := listed.get(s.remnawave_user_id)) is not None
            and s.panel_created_at != entry.created_at
        }
        if anchors:
            async with self._uow:
                await self._subscription_repository.set_panel_created_at_many(anchors)

    async def _sync(self, actor: Actor, options: SyncFromPanelOptionsDto) -> PanelSyncResultDto:
        async with self._uow:
            subscriptions = await self._subscription_repository.list_non_terminal()
            linked = await self._subscription_repository.list_linked_remnawave_user_ids()
        updated = failed = missing = imported = users_created = over_limit = 0
        try:
            by_panel_id = {s.remnawave_user_id: s for s in subscriptions}
            listing = await scan_panel(
                self._remnawave_sdk, linked, partial(_summarize, by_panel_id)
            )
            await self._fill_reset_anchors(subscriptions, listing.tracked)
            for position, subscription in enumerate(subscriptions):
                entry = listing.tracked.get(subscription.remnawave_user_id)
                if entry is not None and entry.in_sync:
                    continue
                try:
                    fresh = await self._fresh(subscription.remnawave_user_id)
                except RemnawavePanelUnavailableError:
                    failed += len(subscriptions) - position
                    logger.warning(f"{actor.log} Panel sync from the panel stopped: panel down")
                    break
                except Exception:
                    logger.exception(f"Panel read failed for subscription '{subscription.id}'")
                    failed += 1
                    continue
                if fresh is None:
                    # a panel restored from an old backup lacks users the shop
                    # still sells to: an admin pushes them back or deletes them
                    missing += 1
                    continue
                if not differs_from_panel(subscription, fresh):
                    continue
                try:
                    await self._sync_use_case.system(
                        from_panel(fresh, subscription.remnawave_user_id, _SOURCE)
                    )
                except Exception:
                    logger.exception(f"Sync from the panel failed for '{subscription.id}'")
                    failed += 1
                    continue
                updated += 1
            else:
                imported, users_created, over_limit, import_failed = await self._import_orphans(
                    actor, listing.orphans, options
                )
                failed += import_failed
        except Exception:
            logger.exception(f"{actor.log} Panel sync from the panel failed")
            failed = len(subscriptions) - updated - missing
        result = PanelSyncResultDto(
            direction=PanelSyncDirection.FROM_PANEL,
            checked=len(subscriptions),
            updated=updated,
            failed=failed,
            missing=missing,
            imported=imported,
            users_created=users_created,
            over_limit=over_limit,
            finished_at=datetime_now(),
        )
        await finish_panel_sync(self._redis, PanelSyncDirection.FROM_PANEL, result)
        async with self._uow:
            await self._event_publisher.publish(
                PanelSyncCompletedEvent(
                    direction=result.direction,
                    checked=result.checked,
                    updated=result.updated,
                    failed=result.failed,
                    missing=result.missing,
                    imported=result.imported,
                    users_created=result.users_created,
                )
            )
        logger.info(f"{actor.log} Synced subscriptions from the panel: {result}")
        return result

    async def _import_orphans(
        self,
        actor: Actor,
        orphans: list[UserResponseDto],
        options: SyncFromPanelOptionsDto,
    ) -> tuple[int, int, int, int]:
        """(imported, users_created, over_limit, failed) for the panel users no
        subscription stands for."""
        if not orphans:
            return 0, 0, 0, 0
        async with self._uow:
            plans = await self._plan_repository.list_all()
            language = await self._translation_repository.get_default_locale()
        imported = users_created = over_limit = failed = 0
        for user in orphans:
            try:
                async with self._uow:
                    owner = (
                        await match_shop_user(self._user_repository, user, self._prefix)
                        if options.adopt_unnamed
                        else await named_shop_user(
                            self._user_repository, user.username, self._prefix
                        )
                    )
                    is_new = False
                    if owner is None and options.create_users:
                        owner = await self._create_owner(user, language)
                        is_new = owner is not None
                    if owner is None or owner.deleted_at is not None:
                        continue
                    # linked while the sync ran
                    existing = await self._subscription_repository.find_by_remnawave_user_id(
                        user.id
                    )
                    if existing is not None and existing.status is not SubscriptionStatus.DELETED:
                        continue
                    if options.respect_subscription_limit:
                        try:
                            await self._eligibility.assert_below_cap(owner.id)
                        except ValidationError:
                            over_limit += 1
                            logger.info(
                                f"{actor.log} Skipped panel user {user.id}: "
                                f"user '{owner.id}' is at the subscription cap"
                            )
                            continue
                    created = await self._subscription_repository.create(
                        imported_row(user, owner, plans)
                    )
            except Exception:
                logger.exception(f"Import of panel user {user.id} failed")
                failed += 1
                continue
            imported += 1
            users_created += is_new
            logger.info(
                f"{actor.log} Imported panel user {user.id} "
                f"as subscription '{created.id}' of user '{owner.id}'"
            )
        return imported, users_created, over_limit, failed

    async def _create_owner(self, user: UserResponseDto, language: Locale) -> Optional[UserDto]:
        """A customer for a panel user no shop user matches by any key, by its
        Telegram id; no registration event, so no greeting or referral."""
        telegram_id = user.telegram_id
        if (
            telegram_id is None
            or await self._user_repository.find_by_telegram_id(telegram_id) is not None
            or await named_shop_user(self._user_repository, user.username, self._prefix) is not None
            or (user.email and await self._user_repository.find_by_email(user.email) is not None)
            or await self._blacklist_repository.is_blacklisted_telegram_id(telegram_id)
        ):
            return None
        created = await self._user_repository.create(
            CreateUserDto(name="", telegram_id=telegram_id, language=language)
        )
        logger.info(f"Created user '{created.id}' for panel user {user.id}")
        return created

    async def _fresh(self, remnawave_user_id: int) -> Optional[UserResponseDto]:
        try:
            return await self._remnawave_sdk.users.get_user_by_id(remnawave_user_id)
        except RemnawaveNotFoundError:
            return None


@dataclass(frozen=True, kw_only=True)
class _Listed:
    in_sync: bool
    created_at: datetime


def _summarize(by_panel_id: dict[int, SubscriptionDto], user: UserResponseDto) -> Optional[_Listed]:
    subscription = by_panel_id.get(user.id)
    if subscription is None:
        return None
    return _Listed(in_sync=not differs_from_panel(subscription, user), created_at=user.created_at)
