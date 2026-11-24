import base64
import hashlib
import hmac
import json
import time
from decimal import Decimal
from typing import Final, Optional

import httpx
import pytest

from app.core.enums import PaymentGatewayType, TransactionStatus
from app.core.error_codes import ErrorCode
from app.core.exceptions import PaymentGatewayError, ValidationError
from app.modules.gateways import is_gateway_configured
from app.modules.gateways.field_catalog import (
    GATEWAY_FIELD_CATALOG,
    gateway_settings_error,
    gateway_splits_channels,
    validate_gateway_field_values,
)
from app.modules.transactions.gateways.base import PaymentHttpClient, WebhookSignal
from app.modules.transactions.gateways.yookassa import RELAY_ACCOUNT, YooKassaGateway
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

_SETTINGS = {"shop_id": "1000", "api_key": "test_key"}


def _gateway(*, http: PaymentHttpClient = NO_HTTP) -> YooKassaGateway:
    return YooKassaGateway(
        settings=_SETTINGS, webhook_url=WEBHOOK_URL, return_url=RETURN_URL, http=http
    )


def _payment(**overrides: object) -> dict[str, object]:
    payment: dict[str, object] = {
        "id": "yk-1",
        "status": "succeeded",
        "paid": True,
        "amount": {"value": "500.00", "currency": "RUB"},
        "metadata": {"order_ref": ORDER_REF},
    }
    payment.update(overrides)
    return payment


async def test_create_payment_sends_idempotence_key_and_order_ref() -> None:
    http = HttpRecorder(
        json_response({"id": "yk-1", "confirmation": {"confirmation_url": "https://pay/1"}}),
    )

    intent = await _gateway(http=http.client).create_payment(make_order(), PAYMENT_TEXT)

    assert intent.provider_payment_id == "yk-1"
    assert intent.confirmation_url == "https://pay/1"
    assert http.last.headers["Idempotence-Key"] == ORDER_REF
    assert http.last_json()["metadata"] == {"order_ref": ORDER_REF}


async def test_create_payment_receipt_item_carries_subject_and_mode() -> None:
    http = HttpRecorder(
        json_response({"id": "yk-1", "confirmation": {"confirmation_url": "https://pay/1"}}),
    )
    gateway = YooKassaGateway(
        settings={**_SETTINGS, "vat_code": "1"},
        webhook_url=WEBHOOK_URL,
        return_url=RETURN_URL,
        http=http.client,
    )

    await gateway.create_payment(make_order(customer_email="a@b.ru"), PAYMENT_TEXT)

    body = http.last_json()
    item = body["receipt"]["items"][0]
    assert body["description"] == item["description"] == PAYMENT_TEXT.description
    assert item["payment_subject"] == "service"
    assert item["payment_mode"] == "full_payment"


async def test_parse_webhook_flags_only_payment_succeeded() -> None:
    paid = await _gateway().parse_webhook(
        webhook({"event": "payment.succeeded", "object": _payment()})
    )
    refund = await _gateway().parse_webhook(
        webhook({"event": "refund.succeeded", "object": {"id": "rf-1", "status": "succeeded"}})
    )

    assert paid is not None and paid.claims_paid is True
    assert paid.reference == ORDER_REF
    assert paid.provider_payment_id == "yk-1"
    assert refund is not None and refund.claims_paid is False


async def test_parse_webhook_payment_canceled_reports_canceled() -> None:
    signal = await _gateway().parse_webhook(
        webhook({"event": "payment.canceled", "object": _payment(status="canceled")})
    )
    assert signal is not None and signal.claims_paid is False
    assert signal.reported_status is TransactionStatus.CANCELED


async def test_parse_webhook_rejects_malformed_body() -> None:
    with pytest.raises(PaymentGatewayError):
        await _gateway().parse_webhook(webhook(b"not json"))


async def test_fetch_payment_reads_the_payment_with_the_api_timeout() -> None:
    http = HttpRecorder(json_response(_payment()))
    order = make_order(external_payment_id="yk-1")
    signal = await _gateway(http=http.client).parse_webhook(
        webhook({"event": "payment.succeeded", "object": _payment()})
    )
    assert signal is not None

    confirmed = await _gateway(http=http.client).confirm(order, signal)

    assert confirmed is not None
    assert confirmed.provider_payment_id == "yk-1"
    assert http.last.url.path == "/v3/payments/yk-1"
    assert http.last.extensions["timeout"]["read"] == 8.0


