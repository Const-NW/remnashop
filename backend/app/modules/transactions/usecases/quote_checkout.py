from dataclasses import replace

from app.core.db.uow import UnitOfWork
from app.core.error_codes import ErrorCode
from app.core.exceptions import ValidationError
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.core.utils.time import datetime_now
from app.modules.subscriptions import (
    ChangeCreditCalculator,
    SubscriptionRepository,
    get_owned_target,
)
from app.modules.transactions.checkout_pricing import CheckoutPricer
from app.modules.transactions.dto import PriceQuote, QuoteCheckoutDto
from app.modules.users import UserDto


class QuoteCheckoutUseCase(UseCase[QuoteCheckoutDto, PriceQuote, UserDto]):
    """The price checkout would charge right now. Gateway, subscription cap and
    payments_enabled are checkout's own refusals, so they aren't checked here."""

    required_permission = Permission.PUBLIC

    def __init__(
        self,
        pricer: CheckoutPricer,
        uow: UnitOfWork,
        subscription_repository: SubscriptionRepository,
        change_credit_calculator: ChangeCreditCalculator,
    ) -> None:
        self._pricer = pricer
        self._uow = uow
        self._subscription_repository = subscription_repository
        self._change_credit_calculator = change_credit_calculator

    async def _execute(self, actor: UserDto, data: QuoteCheckoutDto) -> PriceQuote:
        async with self._uow:
            plan, user = await self._pricer.load_purchasable_plan(
                actor.id, data.plan_id, data.plan_code, data.renew_subscription_id
            )
            target = (
                await get_owned_target(
                    self._subscription_repository, data.change_subscription_id, actor.id
                )
                if data.change_subscription_id is not None
                else None
            )
        # the order would be refused the same way
        if target is not None and target.plan_snapshot.plan_id == plan.id:
            raise ValidationError(
                "Changing to the same plan is a RENEW, not a CHANGE",
                code=ErrorCode.CHANGE_SAME_PLAN,
            )
        user = await self._pricer.drop_dead_promocode_discounts(user)
        change_credit = await self._pricer.change_credit(
            actor.id, data.change_subscription_id, data.duration_days, data.currency
        )
        quote = await self._pricer.quote(
            plan, data.duration_days, data.currency, user, change_credit=change_credit
        )
        if target is None or data.duration_days == 0:
            return quote
        _, days = await self._change_credit_calculator.credit(
            target,
            currency=data.currency,
            list_price=quote.base_price,
            duration_days=data.duration_days,
            now=datetime_now(),
        )
        return replace(quote, change_credit_days=days)
