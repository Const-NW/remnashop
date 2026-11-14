from app.core.enums import Locale
from app.modules.transactions.dto import PaymentTextDto, TransactionDto
from tests.modules.transactions.gateways.support import PAYMENT_TEXT


class FixedPaymentTexts:
    async def describe(self, order: TransactionDto, locale: Locale) -> PaymentTextDto:
        return PAYMENT_TEXT
