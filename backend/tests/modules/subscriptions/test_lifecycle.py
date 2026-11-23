from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.db.uow import UnitOfWork
from app.core.enums import CreditSegmentKind, Currency, SubscriptionStatus
from app.core.utils.time import datetime_now
from app.modules.subscriptions.credit_segment_repository import CreditSegmentRepository
from app.modules.subscriptions.dto import CreditSegmentDto
from app.modules.subscriptions.lifecycle import SubscriptionLifecycleService
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.users.repository import UserRepository
from tests.modules.subscriptions.factories import create_subscription


async def test_sync_status_persists_a_legal_transition(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    uow: UnitOfWork,
) -> None:
    created = await create_subscription(
        subscription_repository, user_repository, telegram_id=601, remnawave_user_id=601001
    )
    service = SubscriptionLifecycleService(
        subscription_repository, CreditSegmentRepository(subscription_repository.session)
    )

    async with uow:
        result = await service.sync_status(created.id, SubscriptionStatus.LIMITED)

    assert result.status is SubscriptionStatus.LIMITED


async def test_sync_status_is_a_noop_on_an_already_deleted_subscription(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    uow: UnitOfWork,
) -> None:
    created = await create_subscription(
        subscription_repository, user_repository, telegram_id=602, remnawave_user_id=602001
    )
    service = SubscriptionLifecycleService(
        subscription_repository, CreditSegmentRepository(subscription_repository.session)
    )
    async with uow:
        await service.sync_status(created.id, SubscriptionStatus.DELETED)

    async with uow:
        result = await service.sync_status(created.id, SubscriptionStatus.ACTIVE)

    assert result.status is SubscriptionStatus.DELETED  # unchanged, no exception


async def test_renew_reactivates_an_expired_subscription(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    uow: UnitOfWork,
) -> None:
    created = await create_subscription(
        subscription_repository, user_repository, telegram_id=603, remnawave_user_id=603001
    )
    service = SubscriptionLifecycleService(
        subscription_repository, CreditSegmentRepository(subscription_repository.session)
    )
    async with uow:
        await service.sync_status(created.id, SubscriptionStatus.EXPIRED)

    new_expire_at = datetime(2027, 1, 1, tzinfo=timezone.utc)
    async with uow:
        result = await service.renew(
            created.id,
            new_expire_at,
            paid=Decimal(0),
            currency=Currency.RUB,
            previous_expire_at=created.expire_at,
            starts_at=max(created.expire_at, datetime_now()),
            payment_key=None,
        )

    assert result.status is SubscriptionStatus.ACTIVE
    assert result.expire_at == new_expire_at


@pytest.mark.parametrize(("expire_in_days", "term_days"), [(-1, 60), (5, 55)])
async def test_a_renewal_segment_runs_from_the_old_end_or_today_when_lapsed(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    uow: UnitOfWork,
    expire_in_days: int,
    term_days: int,
) -> None:
    created = await create_subscription(
        subscription_repository,
        user_repository,
        telegram_id=604 + expire_in_days + 10,
        remnawave_user_id=604_100 + expire_in_days + 10,
        expire_at=datetime_now() + timedelta(days=expire_in_days),
    )
    segments = CreditSegmentRepository(subscription_repository.session)
    service = SubscriptionLifecycleService(subscription_repository, segments)
    new_expire_at = datetime_now() + timedelta(days=60)

    async with uow:
        await service.renew(
            created.id,
            new_expire_at,
            paid=Decimal("100"),
            currency=Currency.RUB,
            previous_expire_at=created.expire_at,
            starts_at=max(created.expire_at, datetime_now()),
            payment_key="renew-1",
        )

    [segment] = await _renewals(segments, created.id)
    assert segment.kind is CreditSegmentKind.PURCHASE
    assert segment.ends_at == new_expire_at
    assert abs(segment.seconds - term_days * 86400) < 60


async def _renewals(
    segments: CreditSegmentRepository, subscription_id: int
) -> list[CreditSegmentDto]:
    # without the term the test subscription was created with
    return [
        segment
        for segment in await segments.list_for(subscription_id)
        if segment.kind is not CreditSegmentKind.LEGACY
    ]


async def _renewed_twice(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    uow: UnitOfWork,
    telegram_id: int,
) -> tuple[SubscriptionLifecycleService, CreditSegmentRepository, int, datetime]:
    now = datetime_now()
    created = await create_subscription(
        subscription_repository,
        user_repository,
        telegram_id=telegram_id,
        remnawave_user_id=telegram_id * 1000,
        expire_at=now,
    )
    segments = CreditSegmentRepository(subscription_repository.session)
    service = SubscriptionLifecycleService(subscription_repository, segments)
    async with uow:
        await service.renew(
            created.id,
            now + timedelta(days=30),
            paid=Decimal("300"),
            currency=Currency.RUB,
            previous_expire_at=now,
            starts_at=max(now, datetime_now()),
            payment_key="first",
        )
        await service.renew(
            created.id,
            now + timedelta(days=60),
            paid=Decimal("100"),
            currency=Currency.RUB,
            previous_expire_at=now + timedelta(days=30),
            starts_at=max(now + timedelta(days=30), datetime_now()),
            payment_key="second",
        )
    return service, segments, created.id, now


async def test_refunding_an_earlier_renewal_moves_the_later_one_back(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    uow: UnitOfWork,
) -> None:
    service, segments, subscription_id, now = await _renewed_twice(
        subscription_repository, user_repository, uow, 620
    )

    async with uow:
        shortened = await service.shorten_after_refund(
            subscription_id,
            days=30,
            payment_key="first",
            paid=Decimal("300"),
            unlimited=False,
            now=now,
        )

    [left] = await _renewals(segments, subscription_id)
    assert left.payment_key == "second"
    assert left.ends_at == now + timedelta(days=30) == shortened.expire_at


