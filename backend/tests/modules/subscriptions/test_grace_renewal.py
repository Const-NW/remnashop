from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Final, Optional
from uuid import UUID, uuid4

import httpx
import pytest
from remnapy import RemnawaveSDK
from remnapy.enums import TrafficLimitStrategy
from remnapy.models import UpdateUserRequestDto, UserResponseDto
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import AppConfig
from app.core.constants import GB_TO_BYTES, MB_TO_BYTES
from app.core.db.uow import UnitOfWork
from app.core.enums import (
    AddOnType,
    Currency,
    Locale,
    PlanType,
    PurchaseType,
    Role,
    SubscriptionStatus,
)
from app.core.error_codes import ErrorCode
from app.core.ports import EventPublisher
from app.core.utils.time import datetime_now, truncate_to_ms
from app.modules.plans.addon_repository import AddOnRepository
from app.modules.plans.dto import (
    CreateAddOnDto,
    CreatePlanDto,
    PlanDto,
    SetPlanDurationDto,
    SetPlanDurationsDto,
    UpdatePlanDto,
    UpdatePlanSquadsDto,
)
from app.modules.plans.repository import PlanRepository
from app.modules.pricing.repository import PricingRepository
from app.modules.settings import SettingsRepository
from app.modules.settings.dto import UpdateGraceSettingsDto
from app.modules.subscriptions.credit import remaining_term_credit
from app.modules.subscriptions.credit_segment_repository import CreditSegmentRepository
from app.modules.subscriptions.domain import OrderNotPaidError
from app.modules.subscriptions.dto import (
    GrantAddOnDto,
    PurchaseSubscriptionDto,
    SubscriptionDto,
    UpdateSubscriptionDto,
    UpdateSubscriptionSquadsDto,
)
from app.modules.subscriptions.grace import grace_entry_allowed
from app.modules.subscriptions.idempotency_repository import PurchaseIdempotencyRepository
from app.modules.subscriptions.lifecycle import SubscriptionLifecycleService
from app.modules.subscriptions.panel_sync import from_panel
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.subscriptions.unlimited import expiry_after
from app.modules.subscriptions.usecases import (
    EnterGraceUseCase,
    GrantAddOnUseCase,
    PurchaseSubscriptionUseCase,
    SyncSubscriptionStatusUseCase,
    UpdateSubscriptionSquadsUseCase,
    UpdateSubscriptionUseCase,
)
from app.modules.users.dto import UserDto
from app.modules.users.repository import UserRepository
from tests.fakes.remnawave import FakeUserStore, _user_payload
from tests.modules.subscriptions.factories import (
    create_subscription,
    make_eligibility,
    snapshot_from_plan,
)

OWNER: Final[UserDto] = UserDto(login="owner", name="owner", role=Role.OWNER, language=Locale.EN)
_GRACE_SQUAD: Final[UUID] = uuid4()
_PLAN_SQUAD: Final[UUID] = uuid4()
_NEW_PLAN_SQUAD: Final[UUID] = uuid4()


async def _plan(plan_repository: PlanRepository, squad: Optional[UUID], tag: str) -> PlanDto:
    plan = await plan_repository.create(
        CreatePlanDto(
            public_code=f"gr{uuid4().hex[:6]}",
            name=f"gr{uuid4().hex[:6]}",
            type=PlanType.BOTH,
            traffic_limit=100,
            device_limit=3,
            tag=tag,
        )
    )
    await plan_repository.set_durations(
        SetPlanDurationsDto(
            plan_id=plan.id,
            durations=[SetPlanDurationDto(days=30, order_index=0, base_price=Decimal("299.00"))],
        )
    )
    await plan_repository.update_squads(
        UpdatePlanSquadsDto(
            plan_id=plan.id, internal_squads=[squad] if squad else [], external_squad=None
        )
    )
    return await plan_repository.update(UpdatePlanDto(plan_id=plan.id, is_active=True))


