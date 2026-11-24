import base64
from typing import Any, Final, Optional

from app.core.enums import Currency, TransactionStatus
from app.core.exceptions import PaymentGatewayError
from app.modules.gateways import Pay2328Settings
from app.modules.transactions.dto import PaymentTextDto, TransactionDto, WebhookRequest
from app.modules.transactions.gateways.base import (
    PaymentGateway,
    PaymentIntent,
    ProviderPayment,
    WebhookSignal,
)
from app.modules.transactions.gateways.common import (
    PROVIDER_API_TIMEOUT_S,
    compact_json,
    hmac_hex,
    link_lifetime,
    parse_json,
    safe_equals,
    to_decimal,
)

_API_BASE: Final[str] = "https://api.2328.io/api/v1"
_PAID_STATUSES: Final[frozenset[str]] = frozenset({"paid", "overpaid"})
_REPORTED_STATUSES: Final[dict[str, TransactionStatus]] = {
    "cancel": TransactionStatus.CANCELED,
    "underpaid": TransactionStatus.FAILED,
    "aml_lock": TransactionStatus.FAILED,
    "refund_paid": TransactionStatus.REFUNDED,
}


# Docs: https://2328.io/api/payments
class Pay2328Gateway(PaymentGateway[Pay2328Settings]):
    settings_model = Pay2328Settings
    currency = Currency.RUB

    def _sign(self, body: bytes) -> str:
        return hmac_hex(self.settings.api_key.get_secret_value(), base64.b64encode(body))

    async def _request(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = compact_json(payload)
        response = await self._http.post(
            f"{_API_BASE}/{endpoint}",
            content=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Remnashop (+https://github.com/remnashop/remnashop)",
                "project": self.settings.project_uuid,
                "sign": self._sign(body),
            },
            timeout=PROVIDER_API_TIMEOUT_S,
        )
        response.raise_for_status()
        data = parse_json(response.content, "2328.io")
        if type(data.get("state")) is not int or data["state"] != 0:
            raise PaymentGatewayError("2328.io API returned an unsuccessful state")
        result = data.get("result")
        if not isinstance(result, dict):
            raise PaymentGatewayError("2328.io API response missing result")
        return result

    async def create_payment(self, order: TransactionDto, text: PaymentTextDto) -> PaymentIntent:
        result = await self._request(
            "payment",
            {
                "amount": f"{order.final_price:.2f}",
                "currency": order.currency.value,
                "order_id": order.idempotency_key,
                "url_return": self._return_url,
                "url_success": self._return_url,
                "url_callback": self._webhook_url,
                "description": text.description[:500],
                "ttl_seconds": link_lifetime(order, unit="seconds", minimum=300, maximum=86400),
            },
        )
        if (
            result.get("order_id") != order.idempotency_key
            or to_decimal(result.get("amount")) != order.final_price
            or result.get("currency") != order.currency.value
        ):
            raise PaymentGatewayError("2328.io invoice does not match the order")
        if not isinstance(result.get("uuid"), str) or not result["uuid"]:
            raise PaymentGatewayError("2328.io response missing payment UUID")
        if not isinstance(result.get("url"), str) or not result["url"]:
            raise PaymentGatewayError("2328.io response missing payment URL")
        return PaymentIntent(confirmation_url=result["url"], provider_payment_id=result["uuid"])

    async def parse_webhook(self, request: WebhookRequest) -> Optional[WebhookSignal]:
        data = parse_json(request.body, "2328.io")
        signature = data.pop("sign", None)
        if not isinstance(signature, str) or not safe_equals(
            self._sign(compact_json(data)), signature, ignore_case=True
        ):
            raise PaymentGatewayError("Invalid 2328.io webhook signature")
        reference, payment_id = data.get("order_id"), data.get("uuid")
        if not isinstance(reference, str) or not reference:
            raise PaymentGatewayError("2328.io webhook missing order_id")
        if not isinstance(payment_id, str) or not payment_id:
            raise PaymentGatewayError("2328.io webhook missing payment UUID")
        status = str(data.get("payment_status"))
        return WebhookSignal(
            reference=reference,
            provider_payment_id=payment_id,
            claims_paid=status in _PAID_STATUSES,
            reported_status=_REPORTED_STATUSES.get(status),
        )

    async def fetch_payment(self, order: TransactionDto, signal: WebhookSignal) -> ProviderPayment:
        result = await self._request("payment/info", {"order_id": order.idempotency_key})
        amount = to_decimal(result.get("amount"))
        currency = result.get("currency")
        payment_id = result.get("uuid")
        return ProviderPayment(
            paid=result.get("payment_status") in _PAID_STATUSES
            and amount is not None
            and isinstance(currency, str)
            and bool(currency)
            and isinstance(payment_id, str)
            and bool(payment_id)
            and (not order.external_payment_id or payment_id == order.external_payment_id)
            and (not signal.provider_payment_id or payment_id == signal.provider_payment_id),
            amount=amount,
            currency=currency if isinstance(currency, str) else None,
            reference=str(result.get("order_id") or ""),
            provider_payment_id=payment_id if isinstance(payment_id, str) else None,
        )

    async def confirm_reported(
        self, order: TransactionDto, signal: WebhookSignal
    ) -> Optional[WebhookSignal]:
        result = await self._request("payment/info", {"order_id": order.idempotency_key})
        if (
            result.get("order_id") != order.idempotency_key
            or result.get("uuid") != signal.provider_payment_id
            or (order.external_payment_id and result.get("uuid") != order.external_payment_id)
            or _REPORTED_STATUSES.get(str(result.get("payment_status"))) != signal.reported_status
        ):
            return None
        return signal
