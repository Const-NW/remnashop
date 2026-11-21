from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.enums import Currency, PlanType
from app.modules.plans.dto import PlanDto, PlanDurationDto, PlanPriceOverrideDto
from app.modules.subscriptions.unlimited import expiry_after, is_unlimited_expiry
from tests.modules.subscriptions.factories import make_subscription_dto


def test_expiry_after_zero_is_year_2099_same_day() -> None:
    start = datetime(2026, 11, 14, 10, 30, tzinfo=timezone.utc)

    assert expiry_after(start, 0, exact_days=False) == datetime(
        2099, 11, 14, 10, 30, tzinfo=timezone.utc
    )


def test_expiry_after_feb_29_clamps() -> None:
    start = datetime(2028, 2, 29, tzinfo=timezone.utc)

    assert expiry_after(start, 0, exact_days=False) == datetime(2099, 2, 28, tzinfo=timezone.utc)


def test_expiry_after_days_adds() -> None:
    start = datetime(2026, 11, 14, tzinfo=timezone.utc)

    assert expiry_after(start, 45, exact_days=False) == start + timedelta(days=45)


def test_expiry_after_month_is_a_calendar_month() -> None:
    start = datetime(2026, 1, 31, 12, tzinfo=timezone.utc)

    assert expiry_after(start, 30, exact_days=False) == datetime(
        2026, 2, 28, 12, tzinfo=timezone.utc
    )


def test_expiry_after_exact_days_stays_plain_days() -> None:
    start = datetime(2026, 1, 31, 12, tzinfo=timezone.utc)

    assert expiry_after(start, 30, exact_days=True) == start + timedelta(days=30)


def test_expiry_after_negative_raises() -> None:
    with pytest.raises(ValueError):
        expiry_after(datetime(2026, 1, 1, tzinfo=timezone.utc), -1, exact_days=False)


def test_is_unlimited_expiry_threshold() -> None:
    assert not is_unlimited_expiry(datetime(2098, 12, 31, 23, 59, tzinfo=timezone.utc))
    assert is_unlimited_expiry(datetime(2099, 1, 1, tzinfo=timezone.utc))
    assert is_unlimited_expiry(datetime(2105, 6, 1, tzinfo=timezone.utc))


def test_subscription_dto_is_unlimited() -> None:
    assert make_subscription_dto(expire_at=datetime(2099, 3, 1, tzinfo=timezone.utc)).is_unlimited
    assert not make_subscription_dto(
        expire_at=datetime(2027, 3, 1, tzinfo=timezone.utc)
    ).is_unlimited


def _plan(*, is_trial: bool, durations: list[PlanDurationDto]) -> PlanDto:
    return PlanDto(
        public_code="plan",
        name="Plan",
        type=PlanType.BOTH,
        traffic_limit=1,
        device_limit=1,
        is_trial=is_trial,
        durations=durations,
    )


def test_a_trial_offers_its_unlimited_duration_for_free() -> None:
    forever = PlanDurationDto(id=1, days=0, base_price=Decimal("0"))
    trial = _plan(is_trial=True, durations=[forever])

    assert trial.sellable_durations() == [forever]
    assert trial.free_trial_duration() == forever


def test_a_free_duration_is_sold_but_never_renewed() -> None:
    free = PlanDurationDto(id=1, days=30, base_price=Decimal("0"))
    paid = PlanDurationDto(id=2, days=90, base_price=Decimal("300"))
    # a migrated duration without an anchor price: free in the anchor, paid elsewhere
    unpriced = PlanDurationDto(
        id=3,
        days=180,
        base_price=Decimal("0"),
        overrides=[PlanPriceOverrideDto(currency=Currency.RUB, price=Decimal("500"))],
    )
    plan = _plan(is_trial=False, durations=[free, paid, unpriced])

    assert plan.sellable_durations() == [free, paid]
    assert plan.renewable_durations() == [paid]
