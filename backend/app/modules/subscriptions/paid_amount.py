from decimal import Decimal

from app.core.enums import Currency
from app.modules.pricing import PricingRepository


async def paid_in_currency(
    pricing_repository: PricingRepository, amount: Decimal, paid: Currency, target: Currency
) -> Decimal:
    if amount <= 0 or paid is target:
        return amount
    rates = await pricing_repository.get_rate_table()
    return rates.convert(amount, paid, target)
