from typing import Final, NewType, Optional, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator
from pydantic.config import JsonDict

from app.core.enums import PaymentGatewayType
from app.core.error_codes import ErrorCode
from app.core.exceptions import PaymentGatewayError

# digits as the admin typed them; a provider gets the exact text back
NumericStr = NewType("NumericStr", str)
# an https:// address the server calls, checked when saved
HttpsUrlStr = NewType("HttpsUrlStr", str)
# json_schema_extra key: the boolean field a field sits under in the dashboard form
FIELD_GROUP_KEY: Final[str] = "group"
# json_schema_extra key on a group's switch: it means something only with every channel on
REQUIRES_ALL_CHANNELS_KEY: Final[str] = "requires_all_channels"
# json_schema_extra key: the switch that makes a field the model requires optional
REQUIRED_UNLESS_KEY: Final[str] = "required_unless"
_UNLESS_RELAY: Final[JsonDict] = {REQUIRED_UNLESS_KEY: "relay_enabled"}


def _grouped(
    switch: str, *, requires_all_channels: bool = False, required_unless: Optional[str] = None
) -> JsonDict:
    return {
        FIELD_GROUP_KEY: switch,
        REQUIRES_ALL_CHANNELS_KEY: requires_all_channels,
        REQUIRED_UNLESS_KEY: required_unless,
    }


class GatewaySettings(BaseModel):
    """A gateway's dashboard fields; the dashboard catalog is built from them."""

    model_config = ConfigDict(frozen=True)

    @property
    def splits_channels(self) -> bool:
        """The bot and the cabinet pay into different shops."""
        return False

    @classmethod
    def from_values(cls, values: dict[str, str]) -> Self:
        # a blank field is an unset one
        present = {key: value for key, value in values.items() if value.strip()}
        try:
            return cls.model_validate(present)
        except ValidationError as error:
            missing = ", ".join(str(e["loc"][0]) for e in error.errors() if e["loc"])
            raise PaymentGatewayError(
                f"{cls.__name__}: invalid or missing setting(s) {missing}"
            ) from None


class TelegramStarsSettings(GatewaySettings):
    require_paid_purchase: bool = False


class YooKassaSettings(GatewaySettings):
    # the Telegram shop (bot and Mini App), and the cabinet's too without a shop of its own
    shop_id: Optional[str] = Field(None, json_schema_extra=_UNLESS_RELAY)
    api_key: Optional[SecretStr] = Field(None, json_schema_extra=_UNLESS_RELAY)
    vat_code: Optional[NumericStr] = None
    verify_ip: bool = True
    cabinet_shop_enabled: bool = Field(
        False, json_schema_extra=_grouped("cabinet_shop_enabled", requires_all_channels=True)
    )
    cabinet_shop_id: Optional[str] = Field(
        None, json_schema_extra=_grouped("cabinet_shop_enabled", required_unless="relay_enabled")
    )
    cabinet_api_key: Optional[SecretStr] = Field(
        None, json_schema_extra=_grouped("cabinet_shop_enabled", required_unless="relay_enabled")
    )
    # every channel goes through one paygate relay, which holds its shop's key
    relay_enabled: bool = Field(False, json_schema_extra=_grouped("relay_enabled"))
    # not run through the SSRF guard: fake-IP DNS, common on these servers, resolves
    # public hosts to 198.18.x; only an admin with GATEWAYS_UPDATE sets it
    relay_url: Optional[HttpsUrlStr] = Field(None, json_schema_extra=_grouped("relay_enabled"))
    relay_ingress_secret: Optional[SecretStr] = Field(
        None, json_schema_extra=_grouped("relay_enabled")
    )
    relay_forward_secret: Optional[SecretStr] = Field(
        None, json_schema_extra=_grouped("relay_enabled")
    )

    @property
    def splits_channels(self) -> bool:
        return self.cabinet_shop_enabled and not self.relay_enabled

    @model_validator(mode="after")
    def _require_active_fields(self) -> Self:
        if self.relay_enabled:
            required: tuple[str, ...] = (
                "relay_url",
                "relay_ingress_secret",
                "relay_forward_secret",
            )
        elif self.cabinet_shop_enabled:
            required = ("shop_id", "api_key", "cabinet_shop_id", "cabinet_api_key")
        else:
            required = ("shop_id", "api_key")
        missing = ", ".join(name for name in required if getattr(self, name) is None)
        if missing:
            raise PaymentGatewayError(
                f"{type(self).__name__}: invalid or missing setting(s) {missing}"
            )
        # orders are told apart by shop id; one shop for both channels is the switch off
        # the relay ignores both shops, so their ids may be blank there
        if (
            not self.relay_enabled
            and self.cabinet_shop_enabled
            and self.cabinet_shop_id == self.shop_id
        ):
            raise PaymentGatewayError(
                f"{type(self).__name__}: the cabinet shop is the main one",
                code=ErrorCode.GATEWAY_SHOP_DUPLICATE,
            )
        return self


