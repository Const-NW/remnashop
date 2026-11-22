from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from redis.asyncio import Redis
from remnapy import RemnawaveSDK
from remnapy.enums import TrafficLimitStrategy, UserStatus
from remnapy.models import UserResponseDto

from app.core.config import AppConfig
from app.core.constants import GB_TO_BYTES, MB_TO_BYTES
from app.core.db.uow import UnitOfWork
from app.core.enums import Locale, Role, SubscriptionStatus
from app.core.ports import EventPublisher
from app.core.utils.time import datetime_now, truncate_to_ms
from app.modules.settings import SettingsRepository
from app.modules.settings.dto import GraceSettingsDto, UpdateGraceSettingsDto
from app.modules.subscriptions.credit_segment_repository import CreditSegmentRepository
from app.modules.subscriptions.dto import (
    PushSubscriptionToPanelDto,
    SubscriptionDto,
    SyncSubscriptionStatusDto,
    SyncToPanelOptionsDto,
)
from app.modules.subscriptions.lifecycle import SubscriptionLifecycleService
from app.modules.subscriptions.panel import restore
from app.modules.subscriptions.panel_sync import from_panel
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.subscriptions.usecases import (
    PullSubscriptionFromPanelUseCase,
    PushSubscriptionToPanelUseCase,
    SyncAllToPanelUseCase,
    SyncSubscriptionStatusUseCase,
)
from app.modules.users.dto import UserDto
from app.modules.users.repository import UserRepository
from tests.fakes.remnawave import FakeUserStore, _user_payload
from tests.modules.subscriptions.factories import create_subscription, make_subscription_dto

OWNER = UserDto(login="owner", name="owner", role=Role.OWNER, language=Locale.EN)
_GRACE_SQUAD = uuid4()


def _sync(
    subscription_repository: SubscriptionRepository, uow: UnitOfWork, publisher: EventPublisher
) -> SyncSubscriptionStatusUseCase:
    return SyncSubscriptionStatusUseCase(
        subscription_repository,
        uow,
        publisher,
        SubscriptionLifecycleService(
            subscription_repository, CreditSegmentRepository(subscription_repository.session)
        ),
    )


async def _in_grace(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    panel_id: int,
) -> SubscriptionDto:
    await settings_repository.update_grace_settings(
        UpdateGraceSettingsDto(
            enabled=True,
            internal_squads=[_GRACE_SQUAD],
            traffic_limit_mb=512,
            traffic_limit_strategy=TrafficLimitStrategy.DAY,
            tag="GRACE",
            duration_days=3,
            fields_set=frozenset(
                {
                    "enabled",
                    "internal_squads",
                    "traffic_limit_mb",
                    "traffic_limit_strategy",
                    "tag",
                    "duration_days",
                }
            ),
        )
    )
    created = await create_subscription(
        subscription_repository,
        user_repository,
        telegram_id=panel_id,
        remnawave_user_id=panel_id,
        status=SubscriptionStatus.EXPIRED,
        expire_at=datetime_now() - timedelta(hours=2),
    )
    return await subscription_repository.set_grace_until(
        created.id, datetime_now() + timedelta(days=3)
    )


def _grace_panel_user(
    subscription: SubscriptionDto, expire_at: datetime, **overrides: Any
) -> dict[str, Any]:
    payload = _user_payload(
        user_id=subscription.remnawave_user_id,
        username=f"rs_{subscription.remnawave_user_id}",
        expire_at=expire_at,
        traffic_limit_bytes=512 * MB_TO_BYTES,
        traffic_limit_strategy="DAY",
        hwid_device_limit=3,
        active_internal_squads=[str(_GRACE_SQUAD)],
    )
    payload["tag"] = "GRACE"
    payload["subscriptionUrl"] = subscription.url
    payload.update(overrides)
    return payload


def _assert_row_kept(before: SubscriptionDto, after: SubscriptionDto) -> None:
    assert after.status is SubscriptionStatus.EXPIRED
    assert truncate_to_ms(after.expire_at) == truncate_to_ms(before.expire_at)
    assert after.grace_until == before.grace_until
    assert (after.traffic_limit, after.traffic_limit_strategy, after.tag) == (
        before.traffic_limit,
        before.traffic_limit_strategy,
        before.tag,
    )
    assert after.internal_squads == before.internal_squads


async def test_echo_during_grace_keeps_row(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
) -> None:
    before = await _in_grace(subscription_repository, user_repository, settings_repository, 93_001)
    assert before.grace_until is not None
    echo = UserResponseDto.model_validate(_grace_panel_user(before, before.grace_until))

    after = await _sync(subscription_repository, uow, event_publisher).system(
        from_panel(echo, before.remnawave_user_id, "webhook")
    )

    assert after is not None
    _assert_row_kept(before, after)


async def test_pull_during_grace_keeps_row(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
) -> None:
    before = await _in_grace(subscription_repository, user_repository, settings_repository, 93_002)
    assert before.grace_until is not None
    remnawave_store[93_002] = _grace_panel_user(before, before.grace_until)

    after = await PullSubscriptionFromPanelUseCase(
        subscription_repository,
        user_repository,
        remnawave_sdk,
        _sync(subscription_repository, uow, event_publisher),
        event_publisher,
        uow,
    )(OWNER, before.id)

    _assert_row_kept(before, after)