async def _in_grace(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    store: FakeUserStore,
    plan: PlanDto,
    panel_id: int,
    *,
    expired_ago: timedelta = timedelta(days=1),
    grace_until: Optional[datetime] = None,
) -> SubscriptionDto:
    """An expired subscription whose panel user runs on the grace config."""
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
    snapshot = snapshot_from_plan(plan, price_paid=Decimal("299.00"), tag=plan.tag)
    subscription = await create_subscription(
        subscription_repository,
        user_repository,
        telegram_id=panel_id,
        remnawave_user_id=panel_id,
        status=SubscriptionStatus.EXPIRED,
        expire_at=truncate_to_ms(datetime_now() - expired_ago),
        **{
            name: getattr(snapshot, name)
            for name in ("plan_id", "public_code", "name", "price_paid", "internal_squads", "tag")
        },
    )
    await subscription_repository.update_squads(
        UpdateSubscriptionSquadsDto(
            subscription_id=subscription.id, internal_squads=[_PLAN_SQUAD], external_squad=None
        )
    )
    await subscription_repository.set_tag(subscription.id, plan.tag)
    grace_until = truncate_to_ms(grace_until or datetime_now() + timedelta(days=2))
    payload = _user_payload(
        user_id=panel_id,
        username=f"rs_{panel_id}",
        expire_at=grace_until,
        traffic_limit_bytes=512 * MB_TO_BYTES,
        traffic_limit_strategy="DAY",
        hwid_device_limit=3,
        active_internal_squads=[str(_GRACE_SQUAD)],
    )
    payload["tag"] = "GRACE"
    store[panel_id] = payload
    return await subscription_repository.set_grace_until(subscription.id, grace_until)


def _purchase(
    subscription_repository: SubscriptionRepository,
    remnawave_sdk: RemnawaveSDK,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
    lifecycle: Optional[SubscriptionLifecycleService] = None,
) -> PurchaseSubscriptionUseCase:
    session = subscription_repository.session
    return PurchaseSubscriptionUseCase(
        subscription_repository,
        make_eligibility(session),
        remnawave_sdk,
        uow,
        event_publisher,
        lifecycle
        or SubscriptionLifecycleService(subscription_repository, CreditSegmentRepository(session)),
        PricingRepository(session),
        PurchaseIdempotencyRepository(session),
        UserRepository(session),
        AppConfig.get(),
        SettingsRepository(session),
    )


def _update(
    subscription_repository: SubscriptionRepository,
    remnawave_sdk: RemnawaveSDK,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
) -> UpdateSubscriptionUseCase:
    session = subscription_repository.session
    return UpdateSubscriptionUseCase(
        subscription_repository,
        UserRepository(session),
        remnawave_sdk,
        event_publisher,
        uow,
        SubscriptionLifecycleService(subscription_repository, CreditSegmentRepository(session)),
        SettingsRepository(session),
    )


def _paid_renewal(
    target: SubscriptionDto, plan: PlanDto, *, key: Optional[str] = None
) -> PurchaseSubscriptionDto:
    """As the fulfillment task builds a customer's order."""
    return PurchaseSubscriptionDto(
        user_id=target.user_id,
        plan_snapshot=snapshot_from_plan(
            plan, price_paid=Decimal("299.00"), duration_days=30, tag=plan.tag
        ),
        duration_days=30,
        price_paid=Decimal("299.00"),
        currency=Currency.RUB,
        purchase_type=PurchaseType.RENEW,
        target_subscription_id=target.id,
        idempotency_key=key or uuid4().hex,
        reset_traffic=True,
        apply_current_plan=True,
        refuse_disabled_target=False,
    )


async def _buyer(user_repository: UserRepository, target: SubscriptionDto) -> UserDto:
    buyer = await user_repository.find_by_id(target.user_id)
    assert buyer is not None
    return buyer


def _panel_expire_at(store: FakeUserStore, panel_id: int) -> datetime:
    return datetime.fromisoformat(str(store[panel_id]["expireAt"]).replace("Z", "+00:00"))


def _assert_plan_config(store: FakeUserStore, panel_id: int, squad: UUID, tag: str) -> None:
    panel = store[panel_id]
    assert [s["uuid"] for s in panel["activeInternalSquads"]] == [str(squad)]
    assert panel["trafficLimitBytes"] == 100 * GB_TO_BYTES
    assert panel["tag"] == tag