async def test_confirm_rejects_when_the_api_reports_pending() -> None:
    http = HttpRecorder(json_response(_payment(status="pending", paid=False)))
    signal = await _gateway(http=http.client).parse_webhook(
        webhook({"event": "payment.succeeded", "object": _payment()})
    )
    assert signal is not None

    assert (
        await _gateway(http=http.client).confirm(make_order(external_payment_id="yk-1"), signal)
        is None
    )


async def test_fetch_payment_without_a_stored_id_is_not_paid() -> None:
    signal = await _gateway().parse_webhook(
        webhook({"event": "payment.succeeded", "object": _payment()})
    )
    assert signal is not None
    assert await _gateway().confirm(make_order(), signal) is None


async def test_fetch_payment_propagates_http_errors() -> None:
    http = HttpRecorder(lambda request: httpx.Response(500))
    signal = await _gateway(http=http.client).parse_webhook(
        webhook({"event": "payment.succeeded", "object": _payment()})
    )
    assert signal is not None
    with pytest.raises(httpx.HTTPStatusError):
        await _gateway(http=http.client).confirm(make_order(external_payment_id="yk-1"), signal)


async def test_parse_webhook_refund_reports_refunded_against_the_payment() -> None:
    signal = await _gateway().parse_webhook(
        webhook(
            {
                "event": "refund.succeeded",
                "object": {"id": "rf-1", "payment_id": "yk-1", "status": "succeeded"},
            }
        )
    )
    assert signal is not None and signal.claims_paid is False
    assert signal.reported_status is TransactionStatus.REFUNDED
    assert signal.provider_payment_id == "yk-1"


async def test_parse_webhook_refund_carries_the_refund_amount() -> None:
    signal = await _gateway().parse_webhook(
        webhook(
            {
                "event": "refund.succeeded",
                "object": {
                    "id": "rf-1",
                    "payment_id": "yk-1",
                    "amount": {"value": "50.00", "currency": "RUB"},
                },
            }
        )
    )
    assert signal is not None
    assert signal.refund_amount == Decimal("50.00") and signal.refund_currency == "RUB"


def _refund_signal() -> WebhookSignal:
    return WebhookSignal(
        reference="",
        claims_paid=False,
        provider_payment_id="yk-1",
        reported_status=TransactionStatus.REFUNDED,
        refund_amount=Decimal("500.00"),
        refund_currency="RUB",
    )


def _canceled_signal() -> WebhookSignal:
    return WebhookSignal(
        reference="",
        claims_paid=False,
        provider_payment_id="yk-1",
        reported_status=TransactionStatus.CANCELED,
    )


async def test_confirm_reported_ignores_a_refund_the_api_does_not_show() -> None:
    http = HttpRecorder(json_response(_payment()))
    order = make_order(external_payment_id="yk-1")

    assert await _gateway(http=http.client).confirm_reported(order, _refund_signal()) is None
    assert http.last.url.path == "/v3/payments/yk-1"


async def test_confirm_reported_takes_the_refund_amount_from_the_api() -> None:
    http = HttpRecorder(
        json_response(_payment(refunded_amount={"value": "100.00", "currency": "RUB"}))
    )
    order = make_order(external_payment_id="yk-1")

    confirmed = await _gateway(http=http.client).confirm_reported(order, _refund_signal())

    assert confirmed is not None
    assert confirmed.refund_amount == Decimal("100.00") and confirmed.refund_currency == "RUB"


@pytest.mark.parametrize(("api_status", "confirmed"), [("succeeded", False), ("canceled", True)])
async def test_confirm_reported_checks_a_cancel_against_the_api(
    api_status: str, confirmed: bool
) -> None:
    http = HttpRecorder(json_response(_payment(status=api_status, paid=api_status == "succeeded")))
    order = make_order(external_payment_id="yk-1")

    result = await _gateway(http=http.client).confirm_reported(order, _canceled_signal())

    assert (result is not None) is confirmed