async def test_panel_extension_exits_grace(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
) -> None:
    before = await _in_grace(subscription_repository, user_repository, settings_repository, 93_003)
    extended = datetime_now() + timedelta(days=30)
    panel = UserResponseDto.model_validate(
        _grace_panel_user(
            before,
            extended,
            trafficLimitBytes=50 * GB_TO_BYTES,
            trafficLimitStrategy="MONTH",
            tag=None,
            activeInternalSquads=[],
        )
    )

    after = await _sync(subscription_repository, uow, event_publisher).system(
        from_panel(panel, before.remnawave_user_id, "webhook")
    )

    assert after is not None
    assert after.grace_until is None
    assert after.status is SubscriptionStatus.ACTIVE
    assert truncate_to_ms(after.expire_at) == truncate_to_ms(extended)
    assert (after.traffic_limit, after.traffic_limit_strategy, after.tag) == (
        50,
        TrafficLimitStrategy.MONTH,
        None,
    )
    assert after.internal_squads == []


async def test_push_during_grace_sends_grace_config(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
) -> None:
    subscription = await _in_grace(
        subscription_repository, user_repository, settings_repository, 93_004
    )
    assert subscription.grace_until is not None
    remnawave_store[93_004] = _grace_panel_user(
        subscription,
        subscription.grace_until,
        trafficLimitBytes=100 * GB_TO_BYTES,
        trafficLimitStrategy="NO_RESET",
        tag=None,
    )

    await PushSubscriptionToPanelUseCase(
        subscription_repository,
        user_repository,
        remnawave_sdk,
        event_publisher,
        uow,
        AppConfig.get(),
        settings_repository,
    )(OWNER, PushSubscriptionToPanelDto(subscription_id=subscription.id))

    pushed = remnawave_store[93_004]
    assert pushed["trafficLimitBytes"] == 512 * MB_TO_BYTES
    assert pushed["trafficLimitStrategy"] == "DAY"
    assert pushed["tag"] == "GRACE"
    assert pushed["status"] == "ACTIVE"
    assert truncate_to_ms(
        datetime.fromisoformat(str(pushed["expireAt"]).replace("Z", "+00:00"))
    ) == truncate_to_ms(subscription.grace_until)


async def test_bulk_to_panel_skips_grace_as_not_lagging(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
    redis: Redis,
) -> None:
    subscription = await _in_grace(
        subscription_repository, user_repository, settings_repository, 93_005
    )
    assert subscription.grace_until is not None
    remnawave_store[93_005] = _grace_panel_user(subscription, subscription.grace_until)

    await SyncAllToPanelUseCase(
        subscription_repository,
        user_repository,
        remnawave_sdk,
        uow,
        redis,
        event_publisher,
        AppConfig.get(),
        settings_repository,
    ).system(SyncToPanelOptionsDto(recreate_missing=False, delete_orphans=False))

    kept = remnawave_store[93_005]
    assert kept["trafficLimitBytes"] == 512 * MB_TO_BYTES
    assert kept["tag"] == "GRACE"
    assert kept["status"] == "ACTIVE"


async def test_url_still_synced_in_grace(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
) -> None:
    before = await _in_grace(subscription_repository, user_repository, settings_repository, 93_006)
    assert before.grace_until is not None
    echo = UserResponseDto.model_validate(
        _grace_panel_user(before, before.grace_until, subscriptionUrl="https://example.com/sub/new")
    )

    after = await _sync(subscription_repository, uow, event_publisher).system(
        from_panel(echo, before.remnawave_user_id, "webhook")
    )

    assert after is not None
    assert after.url == "https://example.com/sub/new"
    _assert_row_kept(before, after)


def test_restore_in_grace_keeps_an_admins_switch_off() -> None:
    until = datetime_now() + timedelta(days=3)
    settings = GraceSettingsDto(enabled=True, traffic_limit_mb=512)
    disabled = make_subscription_dto(status=SubscriptionStatus.DISABLED, grace_until=until)

    assert restore(disabled, grace=settings).status is None
    assert (
        restore(
            disabled.model_copy(update={"status": SubscriptionStatus.EXPIRED}), grace=settings
        ).status
        == UserStatus.ACTIVE
    )


async def test_panel_deletion_in_grace_deletes_the_row(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
) -> None:
    before = await _in_grace(subscription_repository, user_repository, settings_repository, 93_007)

    after = await _sync(subscription_repository, uow, event_publisher).system(
        SyncSubscriptionStatusDto(
            remnawave_user_id=before.remnawave_user_id,
            status=SubscriptionStatus.DELETED,
            source="webhook",
        )
    )

    assert after is not None
    assert after.status is SubscriptionStatus.DELETED
    assert after.grace_until is None


def test_restore_after_the_grace_end_leaves_out_the_past_expiry() -> None:
    settings = GraceSettingsDto(enabled=True, traffic_limit_mb=512)
    lapsed = make_subscription_dto(
        status=SubscriptionStatus.EXPIRED, grace_until=datetime_now() - timedelta(minutes=1)
    )

    assert restore(lapsed, grace=settings).expire_at is None
