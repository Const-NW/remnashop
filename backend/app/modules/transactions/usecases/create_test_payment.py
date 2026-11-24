from datetime import timedelta
from decimal import Decimal
from typing import Final
from uuid import uuid4

from loguru import logger
from remnapy.enums import TrafficLimitStrategy

from app.core.db.uow import UnitOfWork
from app.core.enums import (
    DiscountSource,
    PaymentGatewayType,
    PurchaseChannel,
    PurchaseType,
    TransactionStatus,
)
from app.core.error_codes import ErrorCode
from app.core.exceptions import PaymentGatewayError, ValidationError
from app.core.ports import EventPublisher
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.core.utils.time import datetime_now
from app.modules.gateways import GatewayRepository
from app.modules.pricing import PricingRepository, convert_for_report
from app.modules.settings import SettingsRepository
from app.modules.subscriptions import PlanSnapshot
from app.modules.transactions.dto import (
    CreateTestPaymentDto,
    CreateTransactionDto,
    TestPaymentDto,
)
from app.modules.transactions.events import GatewayTestPaymentCreatedEvent
from app.modules.transactions.gateways.factory import PaymentGatewayFactory
from app.modules.transactions.gateways.registry import GATEWAY_REGISTRY, gateway_currency
from app.modules.transactions.gateways.telegram_stars import with_bot_settings
from app.modules.transactions.ports import PaymentTexts
from app.modules.transactions.repository import TransactionRepository
from app.modules.users import UserDto

_TEST_AMOUNT: Final[Decimal] = Decimal(2)
_STARS_TEST_AMOUNT: Final[Decimal] = Decimal(1)
_TEST_ORDER_TTL: Final[timedelta] = timedelta(minutes=30)
# stands in for an admin without an email when the gateway wants one for a receipt
_TEST_CUSTOMER_EMAIL: Final[str] = "test@example.com"


class CreateTestPaymentUseCase(UseCase[CreateTestPaymentDto, TestPaymentDto, UserDto]):
    required_permission = Permission.GATEWAYS_TEST

    def __init__(
        self,
        gateway_repository: GatewayRepository,
        transaction_repository: TransactionRepository,
        pricing_repository: PricingRepository,
        settings_repository: SettingsRepository,
        uow: UnitOfWork,
        gateway_factory: PaymentGatewayFactory,
        event_publisher: EventPublisher,
        payment_texts: PaymentTexts,
    ) -> None:
        self._gateway_repository = gateway_repository
        self._transaction_repository = transaction_repository
        self._pricing_repository = pricing_repository
        self._settings_repository = settings_repository
        self._uow = uow
        self._gateway_factory = gateway_factory
        self._event_publisher = event_publisher
        self._payment_texts = payment_texts

    async def _execute(self, actor: UserDto, request: CreateTestPaymentDto) -> TestPaymentDto:
        data = request.gateway_type
        gateway_class = GATEWAY_REGISTRY.get(data)
        if gateway_class is None:
            raise ValidationError(
                f"Gateway '{data.value}' cannot be tested from the dashboard",
                code=ErrorCode.GATEWAY_NOT_TESTABLE,
            )

        async with self._uow:
            gateway_config = await self._gateway_repository.get_by_type(data)
            if not gateway_config.is_configured:
                raise ValidationError(
                    f"Gateway '{data.value}' is not configured",
                    code=ErrorCode.GATEWAY_NOT_CONFIGURED,
                )
            settings = await self._gateway_repository.get_raw_settings(data)
            shop_settings = await self._settings_repository.get_shop_settings()
            if data is PaymentGatewayType.TELEGRAM_STARS:
                settings = await self._with_bot_settings(actor, settings)

        customer_email = shop_settings.payment_email or actor.email
        if customer_email is None and gateway_class.requires_customer_email(settings):
            customer_email = _TEST_CUSTOMER_EMAIL

        try:
            account = gateway_class.account_for(
                settings, in_telegram=request.channel is PurchaseChannel.BOT
            )
        except PaymentGatewayError as error:  # settings changed since the check above
            raise ValidationError(
                f"Gateway '{data.value}' is not configured", code=ErrorCode.GATEWAY_NOT_CONFIGURED
            ) from error
        currency = gateway_currency(data, settings)
        amount = _STARS_TEST_AMOUNT if data is PaymentGatewayType.TELEGRAM_STARS else _TEST_AMOUNT
        async with self._uow:
            rates = await self._pricing_repository.get_rate_table()
            anchor_amount = convert_for_report(amount, currency, rates.anchor, rates)
            order = await self._transaction_repository.create(
                CreateTransactionDto(
                    user_id=actor.id,
                    plan_id=None,
                    plan_snapshot=PlanSnapshot(
                        plan_id=0,
                        public_code="test",
                        name="Test payment",
                        price_paid=amount,
                        currency=currency,
                        duration_days=0,
                        traffic_limit=0,
                        device_limit=0,
                        traffic_limit_strategy=TrafficLimitStrategy.NO_RESET,
                        is_trial=False,
                    ),
                    duration_days=0,
                    currency=currency,
                    purchase_type=PurchaseType.NEW,
                    # whichever shop is tested: a BOT order would get the bot's outcome messages
                    channel=PurchaseChannel.CABINET,
                    target_subscription_id=None,
                    gateway_type=data,
                    gateway_account=account,
                    base_price=amount,
                    discount_percent=0,
                    discount_source=DiscountSource.NONE,
                    final_price=amount,
                    anchor_base_price=anchor_amount,
                    anchor_final_price=anchor_amount,
                    customer_email=customer_email,
                    idempotency_key=uuid4().hex,
                    expires_at=datetime_now() + _TEST_ORDER_TTL,
                    is_test=True,
                )
            )

        try:
            text = await self._payment_texts.describe(order, actor.language)
            intent = await self._gateway_factory.build(data, settings).create_payment(order, text)
        except PaymentGatewayError:
            logger.exception(f"Test payment failed on gateway '{data.value}'")
            raise
        except Exception as error:  # surfaced to the admin, not a 500
            logger.exception(f"Test payment failed on gateway '{data.value}'")
            raise PaymentGatewayError(f"Provider request failed: {type(error).__name__}") from error

        async with self._uow:
            await self._transaction_repository.mark_status(
                order.id,
                TransactionStatus.PENDING,
                external_payment_id=intent.provider_payment_id,
            )
            await self._event_publisher.publish(
                GatewayTestPaymentCreatedEvent(actor=actor, order_id=order.id, gateway_type=data)
            )
        logger.info(f"{actor.log} Created test transaction '{order.id}' on gateway '{data.value}'")
        return TestPaymentDto(order_id=order.id, confirmation_url=intent.confirmation_url)

    async def _with_bot_settings(self, actor: UserDto, settings: dict[str, str]) -> dict[str, str]:
        # pre-checkout matches the payer's telegram_id against the order's user
        if actor.telegram_id is None:
            raise ValidationError("No Telegram linked", code=ErrorCode.GATEWAY_TEST_NO_TELEGRAM)
        bot_settings = await self._settings_repository.get_bot_settings()
        if not bot_settings.is_configured or bot_settings.token is None:
            raise ValidationError("The bot is not configured", code=ErrorCode.GATEWAY_NOT_AVAILABLE)
        return with_bot_settings(
            settings, bot_settings.token.get_secret_value(), bot_settings.proxy
        )