async def test_a_refund_without_its_own_segment_comes_off_the_latest_one(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    uow: UnitOfWork,
) -> None:
    # renewed before segments existed: its days and money sit in the legacy one
    service, segments, subscription_id, now = await _renewed_twice(
        subscription_repository, user_repository, uow, 621
    )

    async with uow:
        await service.shorten_after_refund(
            subscription_id,
            days=10,
            payment_key="legacy",
            paid=Decimal("40"),
            unlimited=False,
            now=now,
        )

    first, second = await _renewals(segments, subscription_id)
    assert first.paid == Decimal("300")
    assert second.paid == Decimal("60")
    assert second.ends_at == now + timedelta(days=50)
    assert second.seconds == 20 * 86400


async def test_refunding_an_unlimited_renewal_brings_back_what_it_replaced(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    uow: UnitOfWork,
) -> None:
    service, segments, subscription_id, now = await _renewed_twice(
        subscription_repository, user_repository, uow, 622
    )
    async with uow:
        await service.renew(
            subscription_id,
            datetime(2099, 1, 1, tzinfo=timezone.utc),
            paid=Decimal("5000"),
            currency=Currency.RUB,
            previous_expire_at=now + timedelta(days=60),
            starts_at=max(now + timedelta(days=60), datetime_now()),
            payment_key="forever",
        )
    assert [s.is_live for s in await _renewals(segments, subscription_id)] == [False, False, True]

    async with uow:
        restored = await service.shorten_after_refund(
            subscription_id,
            days=0,
            payment_key="forever",
            paid=Decimal("5000"),
            unlimited=True,
            now=now,
        )

    assert restored.expire_at == now + timedelta(days=60)
    assert [s.payment_key for s in await _renewals(segments, subscription_id) if s.is_live] == [
        "first",
        "second",
    ]


async def test_a_term_kept_through_a_plan_change_keeps_its_counter_reset(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    uow: UnitOfWork,
) -> None:
    now = datetime_now()
    old = await create_subscription(
        subscription_repository, user_repository, telegram_id=623, remnawave_user_id=623_000
    )
    new = await create_subscription(
        subscription_repository, user_repository, telegram_id=624, remnawave_user_id=624_000
    )
    segments = CreditSegmentRepository(subscription_repository.session)
    service = SubscriptionLifecycleService(subscription_repository, segments)
    async with uow:
        await service.add_term(
            old.id,
            starts_at=now,
            expire_at=now + timedelta(days=30),
            paid=Decimal("300"),
            currency=Currency.RUB,
            payment_key="paid-ahead",
        )
        await service.record_traffic_reset(old.id, "paid-ahead", at=now, pace=Decimal("0.5"))
        await service.carry_credit(old.id, new.id)

    carried = [s for s in await segments.list_for(new.id) if s.kind is CreditSegmentKind.CARRIED]
    assert [(s.traffic_reset_at, s.traffic_pace, s.payment_key) for s in carried][-1] == (
        now,
        Decimal("0.5"),
        None,
    )


async def _with_reset(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    uow: UnitOfWork,
    telegram_id: int,
) -> tuple[SubscriptionLifecycleService, int, datetime]:
    now = datetime_now()
    created = await create_subscription(
        subscription_repository,
        user_repository,
        telegram_id=telegram_id,
        remnawave_user_id=telegram_id * 1000,
        expire_at=now + timedelta(days=1),
    )
    service = SubscriptionLifecycleService(
        subscription_repository, CreditSegmentRepository(subscription_repository.session)
    )
    async with uow:
        await service.add_term(
            created.id,
            starts_at=now - timedelta(days=29),
            expire_at=now + timedelta(days=1),
            paid=Decimal("300"),
            currency=Currency.RUB,
            payment_key="month",
        )
        await service.record_traffic_reset(
            created.id, "month", at=now - timedelta(days=29), pace=Decimal(1)
        )
        await service.renew(
            created.id,
            now + timedelta(days=31),
            paid=Decimal("300"),
            currency=Currency.RUB,
            previous_expire_at=now + timedelta(days=1),
            starts_at=max(now + timedelta(days=1), datetime_now()),
            payment_key="next",
        )
    return service, created.id, now


async def test_an_early_run_on_traffic_fades_with_the_next_term(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    uow: UnitOfWork,
) -> None:
    # all the traffic gone a day before the end, then renewed: only that day is lost
    service, subscription_id, now = await _with_reset(
        subscription_repository, user_repository, uow, 625
    )

    async with uow:
        pace = await service.pace_before_reset(subscription_id, "next", Decimal(0), now)

    assert pace == Decimal(30) / Decimal(31)


async def test_a_plan_change_without_a_recorded_reset_counts_from_the_running_term(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    uow: UnitOfWork,
) -> None:
    now = datetime_now()
    created = await create_subscription(
        subscription_repository,
        user_repository,
        telegram_id=626,
        remnawave_user_id=626_000,
        expire_at=now + timedelta(days=15),
    )
    service = SubscriptionLifecycleService(
        subscription_repository, CreditSegmentRepository(subscription_repository.session)
    )

    async with uow:
        reset_at, pace = await service.last_traffic_reset(created.id, now)

    # the factory's term: 30 days ending at the expiry
    assert reset_at == created.expire_at - timedelta(days=30)
    assert pace == 1
