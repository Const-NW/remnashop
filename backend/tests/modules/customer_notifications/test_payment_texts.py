from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

import pytest

from app.core.enums import Locale
from app.modules.customer_notifications.payment_texts import PaymentTextRenderer
from app.modules.notifications.render import NotificationRenderer
from app.modules.transactions.dto import TransactionDto
from tests.modules.subscriptions.factories import make_plan_snapshot
from tests.modules.transactions.gateways.support import make_order

_ASSETS: Final[Path] = Path(__file__).parents[3] / "assets" / "translations"


def _shipped(locale: Locale) -> str:
    return "\n".join(path.read_text() for path in sorted((_ASSETS / locale).glob("*.ftl")))


@dataclass
class _Config:
    translations_dir: Path = _ASSETS


class _Factory:
    def __init__(self, texts: dict[Locale, str]) -> None:
        self._texts = texts

    async def build(self) -> NotificationRenderer:
        return NotificationRenderer(self._texts, default_locale=Locale.EN)


def _renderer(texts: dict[Locale, str]) -> PaymentTextRenderer:
    return PaymentTextRenderer(_Factory(texts), _Config())  # type: ignore[arg-type]


def _shop() -> PaymentTextRenderer:
    return _renderer({locale: _shipped(locale) for locale in (Locale.RU, Locale.EN)})


def _order(days: int = 30) -> TransactionDto:
    snapshot = replace(make_plan_snapshot(), name="Basic", names={Locale.RU: "Базовый"})
    return make_order(plan_snapshot=snapshot, duration_days=days)


@pytest.mark.parametrize(
    ("days", "period"),
    [(7, "7 дней"), (30, "1 месяц"), (90, "3 месяца"), (365, "1 год"), (0, "навсегда")],
)
async def test_the_receipt_text_is_russian(days: int, period: str) -> None:
    text = await _shop().describe(_order(days), Locale.EN)

    assert text.description == f"Подписка «Базовый», {period}"


async def test_stars_gets_the_customers_language() -> None:
    en = await _shop().describe(_order(), Locale.EN)
    ru = await _shop().describe(_order(), Locale.RU)

    assert (en.title, en.localized_description) == ("Basic", "Subscription “Basic”, 1 month")
    assert (ru.title, ru.localized_description) == ("Базовый", "Подписка «Базовый», 1 месяц")


async def test_the_receipt_stays_russian_in_a_shop_without_russian() -> None:
    text = await _renderer({Locale.EN: _shipped(Locale.EN)}).describe(_order(), Locale.EN)

    assert text.description == "Подписка «Базовый», 1 месяц"
    assert text.localized_description == "Subscription “Basic”, 1 month"


async def test_a_broken_key_falls_back_to_the_plan_name() -> None:
    text = await _renderer({Locale.RU: "other = x", Locale.EN: "other = x"}).describe(
        _order(), Locale.EN
    )

    assert (text.description, text.localized_description) == ("Базовый", "Basic")


async def test_a_test_payment_is_named_by_its_plan_only() -> None:
    order = make_order(
        plan_snapshot=replace(make_plan_snapshot(), name="Test payment"), is_test=True
    )

    text = await _shop().describe(order, Locale.RU)

    assert text.description == text.title == text.localized_description == "Test payment"
