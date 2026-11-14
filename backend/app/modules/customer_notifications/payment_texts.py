from typing import Final

from app.core.config import AppConfig
from app.core.enums import Locale
from app.core.utils.time import period_parts
from app.modules.notifications import NotificationRenderer, NotificationRendererFactory
from app.modules.transactions import PaymentTextDto, TransactionDto

_DESCRIPTION_KEY: Final[str] = "payment-description"
_PERIOD_KEY: Final[str] = "payment-period"
# where the fiscal receipt's Russian comes from when the shop has Russian switched off
_SHIPPED_RU_FILE: Final[str] = "ru/notifications.ftl"

# compiled once: the shipped file changes only with a release
_SHIPPED: Final[dict[Locale, NotificationRenderer]] = {}


class PaymentTextRenderer:
    """transactions' PaymentTexts: the order as the customer's payment page names it."""

    def __init__(self, renderer_factory: NotificationRendererFactory, config: AppConfig) -> None:
        self._renderer_factory = renderer_factory
        self._config = config

    async def describe(self, order: TransactionDto, locale: Locale) -> PaymentTextDto:
        snapshot = order.plan_snapshot
        if order.is_test:
            return PaymentTextDto(
                description=snapshot.name, title=snapshot.name, localized_description=snapshot.name
            )
        renderer = await self._renderer_factory.build()
        unit, value = period_parts(order.duration_days)

        def render(source: NotificationRenderer, target: Locale) -> str:
            name = snapshot.display_name(target)
            period = source.render_message(target, _PERIOD_KEY, {"unit": unit, "value": value})
            text = source.render_message(target, _DESCRIPTION_KEY, {"plan": name, "period": period})
            # a key broken in the translations editor must not leave a provider an empty text
            return text.strip() or name

        russian = renderer if renderer.has_locale(Locale.RU) else self._shipped_russian()
        return PaymentTextDto(
            # the fiscal receipt is in Russian whatever the customer reads
            description=render(russian, Locale.RU),
            title=snapshot.display_name(locale),
            localized_description=render(renderer, locale),
        )

    def _shipped_russian(self) -> NotificationRenderer:
        if Locale.RU not in _SHIPPED:
            text = (self._config.translations_dir / _SHIPPED_RU_FILE).read_text(encoding="utf-8")
            _SHIPPED[Locale.RU] = NotificationRenderer({Locale.RU: text}, default_locale=Locale.RU)
        return _SHIPPED[Locale.RU]