class YooMoneySettings(GatewaySettings):
    wallet_id: str
    secret_key: SecretStr
    access_token: Optional[SecretStr] = None


class CryptomusSettings(GatewaySettings):
    merchant_id: str
    api_key: SecretStr
    currency: Optional[str] = None


class HeleketSettings(CryptomusSettings): ...


class CryptoPaySettings(GatewaySettings):
    api_key: SecretStr
    currency: Optional[str] = None


class FreeKassaSettings(GatewaySettings):
    shop_id: NumericStr
    api_key: SecretStr
    secret_word_2: SecretStr
    payment_system_id: NumericStr


class MulenPaySettings(GatewaySettings):
    api_key: SecretStr
    secret_key: SecretStr
    shop_id: NumericStr
    vat_code: Optional[NumericStr] = None


class PayMasterSettings(GatewaySettings):
    merchant_id: str
    api_key: SecretStr


class PlategaSettings(GatewaySettings):
    merchant_id: str
    api_key: SecretStr
    payment_method: Optional[NumericStr] = None


class RobokassaSettings(GatewaySettings):
    merchant_login: str
    password1: SecretStr
    password2: SecretStr


class UrlPaySettings(GatewaySettings):
    shop_id: NumericStr
    api_key: SecretStr
    secret_key: SecretStr
    vat_code: Optional[NumericStr] = None


class WataSettings(GatewaySettings):
    api_key: SecretStr
    currency: Optional[str] = None


class ValutixSettings(GatewaySettings):
    api_key: SecretStr
    service_id: Optional[NumericStr] = None
    currency: Optional[str] = None

    @property
    def token(self) -> SecretStr:
        return self.api_key


class RollyPaySettings(GatewaySettings):
    api_key: SecretStr
    signing_secret: SecretStr


class AntilopaySettings(GatewaySettings):
    secret_id: str
    private_key: SecretStr
    public_key: SecretStr
    project_id: str


class PaypalychSettings(GatewaySettings):
    api_token: SecretStr
    shop_id: str
    currency: Optional[str] = None


class RioPaySettings(GatewaySettings):
    api_token: SecretStr
    service_id: Optional[NumericStr] = None
    currency: Optional[str] = None

    @property
    def token(self) -> SecretStr:
        return self.api_token


class AuraPaySettings(GatewaySettings):
    api_key: SecretStr
    shop_id: str
    secret_key: SecretStr


class SeverPaySettings(GatewaySettings):
    mid: NumericStr
    token: SecretStr
    currency: Optional[str] = None


class LavaSettings(GatewaySettings):
    shop_id: str
    secret_key: SecretStr
    webhook_secret: SecretStr


class CloudPaymentsSettings(GatewaySettings):
    public_id: str
    api_secret: SecretStr
    currency: Optional[str] = None


class PayPearSettings(GatewaySettings):
    shop_id: str
    secret_key: SecretStr
    currency: Optional[str] = None


class CisPaySettings(GatewaySettings):
    shop_id: str
    api_key: SecretStr
    currency: Optional[str] = None


GATEWAY_SETTINGS: Final[dict[PaymentGatewayType, type[GatewaySettings]]] = {
    PaymentGatewayType.TELEGRAM_STARS: TelegramStarsSettings,
    PaymentGatewayType.YOOKASSA: YooKassaSettings,
    PaymentGatewayType.YOOMONEY: YooMoneySettings,
    PaymentGatewayType.CRYPTOMUS: CryptomusSettings,
    PaymentGatewayType.HELEKET: HeleketSettings,
    PaymentGatewayType.CRYPTOPAY: CryptoPaySettings,
    PaymentGatewayType.FREEKASSA: FreeKassaSettings,
    PaymentGatewayType.MULENPAY: MulenPaySettings,
    PaymentGatewayType.PAYMASTER: PayMasterSettings,
    PaymentGatewayType.PLATEGA: PlategaSettings,
    PaymentGatewayType.ROBOKASSA: RobokassaSettings,
    PaymentGatewayType.URLPAY: UrlPaySettings,
    PaymentGatewayType.WATA: WataSettings,
    PaymentGatewayType.VALUTIX: ValutixSettings,
    PaymentGatewayType.ROLLYPAY: RollyPaySettings,
    PaymentGatewayType.ANTILOPAY: AntilopaySettings,
    PaymentGatewayType.PAYPALYCH: PaypalychSettings,
    PaymentGatewayType.RIOPAY: RioPaySettings,
    PaymentGatewayType.AURAPAY: AuraPaySettings,
    PaymentGatewayType.SEVERPAY: SeverPaySettings,
    PaymentGatewayType.LAVA: LavaSettings,
    PaymentGatewayType.CLOUDPAYMENTS: CloudPaymentsSettings,
    PaymentGatewayType.PAYPEAR: PayPearSettings,
    PaymentGatewayType.CISPAY: CisPaySettings,
}