async def test_confirm_reported_without_a_stored_payment_id_is_ignored() -> None:
    assert await _gateway().confirm_reported(make_order(), _refund_signal()) is None


async def test_check_payment_finds_the_payment_by_its_stored_id() -> None:
    http = HttpRecorder(json_response(_payment()))

    payment = await _gateway(http=http.client).check_payment(make_order(external_payment_id="yk-1"))

    assert payment is not None and payment.provider_payment_id == "yk-1"
    assert http.last.url.path == "/v3/payments/yk-1"


_CABINET_SETTINGS: Final[dict[str, str]] = {
    **_SETTINGS,
    "cabinet_shop_enabled": "true",
    "cabinet_shop_id": "2000",
    "cabinet_api_key": "cabinet_key",
}
_RELAY_SETTINGS: Final[dict[str, str]] = {
    "relay_enabled": "true",
    "relay_url": "https://agg.example/",
    "relay_ingress_secret": "ingress",
    "relay_forward_secret": "forward",
}


def _basic_auth(shop_id: str, key: str) -> str:
    return "Basic " + base64.b64encode(f"{shop_id}:{key}".encode()).decode()


@pytest.mark.parametrize(
    ("settings", "in_telegram", "account"),
    [
        (_SETTINGS, False, "1000"),
        (_CABINET_SETTINGS, True, "1000"),
        (_CABINET_SETTINGS, False, "2000"),
        ({**_CABINET_SETTINGS, **_RELAY_SETTINGS}, False, RELAY_ACCOUNT),
    ],
)
def test_account_for_picks_the_channels_shop(
    settings: dict[str, str], in_telegram: bool, account: Optional[str]
) -> None:
    assert YooKassaGateway.account_for(settings, in_telegram=in_telegram) == account


@pytest.mark.parametrize(
    ("account", "auth"),
    [
        ("2000", _basic_auth("2000", "cabinet_key")),
        ("1000", _basic_auth("1000", "test_key")),
        # an order from before the shops were split
        (None, _basic_auth("1000", "test_key")),
    ],
)
async def test_confirm_asks_the_shop_the_order_was_paid_into(
    account: Optional[str], auth: str
) -> None:
    http = HttpRecorder(json_response(_payment()))
    gateway = YooKassaGateway(
        settings=_CABINET_SETTINGS, webhook_url=WEBHOOK_URL, return_url=RETURN_URL, http=http.client
    )
    order = make_order(external_payment_id="yk-1", gateway_account=account)

    assert await gateway.check_payment(order) is not None
    assert http.last.headers["Authorization"] == auth


async def test_a_cabinet_order_is_still_checked_after_its_shop_is_switched_off() -> None:
    http = HttpRecorder(json_response(_payment()))
    gateway = YooKassaGateway(
        settings={**_CABINET_SETTINGS, "cabinet_shop_enabled": "false"},
        webhook_url=WEBHOOK_URL,
        return_url=RETURN_URL,
        http=http.client,
    )

    await gateway.check_payment(make_order(external_payment_id="yk-1", gateway_account="2000"))

    assert http.last.headers["Authorization"] == _basic_auth("2000", "cabinet_key")


async def test_an_order_of_a_shop_no_longer_configured_is_refused() -> None:
    order = make_order(external_payment_id="yk-1", gateway_account="2000")

    with pytest.raises(PaymentGatewayError):
        await _gateway().check_payment(order)


def test_settings_require_the_fields_of_the_active_mode() -> None:
    assert is_gateway_configured(PaymentGatewayType.YOOKASSA, _SETTINGS)
    assert is_gateway_configured(PaymentGatewayType.YOOKASSA, _RELAY_SETTINGS)
    assert not is_gateway_configured(
        PaymentGatewayType.YOOKASSA, {**_SETTINGS, "cabinet_shop_enabled": "true"}
    )
    assert not is_gateway_configured(
        PaymentGatewayType.YOOKASSA, {**_RELAY_SETTINGS, "relay_forward_secret": ""}
    )


