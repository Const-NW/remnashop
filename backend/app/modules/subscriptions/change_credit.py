from datetime import datetime
from decimal import Decimal
from typing import Optional

from remnapy import RemnawaveSDK

from app.core.constants import ZERO_AMOUNT
from app.core.db.uow import UnitOfWork
from app.core.enums import Currency
from app.core.utils.money import round_cents
from app.core.utils.time import datetime_now
from app.modules.pricing import PricingRepository
from app.modules.settings import SettingsRepository
from app.modules.subscriptions.credit import (
    remaining_term_credit,
    traffic_left_share,
    traffic_pace,
    unlimited_credit,
)
from app.modules.subscriptions.dto import SubscriptionDto
from app.modules.subscriptions.lifecycle import SubscriptionLifecycleService
from app.modules.subscriptions.unlimited import is_unlimited_expiry


class ChangeCreditCalculator:
    """What a change to a fixed-term plan credits as days: the purchase that applies it
    and the quote that shows it must agree."""

    def __init__(
        self,
        remnawave_sdk: RemnawaveSDK,
        uow: UnitOfWork,
        pricing_repository: PricingRepository,
        settings_repository: SettingsRepository,
        lifecycle_service: SubscriptionLifecycleService,
    ) -> None:
        self._remnawave_sdk = remnawave_sdk
        self._uow = uow
        self._pricing_repository = pricing_repository
        self._settings_repository = settings_repository
        self._lifecycle_service = lifecycle_service

    async def credit(
        self,
        old: SubscriptionDto,
        *,
        currency: Currency,
        list_price: Decimal,
        duration_days: int,
        now: datetime,
    ) -> tuple[Decimal, int]:
        """The credit in `currency` and the days it buys. Divided by the list price:
        at the discounted rate a 90%-off checkout would buy 10x the credit days."""
        async with self._uow:
            shop = await self._settings_repository.get_shop_settings()
            segments = await self._lifecycle_service.credit_segments(old.id)
            rates = await self._pricing_repository.get_rate_table()
        if not shop.plan_change_credit_enabled or duration_days <= 0:
            return ZERO_AMOUNT, 0
        daily_rate_new = list_price / Decimal(duration_days)
        # an unlimited term has no remaining days to credit
        if daily_rate_new <= 0 or old.is_trial or is_unlimited_expiry(old.expire_at):
            return ZERO_AMOUNT, 0

        def convert(amount: Decimal, source: Currency) -> Decimal:
            return rates.convert(amount, source, currency)

        credit = remaining_term_credit(segments, now=now, expire_at=old.expire_at, convert=convert)
        if credit > 0 and shop.plan_change_traffic_cap_enabled:
            traffic_left = await self._traffic_left(old)
            if traffic_left is not None:
                credit *= traffic_pace(
                    segments, now=now, expire_at=old.expire_at, traffic_left=traffic_left
                )
        return credit, int(credit / daily_rate_new)

    async def unlimited_credit(self, old: SubscriptionDto, currency: Currency) -> Decimal:
        """What a change from an unlimited subscription to another unlimited plan takes
        off the price: what was paid for it and not replaced or used up since."""
        if not old.is_unlimited or old.is_trial:
            return ZERO_AMOUNT
        async with self._uow:
            segments = await self._lifecycle_service.credit_segments(old.id)
            rates = await self._pricing_repository.get_rate_table()
        credit = unlimited_credit(
            segments,
            now=datetime_now(),
            convert=lambda amount, source: rates.convert(amount, source, currency),
        )
        # taken off a price and stored on the order
        return round_cents(credit)

    async def _traffic_left(self, old: SubscriptionDto) -> Optional[Decimal]:
        if traffic_left_share(old, 0) is None:
            return None
        panel_user = await self._remnawave_sdk.users.get_user_by_id(old.remnawave_user_id)
        return traffic_left_share(old, int(panel_user.used_traffic_bytes))
