from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from remnapy import RemnawaveSDK
from remnapy.enums import TrafficLimitStrategy
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.uow import UnitOfWork
from app.core.enums import CreditSegmentKind, Currency, SubscriptionStatus
from app.core.utils.time import datetime_now
from app.modules.plans.dto import PlanDto
from app.modules.pricing.repository import PricingRepository
from app.modules.referrals.repository import ReferralRepository
from app.modules.settings.repository import SettingsRepository
from app.modules.subscriptions.change_credit import ChangeCreditCalculator
from app.modules.subscriptions.credit_segment_repository import CreditSegmentRepository
from app.modules.subscriptions.dto import (
    CreateCreditSegmentDto,
    CreateSubscriptionDto,
    PlanSnapshot,
    SubscriptionDto,
)
from app.modules.subscriptions.eligibility import PurchaseEligibility
from app.modules.subscriptions.lifecycle import SubscriptionLifecycleService
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.transactions.repository import TransactionRepository
from app.modules.users.dto import CreateUserDto
from app.modules.users.repository import UserRepository
from tests.fakes.panel_squads import FixedPanelSquads


def make_eligibility(
    session: AsyncSession, panel_squads: Optional[frozenset[UUID]] = None
) -> PurchaseEligibility:
    return PurchaseEligibility(
        SubscriptionRepository(session),
        ReferralRepository(session),
        SettingsRepository(session),
        TransactionRepository(session),
        UserRepository(session),
        FixedPanelSquads(panel_squads),
    )


def make_change_credit_calculator(
    session: AsyncSession, remnawave_sdk: RemnawaveSDK
) -> ChangeCreditCalculator:
    subscriptions = SubscriptionRepository(session)
    return ChangeCreditCalculator(
        remnawave_sdk,
        UnitOfWork(session),
        PricingRepository(session),
        SettingsRepository(session),
        SubscriptionLifecycleService(subscriptions, CreditSegmentRepository(session)),
    )


@dataclass(frozen=True, kw_only=True)
class CreditSummary:
    """A subscription's credit segments in sum: what's live, what an unlimited
    term replaced, and where its refund would take the expiry."""

    live_paid: Decimal
    live_days: int
    superseded_paid: Decimal
    superseded_days: int
    replaced_expire_at: Optional[datetime]


async def credit_summary(session: AsyncSession, subscription_id: int) -> CreditSummary:
    segments = await CreditSegmentRepository(session).list_for(subscription_id)
    live = [segment for segment in segments if segment.is_live]
    superseded = [segment for segment in segments if not segment.is_live]
    unlimited = [segment for segment in live if segment.ends_at is None]
    return CreditSummary(
        live_paid=sum((segment.paid for segment in live), Decimal(0)),
        live_days=round(sum(segment.seconds for segment in live) / 86400),
        superseded_paid=sum((segment.paid for segment in superseded), Decimal(0)),
        superseded_days=round(sum(segment.seconds for segment in superseded) / 86400),
        replaced_expire_at=unlimited[-1].replaced_expire_at if unlimited else None,
    )


def make_plan_snapshot(**overrides: Any) -> PlanSnapshot:
    """Placeholder PlanSnapshot; override a field (e.g. plan_id=7) to assert
    on it."""
    defaults: dict[str, Any] = {
        "plan_id": 1,
        "public_code": "basic",
        "name": "Basic",
        "price_paid": Decimal("0"),
        "currency": Currency.RUB,
        "duration_days": 30,
        "traffic_limit": 100,
        "device_limit": 3,
        "traffic_limit_strategy": TrafficLimitStrategy.NO_RESET,
        "is_trial": False,
        "internal_squads": [],
        "external_squad": None,
    }
    defaults.update(overrides)
    return PlanSnapshot(**defaults)


def make_subscription_dto(**overrides: Any) -> SubscriptionDto:
    """A SubscriptionDto that never touched the database, for pure rules."""
    defaults: dict[str, Any] = {
        "id": 1,
        "user_id": 1,
        "remnawave_user_id": 1,
        "status": SubscriptionStatus.ACTIVE,
        "traffic_limit": 100,
        "device_limit": 3,
        "effective_traffic_limit": 100,
        "effective_device_limit": 3,
        "expire_at": datetime(2030, 1, 1, tzinfo=timezone.utc),
        "url": "https://example.test/sub",
        "plan_snapshot": make_plan_snapshot(),
    }
    defaults.update(overrides)
    return SubscriptionDto(**defaults)


def snapshot_from_plan(plan: PlanDto, **overrides: Any) -> PlanSnapshot:
    """Builds the PlanSnapshot a real checkout/promocode-redemption path would
    build for a live plan, for tests that purchase against a plan they just
    created via PlanRepository and need the operational fields to match it."""
    defaults: dict[str, Any] = {
        "plan_id": plan.id,
        "public_code": plan.public_code,
        "name": plan.name,
        "price_paid": Decimal("0"),
        "currency": Currency.RUB,
        "duration_days": 30,
        "traffic_limit": plan.traffic_limit,
        "device_limit": plan.device_limit,
        "traffic_limit_strategy": plan.traffic_limit_strategy,
        "is_trial": plan.is_trial,
        "internal_squads": plan.internal_squads,
        "external_squad": plan.external_squad,
    }
    defaults.update(overrides)
    return PlanSnapshot(**defaults)


async def create_subscription(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    *,
    telegram_id: int,
    remnawave_user_id: int,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    expire_at: Optional[datetime] = None,
    is_trial: bool = False,
    **snapshot_overrides: Any,
) -> SubscriptionDto:
    """Creates a user and a subscription for it in one call. `telegram_id`
    and `remnawave_user_id` must be unique per test (both columns are
    unique)."""
    user = await user_repository.create(CreateUserDto(name="buyer", telegram_id=telegram_id))
    subscription = await subscription_repository.create(
        CreateSubscriptionDto(
            user_id=user.id,
            remnawave_user_id=remnawave_user_id,
            status=status,
            is_trial=is_trial,
            traffic_limit=100,
            device_limit=3,
            expire_at=expire_at or (datetime_now() + timedelta(days=30)),
            url=f"https://example.com/sub/{remnawave_user_id}",
            plan_snapshot=make_plan_snapshot(**snapshot_overrides),
        )
    )
    await add_paid_term(subscription_repository.session, subscription)
    return subscription


async def add_paid_term(session: AsyncSession, subscription: SubscriptionDto) -> None:
    """The plan's price over its term up to the expiry, as a migrated subscription has it."""
    snapshot = subscription.plan_snapshot
    await CreditSegmentRepository(session).create(
        CreateCreditSegmentDto(
            subscription_id=subscription.id,
            kind=CreditSegmentKind.LEGACY,
            paid=snapshot.price_paid,
            currency=snapshot.currency,
            seconds=max(snapshot.duration_days, 0) * 86400,
            ends_at=subscription.expire_at if snapshot.duration_days > 0 else None,
        )
    )
