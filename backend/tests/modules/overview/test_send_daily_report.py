from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

import pytest
from redis.asyncio import Redis

from app.core.config.build import BuildConfig
from app.core.db.uow import UnitOfWork
from app.core.enums import (
    Currency,
    DiscountSource,
    Locale,
    PaymentGatewayType,
    PurchaseChannel,
    PurchaseType,
    TransactionStatus,
)
from app.core.utils.time import datetime_now
from app.modules.notifications.dto import UpdateNotificationSettingsDto
from app.modules.notifications.render import NotificationRenderer, notification_args
from app.modules.notifications.repository import NotificationRepository
from app.modules.notifications.telegram_data import CardData
from app.modules.notifications.telegram_layout import build_telegram_card
from app.modules.overview.events import DailyReportEvent
from app.modules.overview.usecases import send_daily_report
from app.modules.overview.usecases.send_daily_report import (
    SendDailyReportUseCase,
    day_bounds,
    report_day,
)
from app.modules.pricing.repository import PricingRepository
from app.modules.referrals.repository import ReferralRepository
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.subscriptions.stats_repository import SubscriptionStatsRepository
from app.modules.transactions.dto import CreateTransactionDto
from app.modules.transactions.repository import TransactionRepository
from app.modules.transactions.stats_repository import TransactionStatsRepository
from app.modules.users.dto import CreateUserDto
from app.modules.users.repository import UserRepository
from app.modules.users.stats_repository import UserStatsRepository
from tests.fakes.events import RecordingEventPublisher, stored_event
from tests.modules.subscriptions.factories import create_subscription, make_plan_snapshot

MOSCOW: Final[ZoneInfo] = ZoneInfo("Europe/Moscow")
_RU_NOTIFICATIONS: Final[Path] = (
    Path(__file__).parents[3] / "assets" / "translations" / "ru" / "notifications.ftl"
)


def test_report_day_is_yesterday_in_the_shop_zone_at_its_hour() -> None:
    # 21:30 UTC is 00:30 next day in Moscow
    now = datetime(2026, 11, 21, 21, 30, tzinfo=UTC)
    assert report_day(now, MOSCOW, 0) == date(2026, 11, 21)
    assert report_day(now, MOSCOW, 9) is None
    assert report_day(now, ZoneInfo("UTC"), 21) == date(2026, 11, 20)


def test_day_bounds_are_local_midnights() -> None:
    start, end = day_bounds(date(2026, 11, 21), MOSCOW)
    assert start == datetime(2026, 11, 20, 21, tzinfo=UTC)
    assert end == datetime(2026, 11, 21, 21, tzinfo=UTC)


async def _pay(
    transaction_repository: TransactionRepository,
    uow: UnitOfWork,
    user_id: int,
    plan_id: int,
    purchase_type: PurchaseType,
    price: str,
    key: str,
) -> None:
    async with uow:
        order = await transaction_repository.create(
            CreateTransactionDto(
                channel=PurchaseChannel.CABINET,
                user_id=user_id,
                plan_id=plan_id,
                plan_snapshot=make_plan_snapshot(plan_id=plan_id),
                duration_days=30,
                currency=Currency.RUB,
                purchase_type=purchase_type,
                target_subscription_id=None,
                gateway_type=PaymentGatewayType.YOOKASSA,
                base_price=Decimal(price),
                discount_percent=0,
                discount_source=DiscountSource.NONE,
                final_price=Decimal(price),
                anchor_base_price=Decimal(price),
                anchor_final_price=Decimal(price),
                customer_email=None,
                idempotency_key=key,
                expires_at=datetime_now() + timedelta(minutes=30),
            )
        )
    await transaction_repository.mark_status(order.id, TransactionStatus.PAID)


async def test_report_counts_the_day_once(
    monkeypatch: pytest.MonkeyPatch,
    user_repository: UserRepository,
    subscription_repository: SubscriptionRepository,
    transaction_repository: TransactionRepository,
    pricing_repository: PricingRepository,
    notification_repository: NotificationRepository,
    referral_repository: ReferralRepository,
    redis: Redis,
    uow: UnitOfWork,
    plan_id: int,
) -> None:
    # runs "tomorrow" in UTC at its hour, so the report covers today
    tomorrow = datetime_now() + timedelta(days=1)
    monkeypatch.setattr(send_daily_report, "datetime_now", lambda: tomorrow)
    async with uow:
        await notification_repository.update_settings(
            UpdateNotificationSettingsDto(report_timezone="UTC", daily_report_hour=tomorrow.hour)
        )
    session = user_repository.session
    publisher = RecordingEventPublisher()
    use_case = SendDailyReportUseCase(
        notification_repository,
        UserStatsRepository(session),
        SubscriptionStatsRepository(session),
        TransactionStatsRepository(session),
        referral_repository,
        pricing_repository,
        publisher,
        redis,
        uow,
    )

    await use_case.system()
    before = publisher.events[-1]
    assert isinstance(before, DailyReportEvent)
    await redis.flushdb()

    trial = await create_subscription(
        subscription_repository,
        user_repository,
        telegram_id=880_001,
        remnawave_user_id=880_001,
        is_trial=True,
    )
    await _pay(transaction_repository, uow, trial.user_id, plan_id, PurchaseType.NEW, "500", "dr-1")
    await _pay(
        transaction_repository, uow, trial.user_id, plan_id, PurchaseType.RENEW, "300", "dr-2"
    )
    await _pay(transaction_repository, uow, trial.user_id, plan_id, PurchaseType.NEW, "0", "dr-3")
    invited = await user_repository.create(CreateUserDto(name="Invited", telegram_id=880_002))
    unnamed = await user_repository.create(CreateUserDto(name="", telegram_id=880_003))
    invited_by_unnamed = await user_repository.create(
        CreateUserDto(name="Second", telegram_id=880_004)
    )
    async with uow:
        await referral_repository.create_referral(
            referrer_id=trial.user_id, referred_id=invited.id, level=1
        )
        await referral_repository.create_referral(
            referrer_id=unnamed.id, referred_id=invited_by_unnamed.id, level=1
        )

    await use_case.system()
    await use_case.system()

    assert len(publisher.events) == 2
    report = publisher.events[-1]
    assert isinstance(report, DailyReportEvent)
    assert report.day == (tomorrow.date() - timedelta(days=1)).isoformat()
    assert report.new_users == before.new_users + 4
    assert report.new_trials == before.new_trials + 1
    assert report.new_payers == before.new_payers + 1
    assert report.trial_conversions == before.trial_conversions + 1
    assert report.purchases == before.purchases + 1
    assert report.renewals == before.renewals + 1
    assert Decimal(report.revenue) == Decimal(before.revenue) + Decimal("800")
    assert (trial.user_id, "buyer", 1) in report.top_referrers
    assert (unnamed.id, "", 1) in report.top_referrers

    texts = {Locale.RU: _RU_NOTIFICATIONS.read_text()}
    renderer = NotificationRenderer(texts, default_locale=Locale.RU)
    stored = stored_event(report)
    args = notification_args(stored.payload, None, Locale.RU)
    title, body = renderer.render(Locale.RU, stored.event_type, args)
    card = build_telegram_card(
        renderer,
        Locale.RU,
        event_type=stored.event_type,
        payload=stored.payload,
        args=args,
        data=CardData(),
        build=BuildConfig(),
        title=title,
        body=body,
        notes="",
    )
    assert "Отчёт за" in card
    assert "Продления" in card
    assert "buyer</b>: 1" in card
    assert f"Пользователь #{unnamed.id}</b>: 1" in card