@pytest.mark.parametrize("url", ["http://agg.example", "https://", "agg.example"])
def test_a_relay_url_other_than_https_is_refused_on_save(url: str) -> None:
    with pytest.raises(ValidationError):
        validate_gateway_field_values(PaymentGatewayType.YOOKASSA, {"relay_url": url})


def _relay_gateway(http: PaymentHttpClient = NO_HTTP) -> YooKassaGateway:
    return YooKassaGateway(
        settings={**_SETTINGS, **_RELAY_SETTINGS},
        webhook_url=WEBHOOK_URL,
        return_url=RETURN_URL,
        http=http,
    )


async def test_relay_request_is_signed_over_the_bytes_sent_and_carries_no_shop_key() -> None:
    http = HttpRecorder(
        json_response({"id": "yk-1", "confirmation": {"confirmation_url": "https://agg/go"}}),
    )

    await _relay_gateway(http.client).create_payment(
        make_order(gateway_account=RELAY_ACCOUNT), PAYMENT_TEXT
    )

    request = http.last
    assert str(request.url) == "https://agg.example/agg/v1/payments"
    assert "Authorization" not in request.headers
    assert request.headers["Idempotence-Key"] == ORDER_REF
    timestamp = request.headers["X-Timestamp"]
    expected = hmac.new(b"ingress", f"{timestamp}.".encode() + request.content, hashlib.sha256)
    assert request.headers["X-Signature"] == expected.hexdigest()


async def test_relay_status_request_signs_an_empty_body() -> None:
    http = HttpRecorder(json_response(_payment()))

    await _relay_gateway(http.client).check_payment(
        make_order(external_payment_id="yk-1", gateway_account=RELAY_ACCOUNT)
    )

    request = http.last
    assert request.url.path == "/agg/v1/payments/yk-1"
    timestamp = request.headers["X-Timestamp"]
    expected = hmac.new(b"ingress", f"{timestamp}.".encode(), hashlib.sha256)
    assert request.headers["X-Signature"] == expected.hexdigest()


