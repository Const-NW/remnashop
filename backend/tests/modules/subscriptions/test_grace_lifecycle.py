from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Final, Optional
from uuid import UUID, uuid4

import httpx
import pytest
from redis.asyncio import Redis
from remnapy import RemnawaveSDK
from remnapy.enums import TrafficLimitStrategy
from remnapy.exceptions import ApiError, ApiErrorResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import AppConfig
from app.core.constants import GB_TO_BYTES, MB_TO_BYTES
from app.core.db.models import EventOutboxEntry, Subscription
from app.core.db.uow import UnitOfWork
from app.core.enums import Locale, PlanType, Role, SubscriptionStatus
from app.core.exceptions import RetryableTaskError
from app.core.ports import EventPublisher
from app.core.utils.time import datetime_now, truncate_to_ms
from app.modules.plans.dto import (
    CreatePlanDto,
    PlanDto,
    SetPlanDurationDto,
    SetPlanDurationsDto,
    UpdatePlanDto,
)
from app.modules.plans.repository import PlanRepository
from app.modules.settings import SettingsRepository
from app.modules.settings.dto import UpdateGraceSettingsDto
from app.modules.subscriptions.credit_segment_repository import CreditSegmentRepository
from app.modules.subscriptions.dto import (
    ProcessRemnawaveWebhookDto,
    SetSubscriptionEnabledDto,
    SubscriptionDto,
    UpdateSubscriptionSquadsDto,
)
from app.modules.subscriptions.lifecycle import SubscriptionLifecycleService
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.subscriptions.usecases import (
    EndGraceUseCase,
    EnterGraceUseCase,
    ProcessRemnawaveWebhookUseCase,
    PublishPanelEventUseCase,
    RestoreDuePanelConfigsUseCase,
    SetSubscriptionEnabledUseCase,
    SweepGraceUseCase,
    SyncSubscriptionStatusUseCase,
)
from app.modules.users.dto import UserDto
from app.modules.users.repository import UserRepository
from tests.fakes.remnawave import FakeUserStore, _user_payload
from tests.fakes.remnawave_webhook import sign_webhook, user_event_payload
from tests.modules.subscriptions.factories import create_subscription

OWNER: Final[UserDto] = UserDto(login="owner", name="owner", role=Role.OWNER, language=Locale.EN)
_GRACE_SQUAD: Final[UUID] = uuid4()
_PLAN_SQUAD: Final[UUID] = uuid4()
_CUSTOMER_EVENTS: Final[tuple[str, ...]] = (
    "SubscriptionGraceStartedEvent",
    "SubscriptionExpiredEvent",
    "SubscriptionExpiringEvent",
    "SubscriptionExpiredAgoEvent",
    "SubscriptionTrafficLimitedEvent",
)


class _Grace:
    def __init__(
        self,
        session: AsyncSession,
        sdk: RemnawaveSDK,
        uow: UnitOfWork,
        publisher: EventPublisher,
        redis: Redis,
    ) -> None:
        subscriptions = SubscriptionRepository(session)
        users = UserRepository(session)
        sync = SyncSubscriptionStatusUseCase(
            subscriptions,
            uow,
            publisher,
            SubscriptionLifecycleService(subscriptions, CreditSegmentRepository(session)),
            sdk,
            SettingsRepository(session),
        )
        self.enter = EnterGraceUseCase(
            subscriptions,
            PlanRepository(session),
            SettingsRepository(session),
            users,
            SubscriptionLifecycleService(subscriptions, CreditSegmentRepository(session)),
            sdk,
            uow,
            publisher,
            sync,
        )
        self.end = EndGraceUseCase(
            subscriptions, users, SettingsRepository(session), sdk, uow, publisher
        )
        self.sweep = SweepGraceUseCase(
            subscriptions, SettingsRepository(session), uow, self.enter, self.end
        )
        self.restore = RestoreDuePanelConfigsUseCase(
            subscriptions, SettingsRepository(session), sdk, uow
        )
        publish = PublishPanelEventUseCase(subscriptions, users, uow, publisher)
        self.webhook = ProcessRemnawaveWebhookUseCase(
            sync,
            publish,
            self.enter,
            self.end,
            subscriptions,
            SettingsRepository(session),
            uow,
            redis,
        )


@pytest.fixture
def grace(
    session: AsyncSession,
    remnawave_sdk: RemnawaveSDK,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
    redis: Redis,
) -> _Grace:
    return _Grace(session, remnawave_sdk, uow, event_publisher, redis)


async def _enable_grace(settings_repository: SettingsRepository) -> None:
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


async def _paid_plan(plan_repository: PlanRepository) -> PlanDto:
    plan = await plan_repository.create(
        CreatePlanDto(
            public_code=f"grace{uuid4().hex[:6]}",
            name=f"grace{uuid4().hex[:6]}",
            type=PlanType.BOTH,
            traffic_limit=100,
            device_limit=3,
        )
    )
    await plan_repository.set_durations(
        SetPlanDurationsDto(
            plan_id=plan.id,
            durations=[SetPlanDurationDto(days=30, order_index=0, base_price=Decimal("500.00"))],
        )
    )
    return await plan_repository.update(UpdatePlanDto(plan_id=plan.id, is_active=True))


