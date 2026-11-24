from typing import Final

from app.core.db.uow import UnitOfWork
from app.core.enums import Locale
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.core.utils.telegram_text import premium_emoji_glyphs
from app.modules.gateways import REQUIRE_PAID_PURCHASE_FIELD_KEY, GatewayRepository, gateway_flags
from app.modules.settings import SettingsRepository
from app.modules.transactions.dto import ListPaymentMethodsDto, PaymentMethodDto
from app.modules.transactions.gateways.registry import GATEWAY_REGISTRY, gateway_currency
from app.modules.transactions.repository import TransactionRepository
from app.modules.translations import TranslationRepository
from app.modules.users import UserDto

# edited in the translations editor, so the shop can say "Card / SBP" instead of a brand
_GATEWAY_NAME_MESSAGE: Final[str] = "gateway-name"


class ListPaymentMethodsUseCase(UseCase[ListPaymentMethodsDto, list[PaymentMethodDto], UserDto]):
    required_permission = Permission.PUBLIC

    def __init__(
        self,
        gateway_repository: GatewayRepository,
        transaction_repository: TransactionRepository,
        settings_repository: SettingsRepository,
        translation_repository: TranslationRepository,
        uow: UnitOfWork,
    ) -> None:
        self._gateway_repository = gateway_repository
        self._transaction_repository = transaction_repository
        self._settings_repository = settings_repository
        self._translation_repository = translation_repository
        self._uow = uow

    async def _execute(self, actor: UserDto, data: ListPaymentMethodsDto) -> list[PaymentMethodDto]:
        methods: list[PaymentMethodDto] = []
        async with self._uow:
            shop_settings = await self._settings_repository.get_shop_settings()
            names = await self._gateway_names(actor.language)
            for gateway in await self._gateway_repository.get_all():
                if (
                    not gateway.is_active
                    or not gateway.is_configured
                    or not gateway.offered_in(in_telegram=data.in_telegram)
                ):
                    continue
                gateway_class = GATEWAY_REGISTRY.get(gateway.type)
                if gateway_class is None:
                    continue
                settings = await self._gateway_repository.get_raw_settings(gateway.type)
                if gateway_flags(gateway.type, settings).get(
                    REQUIRE_PAID_PURCHASE_FIELD_KEY, False
                ) and not await self._transaction_repository.has_paid_purchase(actor.id):
                    continue
                methods.append(
                    PaymentMethodDto(
                        gateway_type=gateway.type,
                        currency=gateway_currency(gateway.type, settings),
                        name=names.get(gateway.type, gateway.type.value),
                        # the client decides: ask for an email or hide the method
                        requires_email=shop_settings.payment_email is None
                        and gateway_class.requires_customer_email(settings),
                        asks_email=shop_settings.payment_email is None,
                    )
                )
        return methods

    async def _gateway_names(self, language: Locale) -> dict[str, str]:
        default_locale = await self._translation_repository.get_default_locale()
        entries = await self._translation_repository.list_message(
            _GATEWAY_NAME_MESSAGE, [language, default_locale]
        )
        names: dict[str, str] = {}
        # the customer's language wins; the default fills what it leaves blank
        for locale in (default_locale, language):
            for entry in entries:
                if entry.locale is locale and entry.value.strip():
                    names[entry.attribute_id] = premium_emoji_glyphs(entry.value)
        return names
