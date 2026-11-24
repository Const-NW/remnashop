import time
from dataclasses import replace
from typing import Any, Final, Optional

import httpx
import orjson

from app.core.enums import Currency, TransactionStatus
from app.core.exceptions import PaymentGatewayError
from app.modules.gateways import YooKassaSettings
from app.modules.transactions.dto import PaymentTextDto, TransactionDto, WebhookRequest
from app.modules.transactions.gateways.base import (
    NOT_PAID,
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
    to_decimal,
)

_API_BASE: Final[str] = "https://api.yookassa.ru/v3"
# paygate mirrors /v3 under this path and signs what it forwards
_RELAY_API_PATH: Final[str] = "/agg/v1"
_RELAY_TIMESTAMP_HEADER: Final[str] = "x-paygate-timestamp"
_RELAY_SIGNATURE_HEADER: Final[str] = "x-paygate-signature"
_RELAY_MAX_SKEW_S: Final[int] = 300
# the gateway_account of an order paid through the relay
RELAY_ACCOUNT: Final[str] = "relay"
_PAID_STATUS: Final[str] = "succeeded"
_CANCELED_STATUS: Final[str] = "canceled"
_PAID_EVENT: Final[str] = "payment.succeeded"
_REPORTED_EVENTS: Final[dict[str, TransactionStatus]] = {
    "payment.canceled": TransactionStatus.CANCELED,
    "refund.succeeded": TransactionStatus.REFUNDED,
}
_REFUND_EVENT_PREFIX: Final[str] = "refund."
_PAYMENT_SUBJECT: Final[str] = "service"
_PAYMENT_MODE: Final[str] = "full_payment"


def _build_receipt(
    order: TransactionDto, text: PaymentTextDto, settings: YooKassaSettings
) -> Optional[dict[str, object]]:
    if not order.customer_email or settings.vat_code is None:
        return None
    item: dict[str, object] = {
        "description": text.description,
        "quantity": "1.00",
        "amount": {"value": f"{order.final_price:.2f}", "currency": order.currency.value},
        "vat_code": int(settings.vat_code),
        "payment_subject": _PAYMENT_SUBJECT,
        "payment_mode": _PAYMENT_MODE,
    }
    return {"customer": {"email": order.customer_email}, "items": [item]}