async def _expired(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    plan: PlanDto,
    store: FakeUserStore,
    panel_id: int,
    *,
    expire_at: Optional[datetime] = None,
    is_trial: bool = False,
) -> SubscriptionDto:
    expire_at = truncate_to_ms(expire_at or datetime_now() - timedelta(minutes=5))
    subscription = await create_subscription(
        subscription_repository,
        user_repository,
        telegram_id=panel_id,
        remnawave_user_id=panel_id,
        status=SubscriptionStatus.EXPIRED,
        expire_at=expire_at,
        is_trial=is_trial,
        plan_id=plan.id,
    )
    await subscription_repository.update_squads(
        UpdateSubscriptionSquadsDto(
            subscription_id=subscription.id, internal_squads=[_PLAN_SQUAD], external_squad=None
        )
    )
    subscription = await subscription_repository.set_tag(subscription.id, "PLAN")
    payload = _user_payload(
        user_id=panel_id,
        username=f"rs_{panel_id}",
        expire_at=expire_at,
        traffic_limit_bytes=100 * GB_TO_BYTES,
        hwid_device_limit=3,
        active_internal_squads=[str(_PLAN_SQUAD)],
    )
    payload["status"] = "EXPIRED"
    payload["tag"] = "PLAN"
    store[panel_id] = payload
    return subscription


def _webhook(
    config: AppConfig,
    event: str,
    panel_id: int,
    status: str,
    expire_at: datetime,
    meta: Optional[dict[str, Any]] = None,
    **data: Any,
) -> ProcessRemnawaveWebhookDto:
    payload = user_event_payload(event=event, remnawave_user_id=panel_id, status=status)
    payload["data"]["expireAt"] = expire_at.isoformat()
    payload["data"].update(data)
    if meta is not None:
        payload["meta"] = meta
    secret = config.remnawave.webhook_secret.get_secret_value()  # type: ignore[union-attr]
    body, _ = sign_webhook(payload, secret)
    return ProcessRemnawaveWebhookDto(body=body.decode())


async def _events(session: AsyncSession, subscription_id: int) -> list[str]:
    result = await session.scalars(
        select(EventOutboxEntry.event_type)
        .where(EventOutboxEntry.payload["subscription_id"].as_integer() == subscription_id)
        .where(EventOutboxEntry.event_type.in_(_CUSTOMER_EVENTS))
        .order_by(EventOutboxEntry.id)
    )
    return list(result.all())


def _panel_expire_at(store: FakeUserStore, panel_id: int) -> datetime:
    return datetime.fromisoformat(str(store[panel_id]["expireAt"]).replace("Z", "+00:00"))


async def test_expired_webhook_enters_grace(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
    config: AppConfig,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    before = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_001)

    await grace.webhook.system(
        _webhook(config, "user.expired", 94_001, "EXPIRED", before.expire_at)
    )

    after = await subscription_repository.get_by_id(before.id)
    assert after.status is SubscriptionStatus.EXPIRED
    assert truncate_to_ms(after.expire_at) == truncate_to_ms(before.expire_at)
    assert after.grace_until is not None
    assert after.grace_until > datetime_now() + timedelta(days=2)
    panel = remnawave_store[94_001]
    assert panel["status"] == "ACTIVE"
    assert truncate_to_ms(_panel_expire_at(remnawave_store, 94_001)) == truncate_to_ms(
        after.grace_until
    )
    assert panel["trafficLimitBytes"] == 512 * MB_TO_BYTES
    assert panel["tag"] == "GRACE"
    assert panel["lastTrafficResetAt"] is not None
    assert await _events(session, before.id) == ["SubscriptionGraceStartedEvent"]


async def test_disabled_grace_keeps_current_path(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
    config: AppConfig,
) -> None:
    plan = await _paid_plan(plan_repository)
    before = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_002)

    await grace.webhook.system(
        _webhook(config, "user.expired", 94_002, "EXPIRED", before.expire_at)
    )

    after = await subscription_repository.get_by_id(before.id)
    assert after.grace_until is None
    assert remnawave_store[94_002]["status"] == "EXPIRED"
    assert remnawave_store[94_002]["lastTrafficResetAt"] is None
    assert await _events(session, before.id) == ["SubscriptionExpiredEvent"]


async def test_trial_gets_no_grace(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
    config: AppConfig,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    before = await _expired(
        subscription_repository, user_repository, plan, remnawave_store, 94_003, is_trial=True
    )

    await grace.webhook.system(
        _webhook(config, "user.expired", 94_003, "EXPIRED", before.expire_at)
    )

    assert (await subscription_repository.get_by_id(before.id)).grace_until is None
    assert await _events(session, before.id) == ["SubscriptionExpiredEvent"]


def _panel_error(status: int) -> ApiError:
    return ApiError(status, ApiErrorResponse(message="fake panel"))


async def test_failed_grace_push_that_never_landed_clears_claim(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    before = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_004)

    async def _down(_request: Any) -> Any:
        raise _panel_error(500)

    monkeypatch.setattr(remnawave_sdk.users, "update_user", _down)

    with pytest.raises(RetryableTaskError):
        await grace.enter.system(before.id)

    assert (await subscription_repository.get_by_id(before.id)).grace_until is None
    assert await _events(session, before.id) == []


async def test_timed_out_grace_push_that_landed_keeps_the_claim(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    before = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_014)
    real_update_user = remnawave_sdk.users.update_user

    async def _applied_then_timeout(request: Any) -> Any:
        await real_update_user(request)
        raise httpx.ReadTimeout("fake panel")

    monkeypatch.setattr(remnawave_sdk.users, "update_user", _applied_then_timeout)

    assert await grace.enter.system(before.id) is True

    after = await subscription_repository.get_by_id(before.id)
    assert after.grace_until is not None
    assert truncate_to_ms(_panel_expire_at(remnawave_store, 94_014)) == truncate_to_ms(
        after.grace_until
    )
    assert await _events(session, before.id) == ["SubscriptionGraceStartedEvent"]


async def test_unreadable_panel_after_a_failed_grace_push_keeps_the_claim(
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
    redis: Redis,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    before = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_015)
    broken = RemnawaveSDK(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(500, json={"message": "panel down"})
            ),
            base_url="http://fake-panel.test/api",
        )
    )

    with pytest.raises(RetryableTaskError):
        await _Grace(session, broken, uow, event_publisher, redis).enter.system(before.id)

    # the write may have landed: the retry pushes the same grace again
    assert (await subscription_repository.get_by_id(before.id)).grace_until is not None
    assert await _events(session, before.id) == []


