from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import uuid4

from remnapy.enums import TrafficLimitStrategy, UserStatus

from app.core.enums import PlanType, SubscriptionStatus
from app.modules.plans.dto import (
    CreatePlanDto,
    PlanDto,
    PlanDurationDto,
    SetPlanDurationDto,
    SetPlanDurationsDto,
)
from app.modules.plans.repository import PlanRepository
from app.modules.settings.dto import GraceSettingsDto
from app.modules.subscriptions.dto import CreateSubscriptionDto
from app.modules.subscriptions.grace import (
    can_renew,
    grace_entry_allowed,
    grace_until_for,
)
from app.modules.subscriptions.panel import grace_panel_update
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.subscriptions.unlimited import is_unlimited_expiry
from app.modules.users.dto import CreateUserDto
from app.modules.users.repository import UserRepository
from tests.modules.subscriptions.factories import make_plan_snapshot, make_subscription_dto

_NOW = datetime(2030, 6, 1, tzinfo=timezone.utc)


def _plan(price: str = "100", **overrides: Any) -> PlanDto:
    defaults: dict[str, Any] = {
        "id": 1,
        "public_code": "basic",
        "name": "Basic",
        "type": PlanType.BOTH,
        "traffic_limit": 100,
        "device_limit": 3,
        "durations": [PlanDurationDto(id=1, days=30, base_price=Decimal(price))],
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(overrides)
    return PlanDto(**defaults)


def _expired(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "status": SubscriptionStatus.EXPIRED,
        "expire_at": _NOW - timedelta(hours=2),
    }
    defaults.update(overrides)
    return make_subscription_dto(**defaults)


def _settings(**overrides: Any) -> GraceSettingsDto:
    defaults: dict[str, Any] = {"enabled": True, "duration_days": 3, "id": 1}
    defaults.update(overrides)
    return GraceSettingsDto(created_at=_NOW, updated_at=_NOW, **defaults)


def test_can_renew_paid_plan() -> None:
    assert can_renew(make_subscription_dto(), _plan()) is True


def test_can_renew_refuses_what_renewal_cannot_sell() -> None:
    far = datetime(2099, 1, 1, tzinfo=timezone.utc)
    assert can_renew(make_subscription_dto(), None) is False
    assert can_renew(make_subscription_dto(is_trial=True), _plan()) is False
    assert can_renew(make_subscription_dto(expire_at=far), _plan()) is False
    assert can_renew(make_subscription_dto(), _plan(allow_renewal=False)) is False
    assert can_renew(make_subscription_dto(), _plan(price="0")) is False


def test_grace_entry_allowed_for_fresh_expired() -> None:
    assert grace_entry_allowed(_expired(), _plan(), _settings(), _NOW) is True


def test_grace_entry_allowed_for_active_past_expiry() -> None:
    # the expiry webhook was lost: the row still says ACTIVE
    lapsed = _expired(status=SubscriptionStatus.ACTIVE)
    assert grace_entry_allowed(lapsed, _plan(), _settings(), _NOW) is True


def test_grace_entry_allowed_for_limited_past_expiry() -> None:
    limited = _expired(status=SubscriptionStatus.LIMITED)
    assert grace_entry_allowed(limited, _plan(), _settings(), _NOW) is True


def test_grace_entry_refusals() -> None:
    plan = _plan()
    assert grace_entry_allowed(_expired(), plan, _settings(enabled=False), _NOW) is False
    old = _expired(expire_at=_NOW - timedelta(hours=25))
    assert grace_entry_allowed(old, plan, _settings(), _NOW) is False
    given = _expired(grace_until=_NOW + timedelta(days=1))
    assert grace_entry_allowed(given, plan, _settings(), _NOW) is False
    active = make_subscription_dto(expire_at=_NOW + timedelta(hours=2))
    assert grace_entry_allowed(active, plan, _settings(), _NOW) is False
    # traffic out, term still running: no grace
    limited = make_subscription_dto(
        status=SubscriptionStatus.LIMITED, expire_at=_NOW + timedelta(hours=2)
    )
    assert grace_entry_allowed(limited, plan, _settings(), _NOW) is False
    assert grace_entry_allowed(_expired(), None, _settings(), _NOW) is False


def test_grace_until_for_days() -> None:
    assert grace_until_for(_settings(duration_days=3), _NOW) == _NOW + timedelta(days=3)


def test_grace_until_for_zero_is_unlimited() -> None:
    assert is_unlimited_expiry(grace_until_for(_settings(duration_days=0), _NOW))


def test_grace_panel_update() -> None:
    until = _NOW + timedelta(days=3)
    update = grace_panel_update(_expired(), _settings(traffic_limit_mb=512), until)

    assert update.status == UserStatus.ACTIVE
    assert update.expire_at == until
    assert update.traffic_limit_bytes == 536870912
    assert update.traffic_limit_strategy == TrafficLimitStrategy.NO_RESET
    assert update.hwid_device_limit == 3
    assert update.active_internal_squads is None
    assert "tag" not in update.model_fields_set


def test_grace_panel_update_without_squads_keeps_the_panel_ones() -> None:
    update = grace_panel_update(_expired(), _settings(), _NOW)

    assert "active_internal_squads" not in update.model_fields_set
    assert "external_squad_uuid" not in update.model_fields_set


def test_grace_panel_update_sends_a_configured_tag() -> None:
    assert grace_panel_update(_expired(), _settings(tag="GRACE"), _NOW).tag == "GRACE"


def test_grace_panel_update_sends_configured_squads() -> None:
    internal, external = uuid4(), uuid4()
    settings = _settings(internal_squads=[internal], external_squad=external)

    update = grace_panel_update(_expired(), settings, _NOW)

    assert update.active_internal_squads == [internal]
    assert update.external_squad_uuid == external


async def _create(
    repo: SubscriptionRepository, users: UserRepository, tg: int, **overrides: Any
) -> int:
    user = await users.create(CreateUserDto(name="u", telegram_id=tg))
    data: dict[str, Any] = {
        "user_id": user.id,
        "remnawave_user_id": tg,
        "status": SubscriptionStatus.EXPIRED,
        "traffic_limit": 100,
        "device_limit": 3,
        "expire_at": datetime.now(timezone.utc) - timedelta(hours=2),
        "url": "https://example.com/s",
        "plan_snapshot": make_plan_snapshot(),
    }
    data.update(overrides)
    return (await repo.create(CreateSubscriptionDto(**data))).id


async def test_set_grace_until_round_trip(
    subscription_repository: SubscriptionRepository, user_repository: UserRepository
) -> None:
    sub_id = await _create(subscription_repository, user_repository, 9001)
    until = datetime.now(timezone.utc) + timedelta(days=3)

    assert (await subscription_repository.set_grace_until(sub_id, until)).grace_until == until
    cleared: Optional[datetime] = (
        await subscription_repository.set_grace_until(sub_id, None)
    ).grace_until
    assert cleared is None


async def test_grace_candidates_and_ended(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    plan_repository: PlanRepository,
) -> None:
    now = datetime.now(timezone.utc)
    plan = await plan_repository.create(
        CreatePlanDto(
            public_code="gracepaid",
            name="gracepaid",
            type=PlanType.BOTH,
            traffic_limit=100,
            device_limit=3,
        )
    )
    await plan_repository.set_durations(
        SetPlanDurationsDto(
            plan_id=plan.id,
            durations=[SetPlanDurationDto(days=30, order_index=0, base_price=Decimal("500"))],
        )
    )
    snapshot = make_plan_snapshot(plan_id=plan.id)
    fresh = await _create(subscription_repository, user_repository, 9002, plan_snapshot=snapshot)
    await _create(
        subscription_repository,
        user_repository,
        9003,
        expire_at=now - timedelta(days=2),
        plan_snapshot=snapshot,
    )
    ended = await _create(subscription_repository, user_repository, 9004)
    await subscription_repository.set_grace_until(ended, now - timedelta(minutes=1))

    candidates = await subscription_repository.list_grace_entry_candidates(now, 10)
    assert [s.id for s in candidates] == [fresh]
    assert [s.id for s in await subscription_repository.list_grace_ended(now, 10)] == [ended]
