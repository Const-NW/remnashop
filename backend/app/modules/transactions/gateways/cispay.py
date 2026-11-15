from decimal import Decimal
from typing import Final, Optional

import orjson

from app.core.enums import Currency, TransactionStatus
from app.core.exceptions import PaymentGatewayError
from app.modules.gateways import CisPaySettings
from app.modules.transactions.dto import PaymentTextDto, TransactionDto, WebhookRequest
from app.modules.transactions.gateways.base import (
    PaymentGateway,
    PaymentIntent,
    ProviderPayment,
    WebhookSignal,
)
from app.modules.transactions.gateways.common import (
    PROVIDER_API_TIMEOUT_S,
    hmac_hex,
    parse_json,
    safe_equals,
)

_API_BASE: Final[str] = "https://api.cispay.app"
_PAID_STATUS: Final[str] = "PAID"
_REPORTED_STATUSES: Final[dict[str, TransactionStatus]] = {
    "FAILED": TransactionStatus.FAILED,
    "EXPIRED": TransactionStatus.CANCELED,
    "REFUNDED": TransactionStatus.REFUNDED,
}
_MINOR_UNITS: Final[Decimal] = Decimal(100)


def _from_minor(value: object) -> Optional[Decimal]:
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return Decimal(value) / _MINOR_UNITS


# Docs: https://cispay.app/developers
class CisPayGateway(PaymentGateway[CisPaySettings]):
    settings_model = CisPaySettings
    currency = Currency.RUB

    def _headers(self) -> dict[str, str]:
        return {
            "X-Shop-ID": self.settings.shop_id,
            "X-Api-Key": self.settings.api_key.get_secret_value(),
        }

    async def create_payment(self, order: TransactionDto, text: PaymentTextDto) -> PaymentIntent:
        # no payment_method: a RUB order lets the customer pick SBP, card or
        # crypto on the cisPay page (customer_id is then required), a USD one
        # is crypto; amount is in kopecks or cents
        payload: dict[str, object] = {
            "amount": int((order.final_price * _MINOR_UNITS).to_integral_value()),
            "currency": order.currency.value,
            "order_id": order.idempotency_key,
            "customer_id": str(order.user_id),
            "description": text.description,
            "redirect_success_url": self._return_url,
            "redirect_fail_url": self._return_url,
        }
        response = await self._http.post(
            f"{_API_BASE}/payments", json=payload, headers=self._headers()
        )
        response.raise_for_status()
        data = orjson.loads(response.content)

        payment_id = data.get("id")
        payment_url = data.get("payment_url")
        if not payment_id or not payment_url:
            raise PaymentGatewayError("CisPay response missing 'id'/'payment_url'")
        return PaymentIntent(confirmation_url=str(payment_url), provider_payment_id=str(payment_id))

    async def parse_webhook(self, request: WebhookRequest) -> Optional[WebhookSignal]:
        signature = request.headers.get("x-signature", "")
        expected = hmac_hex(self.settings.api_key.get_secret_value(), request.body)
        if not signature or not safe_equals(expected, signature, ignore_case=True):
            raise PaymentGatewayError("Invalid CisPay webhook signature")

        data = parse_json(request.body, "CisPay")
        # a subscription lifecycle event or a recurring charge; the shop creates neither
        if data.get("event") or data.get("subscription_id"):
            return None
        payment_id = data.get("id")
        status = data.get("status")
        if not payment_id or not status:
            raise PaymentGatewayError("CisPay webhook missing 'id'/'status'")
        return WebhookSignal(
            reference=str(data.get("order_id") or ""),
            claims_paid=status == _PAID_STATUS,
            provider_payment_id=str(payment_id),
            reported_status=_REPORTED_STATUSES.get(str(status)),
        )

    async def fetch_payment(self, order: TransactionDto, signal: WebhookSignal) -> ProviderPayment:
        response = await self._http.get(
            f"{_API_BASE}/payments/status",
            params={"order_id": order.idempotency_key},
            headers=self._headers(),
            timeout=PROVIDER_API_TIMEOUT_S,
        )
        response.raise_for_status()
        data = orjson.loads(response.content)
        # a RUB order paid in crypto is re-denominated in USD cents; the
        # original kopecks come back as source_amount
        source_currency = data.get("source_currency")
        return ProviderPayment(
            # a blocked customer's payment is never real, whatever the status says
            paid=data.get("status") == _PAID_STATUS
            and not data.get("is_sandbox")
            and not data.get("block_reason"),
            amount=_from_minor(data.get("source_amount" if source_currency else "amount")),
            currency=source_currency or data.get("currency"),
            reference=str(data.get("order_id") or ""),
            provider_payment_id=str(data["id"]) if data.get("id") else order.external_payment_id,
        )