async def test_retry_enters_once(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    before = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_005)

    assert await grace.enter.system(before.id) is True
    first = (await subscription_repository.get_by_id(before.id)).grace_until
    assert await grace.enter.system(before.id) is True

    assert (await subscription_repository.get_by_id(before.id)).grace_until == first
    assert await _events(session, before.id) == ["SubscriptionGraceStartedEvent"]


async def test_repeat_expired_during_grace_ignored(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
    config: AppConfig,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    before = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_006)
    await grace.webhook.system(
        _webhook(config, "user.expired", 94_006, "EXPIRED", before.expire_at)
    )
    in_grace = await subscription_repository.get_by_id(before.id)

    await grace.webhook.system(
        _webhook(config, "user.expired", 94_006, "EXPIRED", before.expire_at)
    )

    assert (await subscription_repository.get_by_id(before.id)).grace_until == (
        in_grace.grace_until
    )
    assert await _events(session, before.id) == ["SubscriptionGraceStartedEvent"]


async def _grace_ended(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    store: FakeUserStore,
    panel_id: int,
) -> tuple[SubscriptionDto, datetime]:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    ended_at = truncate_to_ms(datetime_now() - timedelta(minutes=1))
    subscription = await _expired(
        subscription_repository,
        user_repository,
        plan,
        store,
        panel_id,
        expire_at=ended_at - timedelta(days=3),
    )
    subscription = await subscription_repository.set_grace_until(subscription.id, ended_at)
    # the panel as the grace push left it, expired by itself at grace_until
    store[panel_id].update(
        {
            "expireAt": ended_at.isoformat(),
            "trafficLimitBytes": 512 * MB_TO_BYTES,
            "trafficLimitStrategy": "DAY",
            "tag": "GRACE",
            "activeInternalSquads": [{"uuid": str(_GRACE_SQUAD), "name": "grace"}],
        }
    )
    return subscription, ended_at


async def test_grace_end_restores_plan_config(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
    config: AppConfig,
) -> None:
    subscription, ended_at = await _grace_ended(
        subscription_repository,
        user_repository,
        settings_repository,
        plan_repository,
        remnawave_store,
        94_007,
    )

    await grace.webhook.system(_webhook(config, "user.expired", 94_007, "EXPIRED", ended_at))

    after = await subscription_repository.get_by_id(subscription.id)
    assert after.grace_until is None
    assert after.status is SubscriptionStatus.EXPIRED
    panel = remnawave_store[94_007]
    assert panel["trafficLimitBytes"] == 100 * GB_TO_BYTES
    assert panel["trafficLimitStrategy"] == "NO_RESET"
    assert panel["tag"] == "PLAN"
    assert panel["status"] == "EXPIRED"
    assert await _events(session, subscription.id) == ["SubscriptionExpiredEvent"]


async def test_no_reentry_after_end(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
    config: AppConfig,
) -> None:
    subscription, ended_at = await _grace_ended(
        subscription_repository,
        user_repository,
        settings_repository,
        plan_repository,
        remnawave_store,
        94_008,
    )
    await grace.webhook.system(_webhook(config, "user.expired", 94_008, "EXPIRED", ended_at))
    # the end push's echo: the panel keeps grace_until as its expiry
    await grace.webhook.system(
        _webhook(
            config,
            "user.modified",
            94_008,
            "EXPIRED",
            ended_at,
            trafficLimitBytes=100 * GB_TO_BYTES,
            tag="PLAN",
            activeInternalSquads=[{"uuid": str(_PLAN_SQUAD), "name": "plan"}],
        )
    )
    await grace.sweep.system()
    await grace.webhook.system(_webhook(config, "user.expired", 94_008, "EXPIRED", ended_at))

    after = await subscription_repository.get_by_id(subscription.id)
    assert after.grace_until is None
    assert remnawave_store[94_008]["status"] == "EXPIRED"
    assert await _events(session, subscription.id) == ["SubscriptionExpiredEvent"]


async def test_grace_end_echo_keeps_the_real_expiry(
    grace: _Grace,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
    config: AppConfig,
) -> None:
    subscription, ended_at = await _grace_ended(
        subscription_repository,
        user_repository,
        settings_repository,
        plan_repository,
        remnawave_store,
        94_051,
    )
    await grace.webhook.system(_webhook(config, "user.expired", 94_051, "EXPIRED", ended_at))
    # the panel refuses a past expiry, so it keeps the grace end as its own
    await grace.webhook.system(_webhook(config, "user.modified", 94_051, "EXPIRED", ended_at))

    after = await subscription_repository.get_by_id(subscription.id)
    assert truncate_to_ms(after.expire_at) == truncate_to_ms(subscription.expire_at)


async def test_sweep_picks_missed_webhook(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
) -> None:
    ended, _ = await _grace_ended(
        subscription_repository,
        user_repository,
        settings_repository,
        plan_repository,
        remnawave_store,
        94_009,
    )
    plan = await _paid_plan(plan_repository)
    missed = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_010)
    too_old = await _expired(
        subscription_repository,
        user_repository,
        plan,
        remnawave_store,
        94_011,
        expire_at=datetime_now() - timedelta(days=2),
    )

    await grace.sweep.system()

    assert (await subscription_repository.get_by_id(missed.id)).grace_until is not None
    assert await _events(session, missed.id) == ["SubscriptionGraceStartedEvent"]
    assert (await subscription_repository.get_by_id(ended.id)).grace_until is None
    assert await _events(session, ended.id) == ["SubscriptionExpiredEvent"]
    assert (await subscription_repository.get_by_id(too_old.id)).grace_until is None
    assert await _events(session, too_old.id) == []


