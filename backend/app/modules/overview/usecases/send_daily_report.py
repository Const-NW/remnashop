from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Final, Optional
from zoneinfo import ZoneInfo

from loguru import logger
from redis.asyncio import Redis

from app.core.actor import Actor
from app.core.constants import REDIS_KEY_PREFIX, TTL_7D, ZERO_AMOUNT
from app.core.db.uow import UnitOfWork
from app.core.enums import PurchaseType, Role
from app.core.ports import EventPublisher
from app.core.usecase import UseCase
from app.core.utils.money import round_cents
from app.core.utils.time import datetime_now
from app.modules.notifications import NotificationRepository
from app.modules.overview.events import DailyReportEvent
from app.modules.pricing import PricingRepository
from app.modules.referrals import ReferralRepository
from app.modules.subscriptions import SubscriptionStatsRepository
from app.modules.transactions import TransactionStatsRepository
from app.modules.users import UserStatsRepository

_SENT_KEY: Final[str] = f"{REDIS_KEY_PREFIX}daily_report_sent"
_TOP_REFERRERS: Final[int] = 5


def report_day(now: datetime, zone: ZoneInfo, hour: int) -> Optional[date]:
    """The local day to report on, when `now` falls in the configured local hour."""
    local = now.astimezone(zone)
    if local.hour != hour:
        return None
    return local.date() - timedelta(days=1)


def day_bounds(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(), tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time(), tzinfo=zone)
    return start, end


class SendDailyReportUseCase(UseCase[None, None]):
    required_permission = None

    def __init__(
        self,
        notification_repository: NotificationRepository,
        user_stats: UserStatsRepository,
        subscription_stats: SubscriptionStatsRepository,
        transaction_stats: TransactionStatsRepository,
        referral_repository: ReferralRepository,
        pricing_repository: PricingRepository,
        event_publisher: EventPublisher,
        redis: Redis,
        uow: UnitOfWork,
    ) -> None:
        self._notification_repository = notification_repository
        self._user_stats = user_stats
        self._subscription_stats = subscription_stats
        self._transaction_stats = transaction_stats
        self._referral_repository = referral_repository
        self._pricing_repository = pricing_repository
        self._event_publisher = event_publisher
        self._redis = redis
        self._uow = uow

    async def _execute(self, actor: Actor, data: None) -> None:
        now = datetime_now()
        async with self._uow:
            settings = await self._notification_repository.get_settings()
        zone = ZoneInfo(settings.report_timezone)
        day = report_day(now, zone, settings.daily_report_hour)
        if day is None:
            return
        # a redelivered task or a second worker process reports the day once
        key = f"{_SENT_KEY}:{day.isoformat()}"
        if not await self._redis.set(key, "1", nx=True, ex=TTL_7D):
            return
        try:
            await self._publish(day, zone, now)
        except Exception:
            # lets a redelivery of this run send it
            await self._redis.delete(key)
            raise
        logger.info(f"Daily report for {day} published")

    async def _publish(self, day: date, zone: ZoneInfo, now: datetime) -> None:
        start, end = day_bounds(day, zone)
        async with self._uow:
            currency = (await self._pricing_repository.get_rate_table()).anchor
            registrations = await self._user_stats.list_registrations(start, end, Role.USER)
            trials = await self._subscription_stats.list_trials_started(start, end)
            new_payers = await self._transaction_stats.first_paid_at(end, since=start)
            converted = await self._subscription_stats.list_user_ids_with_trial(list(new_payers))
            payments = await self._transaction_stats.list_paid(start, end)
            subscriptions = await self._subscription_stats.overview_counts(now, now)
            never_connected = await self._subscription_stats.count_paying_never_connected()
            referrers = await self._referral_repository.top_referrers(start, end, _TOP_REFERRERS)

            counts: dict[PurchaseType, int] = defaultdict(int)
            amounts: dict[PurchaseType, Decimal] = defaultdict(lambda: ZERO_AMOUNT)
            for payment in payments:
                # free orders are not money in
                if payment.anchor_final_price > 0:
                    counts[payment.purchase_type] += 1
                    amounts[payment.purchase_type] += payment.anchor_final_price

            await self._event_publisher.publish(
                DailyReportEvent(
                    day=day.isoformat(),
                    new_users=len(registrations),
                    new_trials=len(trials),
                    trial_conversions=len(converted),
                    new_payers=len(new_payers),
                    currency=currency.value,
                    revenue=str(round_cents(sum(amounts.values(), ZERO_AMOUNT))),
                    active_trials=subscriptions.active_trials,
                    active_paid=subscriptions.paid_active,
                    purchases=counts[PurchaseType.NEW],
                    purchases_amount=str(round_cents(amounts[PurchaseType.NEW])),
                    renewals=counts[PurchaseType.RENEW],
                    renewals_amount=str(round_cents(amounts[PurchaseType.RENEW])),
                    changes=counts[PurchaseType.CHANGE],
                    changes_amount=str(round_cents(amounts[PurchaseType.CHANGE])),
                    paid_never_connected=never_connected,
                    top_referrers=[(r.user_id, r.name, r.invited) for r in referrers],
                )
            )