async def test_paid_renewal_in_grace_counts_from_original_expiry(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
) -> None:
    plan = await _plan(plan_repository, _PLAN_SQUAD, "PLAN")
    target = await _in_grace(
        subscription_repository,
        user_repository,
        settings_repository,
        remnawave_store,
        plan,
        95_001,
    )
    use_case = _purchase(subscription_repository, remnawave_sdk, uow, event_publisher)

    renewed = await use_case(await _buyer(user_repository, target), _paid_renewal(target, plan))

    expected = truncate_to_ms(expiry_after(target.expire_at, 30, exact_days=False))
    assert truncate_to_ms(renewed.expire_at) == expected
    assert renewed.status is SubscriptionStatus.ACTIVE
    assert renewed.grace_until is None
    assert truncate_to_ms(_panel_expire_at(remnawave_store, 95_001)) == expected
    _assert_plan_config(remnawave_store, 95_001, _PLAN_SQUAD, "PLAN")


async def test_long_grace_renewal_counts_from_now(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
) -> None:
    plan = await _plan(plan_repository, _PLAN_SQUAD, "PLAN")
    target = await _in_grace(
        subscription_repository,
        user_repository,
        settings_repository,
        remnawave_store,
        plan,
        95_002,
        expired_ago=timedelta(days=40),
    )
    use_case = _purchase(subscription_repository, remnawave_sdk, uow, event_publisher)

    before = datetime_now()
    renewed = await use_case(await _buyer(user_repository, target), _paid_renewal(target, plan))

    assert expiry_after(before, 30, exact_days=False) <= renewed.expire_at
    assert renewed.expire_at <= expiry_after(datetime_now(), 30, exact_days=False)
    assert renewed.grace_until is None
    _assert_plan_config(remnawave_store, 95_002, _PLAN_SQUAD, "PLAN")