async def test_expiration_and_limited_muted_in_grace(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
    config: AppConfig,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    before = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_012)
    assert await grace.enter.system(before.id) is True
    grace_until = (await subscription_repository.get_by_id(before.id)).grace_until
    assert grace_until is not None
    moment = datetime.now(timezone.utc)
    assert grace_until > moment

    await grace.webhook.system(
        _webhook(config, "user.expiration", 94_012, "ACTIVE", grace_until, {"expiration": -24})
    )
    await grace.webhook.system(_webhook(config, "user.limited", 94_012, "LIMITED", grace_until))

    assert await _events(session, before.id) == ["SubscriptionGraceStartedEvent"]


async def test_expired_with_unpushed_claim_pushes_grace(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
    config: AppConfig,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    before = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_013)
    # the claim committed, the worker died before the push
    claimed = await subscription_repository.set_grace_until(
        before.id, datetime_now() + timedelta(days=3)
    )
    assert claimed.grace_until is not None

    await grace.webhook.system(
        _webhook(config, "user.expired", 94_013, "EXPIRED", before.expire_at)
    )

    panel = remnawave_store[94_013]
    assert panel["status"] == "ACTIVE"
    assert panel["trafficLimitBytes"] == 512 * MB_TO_BYTES
    assert truncate_to_ms(_panel_expire_at(remnawave_store, 94_013)) == truncate_to_ms(
        claimed.grace_until
    )
    assert panel["lastTrafficResetAt"] is not None
    assert (await subscription_repository.get_by_id(before.id)).grace_until == claimed.grace_until
    # the interrupted entry is finished once
    await grace.webhook.system(
        _webhook(config, "user.expired", 94_013, "EXPIRED", before.expire_at)
    )
    assert await _events(session, before.id) == ["SubscriptionGraceStartedEvent"]


async def test_enable_in_grace_keeps_the_row_expired_on_the_grace_config(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    event_publisher: EventPublisher,
    uow: UnitOfWork,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    before = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_101)
    assert await grace.enter.system(before.id)
    toggle = SetSubscriptionEnabledUseCase(
        subscription_repository,
        user_repository,
        settings_repository,
        remnawave_sdk,
        SubscriptionLifecycleService(subscription_repository, CreditSegmentRepository(session)),
        event_publisher,
        uow,
    )

    disabled = await toggle(
        OWNER, SetSubscriptionEnabledDto(subscription_id=before.id, enabled=False)
    )
    assert disabled.status is SubscriptionStatus.DISABLED
    remnawave_store[94_101]["tag"] = "DRIFTED"
    enabled = await toggle(
        OWNER, SetSubscriptionEnabledDto(subscription_id=before.id, enabled=True)
    )

    assert enabled.status is SubscriptionStatus.EXPIRED
    assert enabled.grace_until is not None
    assert truncate_to_ms(enabled.expire_at) == truncate_to_ms(before.expire_at)
    panel = remnawave_store[94_101]
    assert panel["status"] == "ACTIVE"
    assert panel["tag"] == "GRACE"
    assert truncate_to_ms(_panel_expire_at(remnawave_store, 94_101)) == truncate_to_ms(
        enabled.grace_until
    )


