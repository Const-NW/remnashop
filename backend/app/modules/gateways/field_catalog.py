from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Final, Optional, get_args
from urllib.parse import urlsplit

from pydantic import SecretStr
from pydantic.fields import FieldInfo

from app.core.enums import Currency, PaymentGatewayType
from app.core.error_codes import ErrorCode
from app.core.exceptions import PaymentGatewayError, ValidationError
from app.modules.gateways.gateway_settings import (
    FIELD_GROUP_KEY,
    GATEWAY_SETTINGS,
    REQUIRED_UNLESS_KEY,
    REQUIRES_ALL_CHANNELS_KEY,
    HttpsUrlStr,
    NumericStr,
)

_CURRENCY_FIELD_KEY: Final[str] = "currency"
# Stars settle only through the Telegram Stars gateway, which has no currency field
_GATEWAY_CURRENCIES: Final[tuple[Currency, ...]] = (Currency.USD, Currency.RUB)
# Webhook source-IP check for providers that don't sign their notifications
VERIFY_IP_FIELD_KEY: Final[str] = "verify_ip"
REQUIRE_PAID_PURCHASE_FIELD_KEY: Final[str] = "require_paid_purchase"
_BOOLEAN_VALUES: Final[frozenset[str]] = frozenset({"true", "false"})


class GatewayFieldKind(StrEnum):
    TEXT = auto()
    SECRET = auto()
    NUMBER = auto()
    BOOLEAN = auto()
    URL = auto()


@dataclass(frozen=True, kw_only=True)
class GatewayFieldSpec:
    key: str
    kind: GatewayFieldKind
    required: bool = True
    default: str = ""
    # the boolean field that shows this one (itself, for the switch); None: always shown
    group: Optional[str] = None
    # a group's switch shown only while every channel is on (or the switch is)
    requires_all_channels: bool = False
    # a field the dashboard asks for while this switch is off (the model decides when it's set)
    required_unless: Optional[str] = None


_KIND_BY_TYPE: Final[dict[object, GatewayFieldKind]] = {
    str: GatewayFieldKind.TEXT,
    SecretStr: GatewayFieldKind.SECRET,
    NumericStr: GatewayFieldKind.NUMBER,
    bool: GatewayFieldKind.BOOLEAN,
    HttpsUrlStr: GatewayFieldKind.URL,
}


def _field_spec(key: str, field: FieldInfo) -> GatewayFieldSpec:
    declared = [arg for arg in get_args(field.annotation) if arg is not type(None)]
    kind = _KIND_BY_TYPE[declared[0] if declared else field.annotation]
    default = str(field.default).lower() if kind is GatewayFieldKind.BOOLEAN else ""
    extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
    group = extra.get(FIELD_GROUP_KEY)
    required_unless = extra.get(REQUIRED_UNLESS_KEY)
    return GatewayFieldSpec(
        key=key,
        kind=kind,
        required=field.is_required(),
        default=default,
        group=group if isinstance(group, str) else None,
        requires_all_channels=extra.get(REQUIRES_ALL_CHANNELS_KEY) is True,
        required_unless=required_unless if isinstance(required_unless, str) else None,
    )


GATEWAY_FIELD_CATALOG: Final[dict[PaymentGatewayType, tuple[GatewayFieldSpec, ...]]] = {
    gateway_type: tuple(_field_spec(key, field) for key, field in model.model_fields.items())
    for gateway_type, model in GATEWAY_SETTINGS.items()
}


def gateway_settings_error(
    gateway_type: PaymentGatewayType, values: dict[str, str]
) -> Optional[ErrorCode]:
    """Why these settings can't take payments, or None when they can."""
    if not all(
        values.get(field_spec.key, "").strip() != ""
        for field_spec in GATEWAY_FIELD_CATALOG[gateway_type]
        if field_spec.required
    ):
        return ErrorCode.GATEWAY_SETTINGS_INCOMPLETE
    # a model may require fields only in some modes (YooKassa's relay)
    try:
        GATEWAY_SETTINGS[gateway_type].from_values(values)
    except PaymentGatewayError as error:
        if error.code is ErrorCode.PAYMENT_GATEWAY_ERROR:
            return ErrorCode.GATEWAY_SETTINGS_INCOMPLETE
        return error.code
    return None


def is_gateway_configured(gateway_type: PaymentGatewayType, values: dict[str, str]) -> bool:
    return gateway_settings_error(gateway_type, values) is None


def gateway_flags(gateway_type: PaymentGatewayType, values: dict[str, str]) -> dict[str, bool]:
    # unlike every other field these aren't secrets, so the dashboard reads
    # them back and renders switches
    return {
        field_spec.key: (values.get(field_spec.key) or field_spec.default) == "true"
        for field_spec in GATEWAY_FIELD_CATALOG[gateway_type]
        if field_spec.kind is GatewayFieldKind.BOOLEAN
    }


def validate_gateway_field_keys(gateway_type: PaymentGatewayType, values: dict[str, str]) -> None:
    valid_keys = {field_spec.key for field_spec in GATEWAY_FIELD_CATALOG[gateway_type]}
    unknown = set(values) - valid_keys
    if unknown:
        raise ValidationError(
            f"Unknown settings field(s) for {gateway_type.value}: {', '.join(sorted(unknown))}",
            code=ErrorCode.GATEWAY_FIELD_UNKNOWN,
        )


def validate_gateway_field_values(gateway_type: PaymentGatewayType, values: dict[str, str]) -> None:
    raw_currency = values.get(_CURRENCY_FIELD_KEY, "").strip()
    if raw_currency:
        if raw_currency.upper() not in _GATEWAY_CURRENCIES:
            allowed = ", ".join(currency.value for currency in _GATEWAY_CURRENCIES)
            raise ValidationError(
                f"Invalid currency '{raw_currency}' for {gateway_type.value}; "
                f"must be one of {allowed}",
                code=ErrorCode.GATEWAY_CURRENCY_INVALID,
            )

    for field_spec in GATEWAY_FIELD_CATALOG[gateway_type]:
        _validate_field_value(gateway_type, field_spec, values)


def _validate_field_value(
    gateway_type: PaymentGatewayType, field_spec: GatewayFieldSpec, values: dict[str, str]
) -> None:
    if field_spec.kind is GatewayFieldKind.BOOLEAN:
        raw_flag = values.get(field_spec.key)
        if raw_flag is not None and raw_flag not in _BOOLEAN_VALUES:
            raise ValidationError(
                f"Field '{field_spec.key}' for {gateway_type.value} must be 'true' or 'false'",
                code=ErrorCode.GATEWAY_FIELD_NOT_BOOLEAN,
            )
        return
    if field_spec.kind is GatewayFieldKind.URL:
        raw_url = values.get(field_spec.key, "").strip()
        if raw_url and not _is_base_https_url(raw_url):
            raise ValidationError(
                f"Field '{field_spec.key}' for {gateway_type.value} must be an https:// URL",
                code=ErrorCode.URL_NOT_HTTPS,
            )
        return
    if field_spec.kind is not GatewayFieldKind.NUMBER:
        return
    raw_value = values.get(field_spec.key, "").strip()
    if not raw_value:
        return
    try:
        int(raw_value)
    except ValueError:
        raise ValidationError(
            f"Field '{field_spec.key}' for {gateway_type.value} must be a number, "
            f"got '{raw_value}'",
            code=ErrorCode.GATEWAY_FIELD_NOT_NUMBER,
        ) from None


def _is_base_https_url(value: str) -> bool:
    # a base address: paths are appended to it, so no query or fragment
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(hostname) and not parsed.query and not parsed.fragment