async def test_pinned_retry_in_grace_still_pushes(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = await _plan(plan_repository, _PLAN_SQUAD, "PLAN")
    # unlimited grace: the panel's date lies past any pinned renewal
    target = await _in_grace(
        subscription_repository,
        user_repository,
        settings_repository,
        remnawave_store,
        plan,
        95_003,
        grace_until=expiry_after(datetime_now(), 0, exact_days=True),
    )
    use_case = _purchase(subscription_repository, remnawave_sdk, uow, event_publisher)
    buyer = await _buyer(user_repository, target)
    renewal = _paid_renewal(target, plan)
    real_update = remnawave_sdk.users.update_user

    async def _refused(*_args: object, **_kwargs: object) -> object:
        raise httpx.ConnectError("panel unreachable")

    monkeypatch.setattr(remnawave_sdk.users, "update_user", _refused)
    with pytest.raises(httpx.ConnectError):
        await use_case(buyer, renewal)
    monkeypatch.setattr(remnawave_sdk.users, "update_user", real_update)

    renewed = await use_case(buyer, renewal)

    expected = truncate_to_ms(expiry_after(target.expire_at, 30, exact_days=False))
    assert truncate_to_ms(renewed.expire_at) == expected
    assert truncate_to_ms(_panel_expire_at(remnawave_store, 95_003)) == expected
    assert renewed.grace_until is None
    _assert_plan_config(remnawave_store, 95_003, _PLAN_SQUAD, "PLAN")


@pytest.mark.parametrize("keyed", [True, False], ids=["bonus-days", "promo-days"])
async def test_bonus_days_in_grace_count_from_now_and_exit(
    keyed: bool,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
) -> None:
    panel_id = 95_004 if keyed else 95_005
    plan = await _plan(plan_repository, _PLAN_SQUAD, "PLAN")
    target = await _in_grace(
        subscription_repository,
        user_repository,
        settings_repository,
        remnawave_store,
        plan,
        panel_id,
    )
    use_case = _purchase(subscription_repository, remnawave_sdk, uow, event_publisher)

    before = datetime_now()
    # as RedeemBonusDaysUseCase (keyed) and a promocode (keyless) build it
    renewed = await use_case(
        await _buyer(user_repository, target),
        PurchaseSubscriptionDto(
            user_id=target.user_id,
            plan_snapshot=target.plan_snapshot,
            duration_days=7,
            price_paid=Decimal("0"),
            currency=Currency.USD,
            purchase_type=PurchaseType.RENEW,
            target_subscription_id=target.id,
            idempotency_key=uuid4().hex if keyed else None,
            exact_days=True,
        ),
    )

    assert before + timedelta(days=7) <= renewed.expire_at
    assert renewed.expire_at <= datetime_now() + timedelta(days=7)
    assert renewed.status is SubscriptionStatus.ACTIVE
    assert renewed.grace_until is None
    # absolute: the panel's extend would have added the days to the grace end
    assert truncate_to_ms(_panel_expire_at(remnawave_store, panel_id)) == truncate_to_ms(
        renewed.expire_at
    )
    # the row's own config, not the grace one
    _assert_plan_config(remnawave_store, panel_id, _PLAN_SQUAD, "PLAN")


async def test_refunded_renewal_in_grace_puts_the_grace_config_back(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
) -> None:
    plan = await _plan(plan_repository, _PLAN_SQUAD, "PLAN")
    target = await _in_grace(
        subscription_repository,
        user_repository,
        settings_repository,
        remnawave_store,
        plan,
        95_006,
    )
    session = subscription_repository.session
    lifecycle = SubscriptionLifecycleService(
        subscription_repository, CreditSegmentRepository(session)
    )

    async def _refunded(*_args: object, **_kwargs: object) -> SubscriptionDto:
        raise OrderNotPaidError("refunded", code=ErrorCode.PAYMENT_ORDER_NOT_FULFILLABLE)

    lifecycle.renew = _refunded  # type: ignore[method-assign]
    use_case = _purchase(subscription_repository, remnawave_sdk, uow, event_publisher, lifecycle)

    with pytest.raises(OrderNotPaidError):
        await use_case(await _buyer(user_repository, target), _paid_renewal(target, plan))

    after = await subscription_repository.get_by_id(target.id)
    assert after.grace_until == target.grace_until
    panel = remnawave_store[95_006]
    assert panel["status"] == "ACTIVE"
    assert truncate_to_ms(_panel_expire_at(remnawave_store, 95_006)) == target.grace_until
    assert panel["trafficLimitBytes"] == 512 * MB_TO_BYTES
    assert panel["tag"] == "GRACE"


async def test_plan_change_in_grace_starts_a_row_without_grace(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
) -> None:
    plan = await _plan(plan_repository, _PLAN_SQUAD, "PLAN")
    new_plan = await _plan(plan_repository, _NEW_PLAN_SQUAD, "NEWPLAN")
    target = await _in_grace(
        subscription_repository,
        user_repository,
        settings_repository,
        remnawave_store,
        plan,
        95_007,
    )
    use_case = _purchase(subscription_repository, remnawave_sdk, uow, event_publisher)

    changed = await use_case(
        await _buyer(user_repository, target),
        PurchaseSubscriptionDto(
            user_id=target.user_id,
            plan_snapshot=snapshot_from_plan(
                new_plan, price_paid=Decimal("299.00"), duration_days=30, tag=new_plan.tag
            ),
            duration_days=30,
            price_paid=Decimal("299.00"),
            currency=Currency.RUB,
            purchase_type=PurchaseType.CHANGE,
            target_subscription_id=target.id,
            idempotency_key=uuid4().hex,
        ),
    )

    assert changed.id != target.id
    assert changed.grace_until is None
    assert changed.status is SubscriptionStatus.ACTIVE
    assert changed.expire_at > datetime_now() + timedelta(days=29)
    assert truncate_to_ms(_panel_expire_at(remnawave_store, 95_007)) == truncate_to_ms(
        changed.expire_at
    )
    _assert_plan_config(remnawave_store, 95_007, _NEW_PLAN_SQUAD, "NEWPLAN")


async def test_admin_expire_edit_exits_grace(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
) -> None:
    plan = await _plan(plan_repository, _PLAN_SQUAD, "PLAN")
    target = await _in_grace(
        subscription_repository,
        user_repository,
        settings_repository,
        remnawave_store,
        plan,
        95_008,
    )
    use_case = _update(subscription_repository, remnawave_sdk, uow, event_publisher)
    new_end = truncate_to_ms(datetime_now() + timedelta(days=10))

    updated = await use_case(
        OWNER, UpdateSubscriptionDto(subscription_id=target.id, expire_at=new_end)
    )

    assert updated.grace_until is None
    assert updated.status is SubscriptionStatus.ACTIVE
    assert truncate_to_ms(updated.expire_at) == new_end
    assert truncate_to_ms(_panel_expire_at(remnawave_store, 95_008)) == new_end
    _assert_plan_config(remnawave_store, 95_008, _PLAN_SQUAD, "PLAN")
    assert remnawave_store[95_008]["trafficLimitStrategy"] == target.traffic_limit_strategy


async def test_failed_admin_expire_edit_in_grace_puts_the_grace_config_back(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = await _plan(plan_repository, _PLAN_SQUAD, "PLAN")
    target = await _in_grace(
        subscription_repository,
        user_repository,
        settings_repository,
        remnawave_store,
        plan,
        95_009,
    )
    use_case = _update(subscription_repository, remnawave_sdk, uow, event_publisher)

    async def _fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("DB write failed after the panel call landed")

    monkeypatch.setattr(subscription_repository, "update", _fail)
    with pytest.raises(RuntimeError):
        await use_case(
            OWNER,
            UpdateSubscriptionDto(
                subscription_id=target.id, expire_at=datetime_now() + timedelta(days=10)
            ),
        )

    panel = remnawave_store[95_009]
    assert truncate_to_ms(_panel_expire_at(remnawave_store, 95_009)) == target.grace_until
    assert panel["trafficLimitBytes"] == 512 * MB_TO_BYTES
    assert panel["tag"] == "GRACE"


async def test_admin_traffic_edit_in_grace_leaves_panel(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = await _plan(plan_repository, _PLAN_SQUAD, "PLAN")
    target = await _in_grace(
        subscription_repository,
        user_repository,
        settings_repository,
        remnawave_store,
        plan,
        95_010,
    )
    use_case = _update(subscription_repository, remnawave_sdk, uow, event_publisher)
    calls: list[object] = []

    async def _record(*args: object, **_kwargs: object) -> None:
        calls.append(args)

    monkeypatch.setattr(remnawave_sdk.users, "update_user", _record)

    updated = await use_case(
        OWNER,
        UpdateSubscriptionDto(subscription_id=target.id, traffic_limit=200, tag="VIP"),
    )

    assert calls == []
    assert updated.traffic_limit == 200
    assert updated.tag == "VIP"
    assert updated.grace_until == target.grace_until
    assert remnawave_store[95_010]["trafficLimitBytes"] == 512 * MB_TO_BYTES


async def test_renewal_echo_in_grace_keeps_add_ons_and_the_plan_config(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = await _plan(plan_repository, _PLAN_SQUAD, "PLAN")
    target = await _in_grace(
        subscription_repository,
        user_repository,
        settings_repository,
        remnawave_store,
        plan,
        95_011,
    )
    # a 10 GB add-on above the 512 MB grace limit
    async with uow:
        await subscription_repository.grant_entitlement(
            subscription_id=target.id, addon_id=None, addon_type=AddOnType.TRAFFIC, amount=10
        )
    session = subscription_repository.session
    sync = SyncSubscriptionStatusUseCase(
        subscription_repository,
        uow,
        event_publisher,
        SubscriptionLifecycleService(subscription_repository, CreditSegmentRepository(session)),
        remnawave_sdk,
        SettingsRepository(subscription_repository.session),
    )
    real_update = remnawave_sdk.users.update_user
    echoed: list[UserResponseDto] = []

    async def _update_then_echo(request: UpdateUserRequestDto) -> UserResponseDto:
        result = await real_update(request)
        # the panel's webhook for the expiry lands before the renewal is recorded
        if request.expire_at is not None and not echoed:
            echoed.append(result)
            await sync.system(from_panel(result, target.remnawave_user_id, "webhook"))
        return result

    monkeypatch.setattr(remnawave_sdk.users, "update_user", _update_then_echo)
    use_case = _purchase(subscription_repository, remnawave_sdk, uow, event_publisher)

    renewed = await use_case(await _buyer(user_repository, target), _paid_renewal(target, plan))

    assert echoed
    assert renewed.grace_until is None
    assert [e.type for e in renewed.addon_entitlements] == [AddOnType.TRAFFIC]
    assert (renewed.traffic_limit, renewed.effective_traffic_limit) == (100, 110)
    assert renewed.internal_squads == [_PLAN_SQUAD]
    assert renewed.tag == "PLAN"
    assert remnawave_store[95_011]["trafficLimitBytes"] == 110 * GB_TO_BYTES


async def test_paid_renewal_in_grace_credits_the_whole_term(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
) -> None:
    plan = await _plan(plan_repository, _PLAN_SQUAD, "PLAN")
    target = await _in_grace(
        subscription_repository,
        user_repository,
        settings_repository,
        remnawave_store,
        plan,
        95_012,
        expired_ago=timedelta(days=10),
        grace_until=datetime_now() + timedelta(days=5),
    )
    session = subscription_repository.session
    lifecycle = SubscriptionLifecycleService(
        subscription_repository, CreditSegmentRepository(session)
    )
    use_case = _purchase(subscription_repository, remnawave_sdk, uow, event_publisher, lifecycle)

    renewed = await use_case(await _buyer(user_repository, target), _paid_renewal(target, plan))

    # a plan change a day later: the 30 paid days counted from the real expiry, 10
    # of them lapsed in grace and one more used
    now = datetime_now() + timedelta(days=1)
    async with uow:
        segments = await lifecycle.credit_segments(renewed.id)
    credit = remaining_term_credit(
        segments, now=now, expire_at=renewed.expire_at, convert=lambda amount, _: amount
    )
    term = renewed.expire_at - target.expire_at
    expected = Decimal("299.00") * Decimal((renewed.expire_at - now) / term)
    assert abs(credit - expected) < Decimal("0.5")


async def test_renewal_in_grace_to_a_plan_without_squads_clears_the_grace_squads(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
) -> None:
    plan = await _plan(plan_repository, None, "PLAN")
    target = await _in_grace(
        subscription_repository,
        user_repository,
        settings_repository,
        remnawave_store,
        plan,
        95_013,
    )
    # sold on the squadless plan
    target = await subscription_repository.update_squads(
        UpdateSubscriptionSquadsDto(
            subscription_id=target.id, internal_squads=[], external_squad=None
        )
    )
    use_case = _purchase(subscription_repository, remnawave_sdk, uow, event_publisher)

    renewed = await use_case(await _buyer(user_repository, target), _paid_renewal(target, plan))

    assert renewed.grace_until is None
    assert renewed.internal_squads == []
    assert remnawave_store[95_013]["activeInternalSquads"] == []


async def test_grace_entered_mid_renewal_leaves_the_panel_on_the_plan_config(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = await _plan(plan_repository, _PLAN_SQUAD, "PLAN")
    graced = await _in_grace(
        subscription_repository,
        user_repository,
        settings_repository,
        remnawave_store,
        plan,
        95_020,
        expired_ago=timedelta(minutes=5),
    )
    target = await subscription_repository.set_grace_until(graced.id, None)
    remnawave_store[95_020].update(
        status="EXPIRED",
        expireAt=target.expire_at.isoformat(),
        tag="PLAN",
        activeInternalSquads=[{"uuid": str(_PLAN_SQUAD), "name": "plan"}],
        trafficLimitBytes=100 * GB_TO_BYTES,
    )
    session = subscription_repository.session
    enter = EnterGraceUseCase(
        subscription_repository,
        plan_repository,
        settings_repository,
        UserRepository(session),
        SubscriptionLifecycleService(subscription_repository, CreditSegmentRepository(session)),
        remnawave_sdk,
        uow,
        event_publisher,
    )
    real_get_user_by_id = remnawave_sdk.users.get_user_by_id

    async def grace_first(user_id: int) -> UserResponseDto:
        # the expiry webhook's grace entry runs while the renewal reads the panel
        assert await enter.system(target.id)
        return await real_get_user_by_id(user_id)

    monkeypatch.setattr(remnawave_sdk.users, "get_user_by_id", grace_first)
    use_case = _purchase(subscription_repository, remnawave_sdk, uow, event_publisher)

    renewed = await use_case(await _buyer(user_repository, target), _paid_renewal(target, plan))

    assert renewed.grace_until is None
    assert renewed.status is SubscriptionStatus.ACTIVE
    assert truncate_to_ms(_panel_expire_at(remnawave_store, 95_020)) == truncate_to_ms(
        renewed.expire_at
    )
    _assert_plan_config(remnawave_store, 95_020, _PLAN_SQUAD, "PLAN")


async def test_renewal_in_grace_keeps_the_rows_own_limits_on_unchanged_terms(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
) -> None:
    plan = await _plan(plan_repository, _PLAN_SQUAD, "PLAN")
    graced = await _in_grace(
        subscription_repository,
        user_repository,
        settings_repository,
        remnawave_store,
        plan,
        95_021,
    )
    # an admin's 10 devices on a plan of 3
    target = await subscription_repository.update(
        UpdateSubscriptionDto(subscription_id=graced.id, device_limit=10)
    )
    renewal = _paid_renewal(target, plan)
    assert not target.plan_snapshot.terms_differ(renewal.plan_snapshot)
    use_case = _purchase(subscription_repository, remnawave_sdk, uow, event_publisher)

    renewed = await use_case(await _buyer(user_repository, target), renewal)

    assert renewed.grace_until is None
    assert renewed.device_limit == 10
    assert remnawave_store[95_021]["hwidDeviceLimit"] == 10
    _assert_plan_config(remnawave_store, 95_021, _PLAN_SQUAD, "PLAN")


async def test_renewal_in_grace_moves_to_terms_changed_since_the_sale(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
) -> None:
    plan = await _plan(plan_repository, _PLAN_SQUAD, "PLAN")
    graced = await _in_grace(
        subscription_repository,
        user_repository,
        settings_repository,
        remnawave_store,
        plan,
        95_022,
    )
    target = await subscription_repository.update(
        UpdateSubscriptionDto(subscription_id=graced.id, device_limit=10)
    )
    renewal = _paid_renewal(target, plan)
    renewal = replace(renewal, plan_snapshot=replace(renewal.plan_snapshot, traffic_limit=200))
    use_case = _purchase(subscription_repository, remnawave_sdk, uow, event_publisher)

    renewed = await use_case(await _buyer(user_repository, target), renewal)

    assert (renewed.traffic_limit, renewed.device_limit) == (200, 3)
    assert remnawave_store[95_022]["trafficLimitBytes"] == 200 * GB_TO_BYTES
    assert remnawave_store[95_022]["hwidDeviceLimit"] == 3
    assert remnawave_store[95_022]["tag"] == "PLAN"


async def test_repeat_grace_push_during_a_renewal_in_grace_is_undone(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = await _plan(plan_repository, _PLAN_SQUAD, "PLAN")
    target = await _in_grace(
        subscription_repository,
        user_repository,
        settings_repository,
        remnawave_store,
        plan,
        95_023,
    )
    session = subscription_repository.session
    enter = EnterGraceUseCase(
        subscription_repository,
        plan_repository,
        settings_repository,
        UserRepository(session),
        SubscriptionLifecycleService(subscription_repository, CreditSegmentRepository(session)),
        remnawave_sdk,
        uow,
        event_publisher,
    )
    real_update = remnawave_sdk.users.update_user
    raced: list[bool] = []

    async def renewal_then_grace(request: UpdateUserRequestDto) -> UserResponseDto:
        result = await real_update(request)
        # a sweep's repeat grace entry lands after the renewal's push, before its commit
        if not raced:
            raced.append(False)
            raced[0] = await enter.system(target.id)
        return result

    monkeypatch.setattr(remnawave_sdk.users, "update_user", renewal_then_grace)
    use_case = _purchase(subscription_repository, remnawave_sdk, uow, event_publisher)

    renewed = await use_case(await _buyer(user_repository, target), _paid_renewal(target, plan))

    assert raced == [True]
    assert renewed.grace_until is None
    assert truncate_to_ms(_panel_expire_at(remnawave_store, 95_023)) == truncate_to_ms(
        renewed.expire_at
    )
    _assert_plan_config(remnawave_store, 95_023, _PLAN_SQUAD, "PLAN")


async def test_refund_restore_of_a_row_replaced_in_grace_brings_it_back_without_grace(
    session: AsyncSession,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_store: FakeUserStore,
) -> None:
    plan = await _plan(plan_repository, _PLAN_SQUAD, "PLAN")
    old = await _in_grace(
        subscription_repository,
        user_repository,
        settings_repository,
        remnawave_store,
        plan,
        95_901,
        expired_ago=timedelta(hours=1),
    )
    lifecycle = SubscriptionLifecycleService(
        subscription_repository, CreditSegmentRepository(session)
    )

    deleted = await subscription_repository.mark_deleted(old.id)
    restored = await lifecycle.restore_after_refund(old.id, datetime_now())

    assert deleted.grace_until is None
    assert restored.status is SubscriptionStatus.EXPIRED
    assert restored.grace_until is None
    # its grace was given: no second one for the same expiry
    settings = await settings_repository.get_grace_settings()
    assert grace_entry_allowed(restored, plan, settings, datetime_now()) is False


async def test_traffic_addon_in_grace_waits_in_the_row(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    addon_repository: AddOnRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
) -> None:
    plan = await _plan(plan_repository, _PLAN_SQUAD, "PLAN")
    target = await _in_grace(
        subscription_repository, user_repository, settings_repository, remnawave_store, plan, 95_101
    )
    addon = await addon_repository.create(
        CreateAddOnDto(name="t50", type=AddOnType.TRAFFIC, amount=50, base_price=Decimal("1"))
    )

    granted = await GrantAddOnUseCase(
        subscription_repository,
        addon_repository,
        user_repository,
        remnawave_sdk,
        uow,
        event_publisher,
    ).system(GrantAddOnDto(subscription_id=target.id, addon_id=addon.id))

    assert granted.effective_traffic_limit == target.effective_traffic_limit + 50
    assert remnawave_store[95_101]["trafficLimitBytes"] == 512 * MB_TO_BYTES


async def test_squad_edit_in_grace_waits_in_the_row(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
) -> None:
    plan = await _plan(plan_repository, _PLAN_SQUAD, "PLAN")
    target = await _in_grace(
        subscription_repository, user_repository, settings_repository, remnawave_store, plan, 95_102
    )

    edited = await UpdateSubscriptionSquadsUseCase(
        subscription_repository, user_repository, remnawave_sdk, event_publisher, uow
    )(
        OWNER,
        UpdateSubscriptionSquadsDto(
            subscription_id=target.id, internal_squads=[_NEW_PLAN_SQUAD], external_squad=None
        ),
    )

    assert edited.internal_squads == [_NEW_PLAN_SQUAD]
    assert [s["uuid"] for s in remnawave_store[95_102]["activeInternalSquads"]] == [
        str(_GRACE_SQUAD)
    ]


async def test_plan_change_in_grace_to_a_plan_without_squads_clears_the_grace_squads(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    settings_repository: SettingsRepository,
    plan_repository: PlanRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
) -> None:
    plan = await _plan(plan_repository, _PLAN_SQUAD, "PLAN")
    new_plan = await _plan(plan_repository, None, "NEWPLAN")
    target = await _in_grace(
        subscription_repository, user_repository, settings_repository, remnawave_store, plan, 95_103
    )

    await _purchase(subscription_repository, remnawave_sdk, uow, event_publisher)(
        await _buyer(user_repository, target),
        PurchaseSubscriptionDto(
            user_id=target.user_id,
            plan_snapshot=snapshot_from_plan(
                new_plan, price_paid=Decimal("299.00"), duration_days=30, tag=new_plan.tag
            ),
            duration_days=30,
            price_paid=Decimal("299.00"),
            currency=Currency.RUB,
            purchase_type=PurchaseType.CHANGE,
            target_subscription_id=target.id,
            idempotency_key=uuid4().hex,
        ),
    )

    assert remnawave_store[95_103]["activeInternalSquads"] == []