# Docs: https://yookassa.ru/developers
class YooKassaGateway(PaymentGateway[YooKassaSettings]):
    settings_model = YooKassaSettings
    currency = Currency.RUB
    # https://yookassa.ru/developers/using-api/webhooks#ip
    trusted_networks = (
        "77.75.153.0/25",
        "77.75.156.11/32",
        "77.75.156.35/32",
        "77.75.154.128/25",
        "185.71.76.0/27",
        "185.71.77.0/27",
        "2a02:5180:0:1509::/64",
        "2a02:5180:0:2655::/64",
        "2a02:5180:0:1533::/64",
        "2a02:5180:0:2669::/64",
    )

    @classmethod
    def requires_customer_email(cls, settings: dict[str, str]) -> bool:
        # asked before checkout checks the form is complete, so an incomplete one is no receipt
        try:
            return YooKassaSettings.from_values(settings).vat_code is not None
        except PaymentGatewayError:
            return False

    @classmethod
    def account_for(cls, settings: dict[str, str], *, in_telegram: bool) -> Optional[str]:
        parsed = YooKassaSettings.from_values(settings)
        if parsed.relay_enabled:
            return RELAY_ACCOUNT
        if parsed.cabinet_shop_enabled and not in_telegram:
            return parsed.cabinet_shop_id
        return parsed.shop_id

    def is_signed_notification(self, request: WebhookRequest) -> bool:
        # relayed: parse_webhook checks paygate's signature instead of the source IP;
        # by header, not the current mode, so orders of the other mode still settle
        if _RELAY_SIGNATURE_HEADER not in request.headers:
            return False
        try:
            return self.settings.relay_forward_secret is not None
        except PaymentGatewayError:
            return False

    def _auth(self, account: Optional[str]) -> tuple[str, str]:
        settings = self.settings
        # every stored shop, switched on or not: an order is checked in the shop it was paid into;
        # None: an order from before the shops were split, paid into the main one
        if account is None or account == settings.shop_id:
            shop_id, api_key = settings.shop_id, settings.api_key
        elif account == settings.cabinet_shop_id:
            shop_id, api_key = settings.cabinet_shop_id, settings.cabinet_api_key
        else:
            shop_id, api_key = None, None
        if shop_id is None or api_key is None:
            raise PaymentGatewayError(f"YooKassa shop '{account}' is no longer configured")
        return shop_id, api_key.get_secret_value()

    async def _request(
        self,
        method: str,
        path: str,
        account: Optional[str],
        *,
        body: Optional[dict[str, object]] = None,
        headers: Optional[dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        settings = self.settings
        content = orjson.dumps(body) if body is not None else b""
        request_headers = dict(headers or {})
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        auth: Optional[tuple[str, str]] = None
        if account == RELAY_ACCOUNT:
            if settings.relay_url is None or settings.relay_ingress_secret is None:
                raise PaymentGatewayError("YooKassa relay is no longer configured")
            url = f"{settings.relay_url.rstrip('/')}{_RELAY_API_PATH}{path}"
            timestamp = str(int(time.time()))
            request_headers["X-Timestamp"] = timestamp
            request_headers["X-Signature"] = hmac_hex(
                settings.relay_ingress_secret.get_secret_value(),
                f"{timestamp}.".encode() + content,
            )
        else:
            url = f"{_API_BASE}{path}"
            auth = self._auth(account)
        response = await self._http.request(
            method,
            url,
            content=content or None,
            auth=auth,
            headers=request_headers,
            timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
        )
        response.raise_for_status()
        data: dict[str, Any] = orjson.loads(response.content)
        return data

    def _verify_relay_signature(self, request: WebhookRequest) -> None:
        secret = self.settings.relay_forward_secret
        if secret is None:
            raise PaymentGatewayError("YooKassa relay webhook without a forward secret set")
        timestamp = request.headers.get(_RELAY_TIMESTAMP_HEADER, "")
        signature = request.headers.get(_RELAY_SIGNATURE_HEADER, "")
        if (
            not (timestamp.isascii() and timestamp.isdigit())
            or abs(time.time() - int(timestamp)) > _RELAY_MAX_SKEW_S
        ):
            raise PaymentGatewayError("YooKassa relay webhook has a stale or missing timestamp")
        expected = hmac_hex(
            secret.get_secret_value(),
            f"{timestamp}.".encode() + request.body,
        )
        if not safe_equals(signature, expected, ignore_case=True):
            raise PaymentGatewayError("YooKassa relay webhook signature mismatch")

    async def create_payment(self, order: TransactionDto, text: PaymentTextDto) -> PaymentIntent:
        body: dict[str, object] = {
            "amount": {"value": f"{order.final_price:.2f}", "currency": order.currency.value},
            "confirmation": {"type": "redirect", "return_url": self._return_url},
            "capture": True,
            "description": text.description,
            "metadata": {"order_ref": order.idempotency_key},
        }
        receipt = _build_receipt(order, text, self.settings)
        if receipt is not None:
            body["receipt"] = receipt

        data = await self._request(
            "POST",
            "/payments",
            order.gateway_account,
            body=body,
            headers={"Idempotence-Key": order.idempotency_key},
        )
        return PaymentIntent(
            confirmation_url=str(data["confirmation"]["confirmation_url"]),
            provider_payment_id=str(data["id"]),
        )

    async def parse_webhook(self, request: WebhookRequest) -> Optional[WebhookSignal]:
        # YooKassa doesn't sign notifications (fetch_payment is the check); paygate does
        if self.is_signed_notification(request):
            self._verify_relay_signature(request)
        payload = parse_json(request.body, "YooKassa")
        obj = payload.get("object")
        if not isinstance(obj, dict):
            raise PaymentGatewayError("Malformed YooKassa webhook")
        event = str(payload.get("event"))
        metadata = obj.get("metadata") or {}
        # a refund object carries its payment under payment_id; its own id is the refund's
        payment_id = (
            obj.get("payment_id") if event.startswith(_REFUND_EVENT_PREFIX) else obj.get("id")
        )
        refund_amount = obj.get("amount") if event.startswith(_REFUND_EVENT_PREFIX) else None
        refund_amount = refund_amount if isinstance(refund_amount, dict) else {}
        return WebhookSignal(
            reference=str(metadata.get("order_ref", "")),
            claims_paid=event == _PAID_EVENT,
            provider_payment_id=str(payment_id) if payment_id else None,
            reported_status=_REPORTED_EVENTS.get(event),
            refund_amount=to_decimal(refund_amount.get("value")),
            refund_currency=refund_amount.get("currency"),
        )

    async def fetch_payment(self, order: TransactionDto, signal: WebhookSignal) -> ProviderPayment:
        if not order.external_payment_id:
            return NOT_PAID
        data = await self._request(
            "GET",
            f"/payments/{order.external_payment_id}",
            order.gateway_account,
            timeout=PROVIDER_API_TIMEOUT_S,
        )
        amount = data.get("amount") or {}
        return ProviderPayment(
            paid=data.get("status") == _PAID_STATUS and data.get("paid") is True,
            amount=to_decimal(amount.get("value")),
            currency=amount.get("currency"),
            reference=str((data.get("metadata") or {}).get("order_ref") or ""),
            provider_payment_id=str(data.get("id") or order.external_payment_id),
        )

    async def confirm_reported(
        self, order: TransactionDto, signal: WebhookSignal
    ) -> Optional[WebhookSignal]:
        # notifications are unsigned, and a refund also claws back referral rewards
        if not order.external_payment_id:
            return None
        data = await self._request(
            "GET",
            f"/payments/{order.external_payment_id}",
            order.gateway_account,
            timeout=PROVIDER_API_TIMEOUT_S,
        )
        if signal.reported_status is TransactionStatus.REFUNDED:
            refunded = data.get("refunded_amount") or {}
            amount = to_decimal(refunded.get("value"))
            if not amount:
                return None
            return replace(signal, refund_amount=amount, refund_currency=refunded.get("currency"))
        return signal if data.get("status") == _CANCELED_STATUS else None
