import hashlib
import hmac
import json
from decimal import Decimal
from typing import Any

import pytest

from app.core.enums import Currency, TransactionStatus
from app.core.exceptions import PaymentGatewayError
from app.modules.transactions.gateways.base import PaymentHttpClient, WebhookSignal
from app.modules.transactions.gateways.cispay import CisPayGateway
from tests.modules.transactions.gateways.support import (
    NO_HTTP,
    ORDER_REF,
    PAYMENT_TEXT,
    RETURN_URL,
    WEBHOOK_URL,
    HttpRecorder,
    json_response,
    make_order,
    webhook,
)

_SETTINGS = {"shop_id": "shop-uuid", "api_key": "cis_sec_key"}
_SIGNAL = WebhookSignal(reference=ORDER_REF, claims_paid=True, provider_payment_id="tx-1")


def _gateway(*, http: PaymentHttpClient = NO_HTTP) -> CisPayGateway:
    return CisPayGateway(
        settings=_SETTINGS, webhook_url=WEBHOOK_URL, return_url=RETURN_URL, http=http
    )


def _signed(body: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(body, separators=(",", ":")).encode()
    signature = hmac.new(b"cis_sec_key", raw, hashlib.sha256).hexdigest()
    return raw, {"X-Signature": signature}


def _status(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "tx-1",
        "order_id": ORDER_REF,
        "status": "PAID",
        "amount": 50000,
        "charged_amount": 50000,
        "currency": "RUB",
        "is_sandbox": False,
    }
    data.update(overrides)
    return data


async def test_create_payment_sends_kopecks_and_auth_headers() -> None:
    http = HttpRecorder(json_response({"id": "tx-1", "payment_url": "https://pay.cispay/1"}))

    intent = await _gateway(http=http.client).create_payment(
        make_order(final_price=Decimal("150.49")), PAYMENT_TEXT
    )

    payload = json.loads(http.last.content)
    assert http.last.url.path == "/payments"
    assert http.last.headers["X-Shop-ID"] == "shop-uuid"
    assert http.last.headers["X-Api-Key"] == "cis_sec_key"
    assert payload["amount"] == 15049
    assert payload["order_id"] == ORDER_REF
    assert payload["customer_id"] == "7"
    assert "payment_method" not in payload
    assert intent.provider_payment_id == "tx-1"
    assert intent.confirmation_url == "https://pay.cispay/1"


async def test_parse_webhook_checks_the_signature() -> None:
    raw, headers = _signed({"id": "tx-1", "order_id": ORDER_REF, "status": "PAID"})

    signal = await _gateway().parse_webhook(webhook(raw, headers))

    assert signal is not None and signal.claims_paid is True
    assert signal.reference == ORDER_REF
    assert signal.provider_payment_id == "tx-1"

    with pytest.raises(PaymentGatewayError):
        await _gateway().parse_webhook(webhook(raw, {"X-Signature": "0" * 64}))
    with pytest.raises(PaymentGatewayError):
        await _gateway().parse_webhook(webhook(raw))


@pytest.mark.parametrize(
    ("status", "reported"),
    [
        ("FAILED", TransactionStatus.FAILED),
        ("EXPIRED", TransactionStatus.CANCELED),
        ("REFUNDED", TransactionStatus.REFUNDED),
    ],
)
async def test_parse_webhook_reports_non_paid_statuses(
    status: str, reported: TransactionStatus
) -> None:
    raw, headers = _signed({"id": "tx-1", "order_id": ORDER_REF, "status": status})
    signal = await _gateway().parse_webhook(webhook(raw, headers))
    assert signal is not None and signal.claims_paid is False
    assert signal.reported_status is reported


async def test_parse_webhook_ignores_recurring_charges() -> None:
    raw, headers = _signed(
        {"id": "tx-2", "order_id": "sub-charge", "status": "PAID", "subscription_id": "sub-1"}
    )
    assert await _gateway().parse_webhook(webhook(raw, headers)) is None


async def test_parse_webhook_ignores_subscription_events() -> None:
    raw, headers = _signed(
        {"event": "subscription.cancelled", "object": "subscription", "id": "sub-1"}
    )
    assert await _gateway().parse_webhook(webhook(raw, headers)) is None


async def test_confirm_reads_the_status_by_order_id() -> None:
    http = HttpRecorder(json_response(_status()))

    confirmed = await _gateway(http=http.client).confirm(make_order(), _SIGNAL)

    assert confirmed is not None and confirmed.amount == Decimal("500")
    assert http.last.url.path == "/payments/status"
    assert http.last.url.params["order_id"] == ORDER_REF


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "PENDING"},
        {"is_sandbox": True},
        {"block_reason": "fraud"},
        {"amount": 49999},
        {"order_id": "other"},
    ],
)
async def test_confirm_rejects(overrides: dict[str, Any]) -> None:
    http = HttpRecorder(json_response(_status(**overrides)))
    assert await _gateway(http=http.client).confirm(make_order(), _SIGNAL) is None


async def test_confirm_uses_the_rub_source_of_a_crypto_payment() -> None:
    http = HttpRecorder(
        json_response(
            _status(amount=550, currency="USD", source_currency="RUB", source_amount=50000)
        )
    )
    confirmed = await _gateway(http=http.client).confirm(make_order(), _SIGNAL)
    assert confirmed is not None and confirmed.amount == Decimal("500")


async def test_a_usd_order_is_sent_and_confirmed_in_cents() -> None:
    order = make_order(currency=Currency.USD, final_price=Decimal("5.50"))
    http = HttpRecorder(json_response({"id": "tx-1", "payment_url": "https://pay.cispay/1"}))
    await _gateway(http=http.client).create_payment(order, PAYMENT_TEXT)
    payload = json.loads(http.last.content)
    assert (payload["amount"], payload["currency"]) == (550, "USD")

    http = HttpRecorder(json_response(_status(amount=550, currency="USD")))
    assert await _gateway(http=http.client).confirm(order, _SIGNAL) is not None
