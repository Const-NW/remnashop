from datetime import datetime
from decimal import Decimal

from remnapy import RemnawaveSDK

from app.core.constants import ZERO_AMOUNT
from app.core.db.uow import UnitOfWork
from app.core.enums import Currency
from app.modules.pricing import PricingRepository
from app.modules.settings import SettingsRepository
from app.modules.subscriptions.domain import calculate_proration_credit
from app.modules.subscriptions.dto import SubscriptionDto


class ChangeCreditCalculator:
    """What a change to a fixed-term plan credits as days: the purchase that applies it
    and the quote that shows it must agree."""

    def __init__(
        self,
        remnawave_sdk: RemnawaveSDK,
        uow: UnitOfWork,
        pricing_repository: PricingRepository,
        settings_repository: SettingsRepository,
    ) -> None:
        self._remnawave_sdk = remnawave_sdk
        self._uow = uow
        self._pricing_repository = pricing_repository
        self._settings_repository = settings_repository

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
        if not shop.plan_change_credit_enabled or duration_days <= 0:
            return ZERO_AMOUNT, 0
        daily_rate_new = list_price / Decimal(duration_days)
        if daily_rate_new <= 0:
            return ZERO_AMOUNT, 0
        panel_user = await self._remnawave_sdk.users.get_user_by_id(old.remnawave_user_id)
        credit = calculate_proration_credit(
            credit_paid=old.plan_snapshot.effective_credit_paid,
            credit_days=old.plan_snapshot.effective_credit_days,
            expire_at=old.expire_at,
            is_trial=old.is_trial,
            now=now,
            traffic_limit=old.effective_traffic_limit,
            traffic_limit_strategy=old.traffic_limit_strategy,
            used_traffic_bytes=int(panel_user.used_traffic_bytes),
        )
        if old.plan_snapshot.currency is not currency:
            async with self._uow:
                rates = await self._pricing_repository.get_rate_table()
            credit = rates.convert(credit, old.plan_snapshot.currency, currency)
        return credit, int(credit / daily_rate_new)
