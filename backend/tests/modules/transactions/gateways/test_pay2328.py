import base64
import hashlib
import hmac
import json
from typing import Any, Final

import pytest

from app.core.enums import TransactionStatus
from app.core.exceptions import PaymentGatewayError
from app.modules.transactions.gateways.base import PaymentHttpClient, WebhookSignal
from app.modules.transactions.gateways.pay2328 import Pay2328Gateway
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

_SETTINGS: Final[dict[str, str]] = {"project_uuid": "project", "api_key": "test-secret"}
_SIGNAL: Final[WebhookSignal] = WebhookSignal(
    reference=ORDER_REF, claims_paid=True, provider_payment_id="invoice"
)


def _gateway(http: PaymentHttpClient = NO_HTTP) -> Pay2328Gateway:
    return Pay2328Gateway(
        settings=_SETTINGS, webhook_url=WEBHOOK_URL, return_url=RETURN_URL, http=http
    )


def _sign(body: bytes) -> str:
    return hmac.new(b"test-secret", base64.b64encode(body), hashlib.sha256).hexdigest()


def _payload(**overrides: Any) -> dict[str, Any]:
    return {
        "uuid": "invoice",
        "order_id": ORDER_REF,
        "amount": "500.00",
        "currency": "RUB",
        "payment_status": "paid",
        "url": "https://2328.io/pay/invoice",
        **overrides,
    }


def _signed(data: dict[str, Any]) -> bytes:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    return json.dumps({**data, "sign": _sign(raw)}, ensure_ascii=False).encode()


async def test_create_reuses_order_key_and_signs_exact_body() -> None:
    http = HttpRecorder(json_response({"state": 0, "result": _payload()}))
    gateway = _gateway(http.client)
    order = make_order()
    for _ in range(2):
        intent = await gateway.create_payment(order, PAYMENT_TEXT)
        assert intent.provider_payment_id == "invoice"
        assert intent.confirmation_url == "https://2328.io/pay/invoice"
        assert http.last.url.path == "/api/v1/payment"
        assert http.last.headers["sign"] == _sign(http.last.content)
        assert http.last.headers["project"] == "project"
        assert "Remnashop" in http.last.headers["user-agent"]
        body = http.last_json()
        assert body["order_id"] == ORDER_REF
        assert body["amount"] == "500.00"
        assert body["url_callback"] == WEBHOOK_URL
        assert body["description"] == PAYMENT_TEXT.description
        assert 300 <= body["ttl_seconds"] <= 86400


@pytest.mark.parametrize("status", ["paid", "overpaid"])
async def test_confirm_checks_provider_status(status: str) -> None:
    http = HttpRecorder(json_response({"state": 0, "result": _payload(payment_status=status)}))
    assert await _gateway(http.client).confirm(make_order(), _SIGNAL) is not None
    assert http.last.url.path == "/api/v1/payment/info"
    assert http.last_json() == {"order_id": ORDER_REF}
    assert http.last.headers["sign"] == _sign(http.last.content)


@pytest.mark.parametrize(
    "overrides",
    [
        {"payment_status": "check"},
        {"payment_status": "underpaid"},
        {"payment_status": "aml_lock"},
        {"amount": "499.99"},
        {"amount": None},
        {"amount": "NaN"},
        {"currency": "USD"},
        {"currency": None},
        {"order_id": "another-order"},
        {"order_id": None},
        {"uuid": "another-invoice"},
        {"uuid": None},
    ],
)
async def test_confirm_refuses_unverified_payment(overrides: dict[str, Any]) -> None:
    http = HttpRecorder(json_response({"state": 0, "result": _payload(**overrides)}))
    assert await _gateway(http.client).confirm(make_order(), _SIGNAL) is None


@pytest.mark.parametrize(
    "status",
    [
        "pending",
        "check",
        "awaiting_confirmation",
        "underpaid_check",
        "refund_process",
        "refund_fail",
        "unknown",
    ],
)
async def test_pending_notification_does_not_settle(status: str) -> None:
    signal = await _gateway().parse_webhook(webhook(_signed(_payload(payment_status=status))))
    assert signal is not None and not signal.claims_paid and signal.reported_status is None


async def test_webhook_signature_preserves_unicode_slashes_and_order() -> None:
    raw = _signed(_payload(description="Подписка / 中文"))
    signal = await _gateway().parse_webhook(webhook(raw))
    assert signal is not None and signal.claims_paid
    assert signal.reference == ORDER_REF
    tampered = json.loads(raw)
    tampered["amount"] = "999"
    with pytest.raises(PaymentGatewayError):
        await _gateway().parse_webhook(webhook(tampered))


@pytest.mark.parametrize("signature", [None, "", "0" * 64, 123])
async def test_bad_signature_refused(signature: Any) -> None:
    with pytest.raises(PaymentGatewayError):
        await _gateway().parse_webhook(webhook({**_payload(), "sign": signature}))


@pytest.mark.parametrize(
    ("status", "reported"),
    [
        ("cancel", TransactionStatus.CANCELED),
        ("underpaid", TransactionStatus.FAILED),
        ("aml_lock", TransactionStatus.FAILED),
        ("refund_paid", TransactionStatus.REFUNDED),
    ],
)
async def test_terminal_notification_rechecked(status: str, reported: TransactionStatus) -> None:
    payload = _payload(payment_status=status)
    http = HttpRecorder(json_response({"state": 0, "result": payload}))
    gateway = _gateway(http.client)
    signal = await gateway.parse_webhook(webhook(_signed(payload)))
    assert signal is not None and signal.reported_status == reported
    assert await gateway.confirm_reported(make_order(), signal) == signal
    stale = _gateway(HttpRecorder(json_response({"state": 0, "result": _payload()})).client)
    assert await stale.confirm_reported(make_order(), signal) is None


@pytest.mark.parametrize(
    "response", [{"state": 1}, {"state": False, "result": {}}, {"state": 0, "result": []}]
)
async def test_invalid_api_response_refused(response: dict[str, Any]) -> None:
    with pytest.raises(PaymentGatewayError):
        await _gateway(HttpRecorder(json_response(response)).client).check_payment(make_order())


@pytest.mark.parametrize(
    "overrides",
    [
        {"order_id": "other"},
        {"amount": "501"},
        {"currency": "USD"},
        {"url": "http://unsafe"},
        {"uuid": None},
    ],
)
async def test_create_rejects_conflicting_invoice(overrides: dict[str, Any]) -> None:
    http = HttpRecorder(json_response({"state": 0, "result": _payload(**overrides)}))
    with pytest.raises(PaymentGatewayError):
        await _gateway(http.client).create_payment(make_order(), PAYMENT_TEXT)