async def test_renewal_landing_before_the_grace_push_wins(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    before = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_102)
    renewed_until = truncate_to_ms(datetime_now() + timedelta(days=30))
    lifecycle = SubscriptionLifecycleService(
        subscription_repository, CreditSegmentRepository(session)
    )
    real_update_user = remnawave_sdk.users.update_user
    calls = 0

    async def renewal_then_push(request: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            # a renewal reads the claim, pushes its plan config and clears grace first
            async with uow:
                await lifecycle.sync_expire_at(before.id, renewed_until)
                await lifecycle.sync_status(before.id, SubscriptionStatus.ACTIVE)
                await subscription_repository.set_grace_until(before.id, None)
            remnawave_store[94_102]["expireAt"] = renewed_until.isoformat()
        return await real_update_user(request)

    monkeypatch.setattr(remnawave_sdk.users, "update_user", renewal_then_push)

    assert await grace.enter.system(before.id) is False

    row = await subscription_repository.get_by_id(before.id)
    assert row.grace_until is None
    panel = remnawave_store[94_102]
    assert truncate_to_ms(_panel_expire_at(remnawave_store, 94_102)) == renewed_until
    assert panel["tag"] == "PLAN"
    assert panel["activeInternalSquads"][0]["uuid"] == str(_PLAN_SQUAD)
    assert await _events(session, before.id) == []


async def test_sweep_reaches_an_eligible_row_past_a_batch_of_free_and_closed_plans(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
) -> None:
    await _enable_grace(settings_repository)
    free = await plan_repository.create(
        CreatePlanDto(
            public_code=f"free{uuid4().hex[:6]}",
            name=f"free{uuid4().hex[:6]}",
            type=PlanType.BOTH,
            traffic_limit=100,
            device_limit=3,
        )
    )
    await plan_repository.set_durations(
        SetPlanDurationsDto(
            plan_id=free.id,
            durations=[SetPlanDurationDto(days=30, order_index=0, base_price=Decimal("0"))],
        )
    )
    closed = await _paid_plan(plan_repository)
    closed = await plan_repository.update(UpdatePlanDto(plan_id=closed.id, allow_renewal=False))
    expire_at = datetime_now() - timedelta(minutes=5)
    for offset in range(101):
        await create_subscription(
            subscription_repository,
            user_repository,
            telegram_id=94_200 + offset,
            remnawave_user_id=94_200 + offset,
            status=SubscriptionStatus.EXPIRED,
            expire_at=expire_at,
            plan_id=(free if offset % 2 else closed).id,
        )
    eligible = await _expired(
        subscription_repository,
        user_repository,
        await _paid_plan(plan_repository),
        remnawave_store,
        94_400,
    )

    await grace.sweep.system()

    assert (await subscription_repository.get_by_id(eligible.id)).grace_until is not None


async def test_a_row_that_keeps_failing_does_not_hold_up_the_grace_end(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.modules.subscriptions.usecases.sweep_grace._BATCH", 1)
    stuck, _ = await _grace_ended(
        subscription_repository,
        user_repository,
        settings_repository,
        plan_repository,
        remnawave_store,
        94_501,
    )
    waiting, _ = await _grace_ended(
        subscription_repository,
        user_repository,
        settings_repository,
        plan_repository,
        remnawave_store,
        94_502,
    )
    real_update_user = remnawave_sdk.users.update_user

    async def _down_for_stuck(request: Any) -> Any:
        if request.id == 94_501:
            raise _panel_error(500)
        return await real_update_user(request)

    monkeypatch.setattr(remnawave_sdk.users, "update_user", _down_for_stuck)

    await grace.sweep.system()
    await grace.sweep.system()

    assert (await subscription_repository.get_by_id(stuck.id)).grace_until is not None
    assert (await subscription_repository.get_by_id(waiting.id)).grace_until is None
    assert await _events(session, waiting.id) == ["SubscriptionExpiredEvent"]


async def test_a_row_that_keeps_failing_does_not_hold_up_grace_entry(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.modules.subscriptions.usecases.sweep_grace._BATCH", 1)
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    stuck = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_503)
    waiting = await _expired(
        subscription_repository, user_repository, plan, remnawave_store, 94_504
    )
    del remnawave_store[94_503]

    await grace.sweep.system()
    await grace.sweep.system()

    assert (await subscription_repository.get_by_id(stuck.id)).grace_until is None
    assert (await subscription_repository.get_by_id(waiting.id)).grace_until is not None


async def test_repeat_grace_entry_does_not_reset_traffic_again(
    grace: _Grace,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    subscription = await _expired(
        subscription_repository, user_repository, plan, remnawave_store, 94_505
    )
    resets: list[int] = []

    async def _reset(user_id: int) -> object:
        resets.append(user_id)
        return None

    monkeypatch.setattr(remnawave_sdk.users, "reset_user_traffic", _reset)

    assert await grace.enter.system(subscription.id)
    assert await grace.enter.system(subscription.id)

    assert resets == [94_505]


async def test_grace_end_of_a_disabled_subscription_sends_no_expiry_notice(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
) -> None:
    subscription, _ = await _grace_ended(
        subscription_repository,
        user_repository,
        settings_repository,
        plan_repository,
        remnawave_store,
        94_506,
    )
    # an admin switched it off during grace
    await session.execute(
        update(Subscription)
        .where(Subscription.id == subscription.id)
        .values(status=SubscriptionStatus.DISABLED)
    )

    await grace.end.system(subscription.id)

    after = await subscription_repository.get_by_id(subscription.id)
    assert after.grace_until is None
    assert after.status is SubscriptionStatus.DISABLED
    assert await _events(session, subscription.id) == []


async def test_marking_a_sweep_attempt_leaves_updated_at_alone(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
) -> None:
    plan = await _paid_plan(plan_repository)
    subscription = await _expired(
        subscription_repository, user_repository, plan, remnawave_store, 94_507
    )
    before = datetime(2026, 1, 1, tzinfo=timezone.utc)
    session = subscription_repository.session
    await session.execute(
        update(Subscription).where(Subscription.id == subscription.id).values(updated_at=before)
    )

    await subscription_repository.mark_grace_attempted([subscription.id], datetime_now())

    row = await session.get(Subscription, subscription.id)
    assert row is not None
    await session.refresh(row)
    assert row.grace_attempted_at is not None
    assert row.updated_at == before


async def _replace_by_plan_change(
    uow: UnitOfWork,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    replaced: SubscriptionDto,
    telegram_id: int,
) -> SubscriptionDto:
    """A plan change committing while a grace push is in flight: same panel user, new row."""
    async with uow:
        await subscription_repository.mark_deleted(replaced.id)
        new = await create_subscription(
            subscription_repository,
            user_repository,
            telegram_id=telegram_id,
            remnawave_user_id=replaced.remnawave_user_id,
        )
        return await subscription_repository.set_tag(new.id, "NEWPLAN")


async def test_plan_change_during_the_grace_push_gets_the_new_rows_config(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    before = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_601)
    real_update_user = remnawave_sdk.users.update_user
    replacing: list[SubscriptionDto] = []

    async def change_then_push(request: Any) -> Any:
        if not replacing:
            replacing.append(
                await _replace_by_plan_change(
                    uow, subscription_repository, user_repository, before, 94_611
                )
            )
        return await real_update_user(request)

    monkeypatch.setattr(remnawave_sdk.users, "update_user", change_then_push)

    assert await grace.enter.system(before.id) is False

    new = replacing[0]
    assert remnawave_store[94_601]["tag"] == "NEWPLAN"
    assert truncate_to_ms(_panel_expire_at(remnawave_store, 94_601)) == truncate_to_ms(
        new.expire_at
    )
    assert await _events(session, before.id) == []


async def test_plan_change_during_the_grace_end_push_gets_the_new_rows_config(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subscription, _ = await _grace_ended(
        subscription_repository,
        user_repository,
        settings_repository,
        plan_repository,
        remnawave_store,
        94_602,
    )
    real_update_user = remnawave_sdk.users.update_user
    replacing: list[SubscriptionDto] = []

    async def change_then_push(request: Any) -> Any:
        if not replacing:
            replacing.append(
                await _replace_by_plan_change(
                    uow, subscription_repository, user_repository, subscription, 94_612
                )
            )
        return await real_update_user(request)

    monkeypatch.setattr(remnawave_sdk.users, "update_user", change_then_push)

    await grace.end.system(subscription.id)

    assert remnawave_store[94_602]["tag"] == "NEWPLAN"
    assert remnawave_store[94_602]["status"] == "ACTIVE"
    assert await _events(session, subscription.id) == []


async def test_grace_push_refused_on_the_expiry_webhook_sends_the_expiry_notice(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    before = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_701)

    async def _refused(_request: Any) -> Any:
        raise _panel_error(400)

    monkeypatch.setattr(remnawave_sdk.users, "update_user", _refused)

    await grace.webhook.system(
        _webhook(config, "user.expired", 94_701, "EXPIRED", before.expire_at)
    )
    await grace.sweep.system()
    await grace.webhook.system(
        _webhook(config, "user.expired", 94_701, "EXPIRED", before.expire_at, onlineAt=None)
    )

    assert (await subscription_repository.get_by_id(before.id)).grace_until is None
    assert remnawave_store[94_701]["status"] == "EXPIRED"
    assert await _events(session, before.id) == ["SubscriptionExpiredEvent"]


async def test_grace_push_refused_on_the_sweep_sends_the_expiry_notice(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    before = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_702)

    async def _refused(_request: Any) -> Any:
        raise _panel_error(400)

    monkeypatch.setattr(remnawave_sdk.users, "update_user", _refused)

    await grace.sweep.system()
    await grace.sweep.system()

    assert (await subscription_repository.get_by_id(before.id)).grace_until is None
    assert await _events(session, before.id) == ["SubscriptionExpiredEvent"]


@pytest.mark.parametrize(
    ("status", "panel_id"),
    [(SubscriptionStatus.ACTIVE, 94_801), (SubscriptionStatus.LIMITED, 94_802)],
    ids=["active", "limited"],
)
async def test_sweep_enters_grace_for_a_row_whose_expiry_webhook_never_came(
    status: SubscriptionStatus,
    panel_id: int,
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    before = await _expired(
        subscription_repository, user_repository, plan, remnawave_store, panel_id
    )
    # the row never heard of the expiry
    await session.execute(
        update(Subscription).where(Subscription.id == before.id).values(status=status)
    )

    await grace.sweep.system()

    after = await subscription_repository.get_by_id(before.id)
    assert after.status is SubscriptionStatus.EXPIRED
    assert after.grace_until is not None
    assert remnawave_store[panel_id]["tag"] == "GRACE"
    assert await _events(session, before.id) == ["SubscriptionGraceStartedEvent"]
    synced = await session.scalars(
        select(EventOutboxEntry.payload)
        .where(EventOutboxEntry.payload["subscription_id"].as_integer() == before.id)
        .where(EventOutboxEntry.event_type == "SubscriptionStatusSyncedEvent")
    )
    assert [(row["old_status"], row["new_status"], row["source"]) for row in synced.all()] == [
        (status.value, "EXPIRED", "grace_sweep")
    ]


async def test_panel_disable_during_grace_reaches_the_row(
    grace: _Grace,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
    config: AppConfig,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    before = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_901)
    assert await grace.enter.system(before.id)
    grace_until = (await subscription_repository.get_by_id(before.id)).grace_until
    assert grace_until is not None

    await grace.webhook.system(_webhook(config, "user.disabled", 94_901, "DISABLED", grace_until))

    disabled = await subscription_repository.get_by_id(before.id)
    assert disabled.status is SubscriptionStatus.DISABLED
    assert disabled.grace_until == grace_until

    await grace.webhook.system(
        _webhook(config, "user.enabled", 94_901, "ACTIVE", grace_until, onlineAt=None)
    )

    enabled = await subscription_repository.get_by_id(before.id)
    assert enabled.status is SubscriptionStatus.EXPIRED
    assert enabled.grace_until == grace_until


async def _disable_grace(settings_repository: SettingsRepository) -> None:
    await settings_repository.update_grace_settings(
        UpdateGraceSettingsDto(enabled=False, fields_set=frozenset({"enabled"}))
    )


async def test_expiry_with_grace_off_skips_the_grace_path(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
    config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = await _paid_plan(plan_repository)
    before = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_951)

    async def _not_run(*_args: Any) -> Any:
        raise AssertionError("the grace path ran with grace off")

    repository = grace.webhook._subscription_repository
    real_find = repository.find_by_remnawave_user_id
    loads: list[int] = []

    async def _find(remnawave_user_id: int) -> Optional[SubscriptionDto]:
        loads.append(remnawave_user_id)
        return await real_find(remnawave_user_id)

    monkeypatch.setattr(repository, "find_by_remnawave_user_id", _find)
    monkeypatch.setattr(grace.enter, "_execute", _not_run)
    monkeypatch.setattr(grace.end, "_execute", _not_run)

    await grace.webhook.system(
        _webhook(config, "user.expired", 94_951, "EXPIRED", before.expire_at)
    )

    # the notice's own lookup only
    assert loads == [94_951]
    assert await _events(session, before.id) == ["SubscriptionExpiredEvent"]


async def test_grace_switched_off_mid_grace_still_ends_it_once(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
    config: AppConfig,
) -> None:
    subscription, ended_at = await _grace_ended(
        subscription_repository,
        user_repository,
        settings_repository,
        plan_repository,
        remnawave_store,
        94_952,
    )
    await _disable_grace(settings_repository)

    await grace.webhook.system(_webhook(config, "user.expired", 94_952, "EXPIRED", ended_at))
    await grace.webhook.system(
        _webhook(config, "user.expired", 94_952, "EXPIRED", ended_at, onlineAt=None)
    )

    assert (await subscription_repository.get_by_id(subscription.id)).grace_until is None
    assert remnawave_store[94_952]["tag"] == "PLAN"
    assert await _events(session, subscription.id) == ["SubscriptionExpiredEvent"]


async def test_grace_push_that_never_reached_the_panel_clears_claim(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    before = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_016)

    async def _unreachable(*_args: Any) -> Any:
        raise httpx.ConnectError("fake panel down")

    monkeypatch.setattr(remnawave_sdk.users, "update_user", _unreachable)
    monkeypatch.setattr(remnawave_sdk.users, "get_user_by_id", _unreachable)

    with pytest.raises(RetryableTaskError):
        await grace.enter.system(before.id)

    assert (await subscription_repository.get_by_id(before.id)).grace_until is None
    assert await _events(session, before.id) == []


async def test_grace_push_rejected_for_the_panel_token_keeps_grace_open(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    before = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_017)

    async def _forbidden(*_args: Any) -> Any:
        raise _panel_error(403)

    monkeypatch.setattr(remnawave_sdk.users, "update_user", _forbidden)
    monkeypatch.setattr(remnawave_sdk.users, "get_user_by_id", _forbidden)

    with pytest.raises(RetryableTaskError):
        await grace.enter.system(before.id)

    after = await subscription_repository.get_by_id(before.id)
    assert after.grace_ended_at is None
    assert await _events(session, before.id) == []


async def test_grace_end_empties_the_grace_squads_of_a_row_without_any(
    grace: _Grace,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
) -> None:
    subscription, _ = await _grace_ended(
        subscription_repository,
        user_repository,
        settings_repository,
        plan_repository,
        remnawave_store,
        94_401,
    )
    await subscription_repository.update_squads(
        UpdateSubscriptionSquadsDto(
            subscription_id=subscription.id, internal_squads=[], external_squad=None
        )
    )

    await grace.end.system(subscription.id)

    assert remnawave_store[94_401]["activeInternalSquads"] == []


async def test_renewal_during_the_grace_end_push_keeps_its_config(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subscription, _ = await _grace_ended(
        subscription_repository,
        user_repository,
        settings_repository,
        plan_repository,
        remnawave_store,
        94_402,
    )
    renewed_until = truncate_to_ms(datetime_now() + timedelta(days=30))
    lifecycle = SubscriptionLifecycleService(
        subscription_repository, CreditSegmentRepository(session)
    )
    real_update_user = remnawave_sdk.users.update_user
    calls = 0

    async def renewal_then_push(request: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            # a renewal commits and pushes its own config before the end's push lands
            async with uow:
                await lifecycle.sync_expire_at(subscription.id, renewed_until)
                await lifecycle.sync_status(subscription.id, SubscriptionStatus.ACTIVE)
                await subscription_repository.set_grace_until(subscription.id, None)
                await subscription_repository.set_tag(subscription.id, "RENEWED")
            remnawave_store[94_402].update(
                {"expireAt": renewed_until.isoformat(), "status": "ACTIVE", "tag": "RENEWED"}
            )
            stale = request.model_copy(update={"tag": "PLAN"})
            return await real_update_user(stale)
        return await real_update_user(request)

    monkeypatch.setattr(remnawave_sdk.users, "update_user", renewal_then_push)

    await grace.end.system(subscription.id)

    panel = remnawave_store[94_402]
    assert panel["tag"] == "RENEWED"
    assert truncate_to_ms(_panel_expire_at(remnawave_store, 94_402)) == renewed_until
    assert await _events(session, subscription.id) == []


async def test_grace_end_of_a_panel_user_deleted_without_a_webhook(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
) -> None:
    subscription, _ = await _grace_ended(
        subscription_repository,
        user_repository,
        settings_repository,
        plan_repository,
        remnawave_store,
        94_403,
    )
    del remnawave_store[94_403]

    await grace.end.system(subscription.id)

    assert (await subscription_repository.get_by_id(subscription.id)).grace_until is None
    assert await _events(session, subscription.id) == ["SubscriptionExpiredEvent"]


async def test_sweep_takes_a_panel_extension_instead_of_entering_grace(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    before = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_404)
    await session.execute(
        update(Subscription)
        .where(Subscription.id == before.id)
        .values(status=SubscriptionStatus.ACTIVE)
    )
    # extended in the panel, both webhooks lost
    extended_until = truncate_to_ms(datetime_now() + timedelta(days=30))
    remnawave_store[94_404].update({"expireAt": extended_until.isoformat(), "status": "ACTIVE"})

    await grace.sweep.system()

    after = await subscription_repository.get_by_id(before.id)
    assert after.grace_until is None
    assert after.status is SubscriptionStatus.ACTIVE
    assert truncate_to_ms(after.expire_at) == extended_until
    assert remnawave_store[94_404]["tag"] == "PLAN"
    assert await _events(session, before.id) == []


async def test_retry_after_an_unreadable_panel_finishes_the_entry_once(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
    redis: Redis,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    before = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_405)
    broken = RemnawaveSDK(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(500, json={"message": "panel down"})
            ),
            base_url="http://fake-panel.test/api",
        )
    )
    with pytest.raises(RetryableTaskError):
        await _Grace(session, broken, uow, event_publisher, redis).enter.system(before.id)

    assert await grace.enter.system(before.id) is True
    assert await grace.enter.system(before.id) is True

    after = await subscription_repository.get_by_id(before.id)
    assert after.grace_started_at is not None
    assert remnawave_store[94_405]["lastTrafficResetAt"] is not None
    assert remnawave_store[94_405]["tag"] == "GRACE"
    assert await _events(session, before.id) == ["SubscriptionGraceStartedEvent"]


async def test_sweep_finishes_an_interrupted_grace_entry(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    before = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_406)
    # the claim committed, the worker died before the push
    await subscription_repository.set_grace_until(before.id, datetime_now() + timedelta(days=3))

    await grace.sweep.system()
    await grace.sweep.system()

    assert (await subscription_repository.get_by_id(before.id)).grace_started_at is not None
    assert remnawave_store[94_406]["tag"] == "GRACE"
    assert remnawave_store[94_406]["lastTrafficResetAt"] is not None
    assert await _events(session, before.id) == ["SubscriptionGraceStartedEvent"]


async def test_finishing_an_entry_keeps_an_admins_switch_off(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    before = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_407)
    await subscription_repository.set_grace_until(before.id, datetime_now() + timedelta(days=3))
    # switched off by an admin before the interrupted entry is finished
    await session.execute(
        update(Subscription)
        .where(Subscription.id == before.id)
        .values(status=SubscriptionStatus.DISABLED)
    )
    remnawave_store[94_407]["status"] = "DISABLED"

    await grace.sweep.system()

    assert remnawave_store[94_407]["status"] == "DISABLED"
    assert (await subscription_repository.get_by_id(before.id)).grace_started_at is not None
    assert await _events(session, before.id) == []


async def test_repeat_grace_push_to_a_deleted_panel_user_gives_grace_up(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    before = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_408)
    await subscription_repository.set_grace_until(before.id, datetime_now() + timedelta(days=3))
    del remnawave_store[94_408]

    await grace.enter.system(before.id)

    assert (await subscription_repository.get_by_id(before.id)).grace_until is None
    assert await _events(session, before.id) == ["SubscriptionExpiredEvent"]


async def test_failed_stale_grace_end_correction_is_pushed_again_by_the_cron(
    grace: _Grace,
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subscription, _ = await _grace_ended(
        subscription_repository,
        user_repository,
        settings_repository,
        plan_repository,
        remnawave_store,
        94_409,
    )
    real_update_user = remnawave_sdk.users.update_user
    calls = 0

    async def renewal_then_down(request: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            async with uow:
                await subscription_repository.set_grace_until(subscription.id, None)
            return await real_update_user(request)
        raise _panel_error(500)

    monkeypatch.setattr(remnawave_sdk.users, "update_user", renewal_then_down)

    await grace.end.system(subscription.id)

    assert calls == 2

    marked = await session.scalar(
        select(Subscription.panel_restore_due_at).where(Subscription.id == subscription.id)
    )
    assert marked is not None
    monkeypatch.setattr(remnawave_sdk.users, "update_user", real_update_user)
    remnawave_store[94_409]["tag"] = "STALE"

    await grace.restore.system()

    assert remnawave_store[94_409]["tag"] == "PLAN"
    after = await subscription_repository.get_by_id(subscription.id)
    assert after.panel_restore_due_at is None


async def test_restore_cron_pushes_grace_config_and_keeps_a_failed_mark(
    grace: _Grace,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    subscription = await _expired(
        subscription_repository, user_repository, plan, remnawave_store, 94_410
    )
    await grace.enter.system(subscription.id)
    remnawave_store[94_410]["tag"] = "PLAN"
    async with uow:
        await subscription_repository.mark_panel_restore_due(subscription.id, datetime_now())
    real_update_user = remnawave_sdk.users.update_user

    async def down(request: Any) -> Any:
        raise _panel_error(500)

    monkeypatch.setattr(remnawave_sdk.users, "update_user", down)
    await grace.restore.system()

    assert (await subscription_repository.get_by_id(subscription.id)).panel_restore_due_at
    monkeypatch.setattr(remnawave_sdk.users, "update_user", real_update_user)

    await grace.restore.system()

    assert remnawave_store[94_410]["tag"] == "GRACE"
    after = await subscription_repository.get_by_id(subscription.id)
    assert after.panel_restore_due_at is None


async def test_restore_cron_drops_a_mark_the_panel_refuses(
    grace: _Grace,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    subscription = await _expired(
        subscription_repository, user_repository, plan, remnawave_store, 94_411
    )
    async with uow:
        await subscription_repository.mark_panel_restore_due(subscription.id, datetime_now())
    # deleted in the panel, its webhook lost
    del remnawave_store[94_411]

    await grace.restore.system()

    after = await subscription_repository.get_by_id(subscription.id)
    assert after.panel_restore_due_at is None


async def test_restore_cron_goes_on_past_a_row_that_fails(
    grace: _Grace,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_grace(settings_repository)
    plan = await _paid_plan(plan_repository)
    broken = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_412)
    fine = await _expired(subscription_repository, user_repository, plan, remnawave_store, 94_413)
    async with uow:
        await subscription_repository.mark_panel_restore_due(broken.id, datetime_now())
        await subscription_repository.mark_panel_restore_due(fine.id, datetime_now())
    real_find = subscription_repository.find_by_remnawave_user_id_for_update

    async def lock_fails_for_one(remnawave_user_id: int) -> Any:
        if remnawave_user_id == 94_412:
            raise RuntimeError("deadlock detected")
        return await real_find(remnawave_user_id)

    monkeypatch.setattr(
        subscription_repository, "find_by_remnawave_user_id_for_update", lock_fails_for_one
    )

    await grace.restore.system()

    assert (await subscription_repository.get_by_id(fine.id)).panel_restore_due_at is None