def _relayed(body: bytes, *, secret: bytes = b"forward", age_s: int = 0) -> dict[str, str]:
    timestamp = str(int(time.time()) - age_s)
    signature = hmac.new(secret, f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Paygate-Timestamp": timestamp, "X-Paygate-Signature": signature}


async def test_relay_webhook_with_a_valid_signature_is_parsed() -> None:
    body = json.dumps({"event": "payment.succeeded", "object": _payment()}).encode()

    signal = await _relay_gateway().parse_webhook(webhook(body, _relayed(body)))

    assert signal is not None and signal.claims_paid is True
    assert signal.reference == ORDER_REF


_PAID_BODY: Final[bytes] = json.dumps({"event": "payment.succeeded", "object": _payment()}).encode()


@pytest.mark.parametrize(
    "headers",
    [
        _relayed(b"another body"),
        _relayed(_PAID_BODY, secret=b"wrong"),
        _relayed(_PAID_BODY, age_s=301),
        {**_relayed(_PAID_BODY), "X-Paygate-Timestamp": "\u00b2"},
    ],
    ids=["other-body", "wrong-secret", "stale", "non-ascii-digit"],
)
async def test_relay_webhook_without_a_valid_signature_is_refused(
    headers: dict[str, str],
) -> None:
    with pytest.raises(PaymentGatewayError):
        await _relay_gateway().parse_webhook(webhook(_PAID_BODY, headers))


@pytest.mark.parametrize(
    ("settings", "account", "url"),
    [
        # relay switched off: its orders still go through it
        ({**_SETTINGS, **_RELAY_SETTINGS, "relay_enabled": "false"}, RELAY_ACCOUNT, "agg.example"),
        # relay switched on: orders paid directly are still checked directly
        ({**_SETTINGS, **_RELAY_SETTINGS}, "1000", "api.yookassa.ru"),
    ],
)
async def test_an_order_is_checked_the_way_it_was_paid(
    settings: dict[str, str], account: str, url: str
) -> None:
    http = HttpRecorder(json_response(_payment()))
    gateway = YooKassaGateway(
        settings=settings, webhook_url=WEBHOOK_URL, return_url=RETURN_URL, http=http.client
    )

    await gateway.check_payment(make_order(external_payment_id="yk-1", gateway_account=account))

    assert http.last.url.host == url


async def test_a_relay_order_without_the_relay_configured_is_refused() -> None:
    order = make_order(external_payment_id="yk-1", gateway_account=RELAY_ACCOUNT)

    with pytest.raises(PaymentGatewayError):
        await _gateway().check_payment(order)


def test_a_relay_signed_notification_is_told_apart_by_its_header() -> None:
    relay_off = YooKassaGateway(
        settings={**_SETTINGS, **_RELAY_SETTINGS, "relay_enabled": "false"},
        webhook_url=WEBHOOK_URL,
        return_url=RETURN_URL,
        http=NO_HTTP,
    )
    assert relay_off.is_signed_notification(webhook(_PAID_BODY, _relayed(_PAID_BODY)))
    assert not _relay_gateway().is_signed_notification(webhook(_PAID_BODY))


async def test_a_relay_signed_notification_is_checked_even_with_the_relay_off() -> None:
    gateway = YooKassaGateway(
        settings={**_SETTINGS, **_RELAY_SETTINGS, "relay_enabled": "false"},
        webhook_url=WEBHOOK_URL,
        return_url=RETURN_URL,
        http=NO_HTTP,
    )

    with pytest.raises(PaymentGatewayError):
        await gateway.parse_webhook(webhook(_PAID_BODY, _relayed(_PAID_BODY, secret=b"wrong")))


async def test_an_order_from_before_the_split_never_falls_through_to_the_cabinet_shop() -> None:
    gateway = YooKassaGateway(
        settings={
            **_RELAY_SETTINGS,
            "cabinet_shop_id": "2000",
            "cabinet_api_key": "cabinet_key",
        },
        webhook_url=WEBHOOK_URL,
        return_url=RETURN_URL,
        http=NO_HTTP,
    )

    with pytest.raises(PaymentGatewayError):
        await gateway.check_payment(make_order(external_payment_id="yk-1", gateway_account=None))


def test_a_paygate_header_without_a_forward_secret_keeps_the_ip_check() -> None:
    assert not _gateway().is_signed_notification(webhook(_PAID_BODY, _relayed(_PAID_BODY)))


@pytest.mark.parametrize(
    "url", ["https://[abc", "https://agg.example/?x=1", "https://agg.example/#top"]
)
def test_a_relay_url_that_isnt_a_base_address_is_refused_on_save(url: str) -> None:
    with pytest.raises(ValidationError):
        validate_gateway_field_values(PaymentGatewayType.YOOKASSA, {"relay_url": url})


def test_the_cabinet_shop_cannot_repeat_the_main_one() -> None:
    assert (
        gateway_settings_error(
            PaymentGatewayType.YOOKASSA, {**_CABINET_SETTINGS, "cabinet_shop_id": "1000"}
        )
        is ErrorCode.GATEWAY_SHOP_DUPLICATE
    )


def test_the_relay_needs_no_shop_ids_even_with_the_cabinet_switch_on() -> None:
    values = {**_RELAY_SETTINGS, "cabinet_shop_enabled": "true"}

    assert gateway_settings_error(PaymentGatewayType.YOOKASSA, values) is None


def test_the_dashboard_asks_for_the_shops_unless_the_relay_is_on() -> None:
    catalog = GATEWAY_FIELD_CATALOG[PaymentGatewayType.YOOKASSA]
    waived_by = {spec.key: spec.required_unless for spec in catalog}

    assert {key for key, switch in waived_by.items() if switch == "relay_enabled"} == {
        "shop_id",
        "api_key",
        "cabinet_shop_id",
        "cabinet_api_key",
    }


@pytest.mark.parametrize(
    ("settings", "splits"),
    [
        (_SETTINGS, False),
        (_CABINET_SETTINGS, True),
        ({**_CABINET_SETTINGS, **_RELAY_SETTINGS}, False),
        ({"shop_id": "1000"}, False),
    ],
)
def test_the_channels_split_only_into_a_cabinet_shop_of_their_own(
    settings: dict[str, str], splits: bool
) -> None:
    assert gateway_splits_channels(PaymentGatewayType.YOOKASSA, settings) is splits
