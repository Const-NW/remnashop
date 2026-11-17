from dataclasses import dataclass
from typing import Optional

from pydantic import Field

from app.core.dto import BaseDto, TimestampMixin
from app.core.enums import PaymentGatewayType


def default_telegram_enabled(gateway_type: PaymentGatewayType) -> bool:
    # Telegram allows only Stars for digital goods in bots and Mini Apps
    return gateway_type is PaymentGatewayType.TELEGRAM_STARS


class PaymentGatewayDto(BaseDto, TimestampMixin):
    type: PaymentGatewayType
    order_index: int = 0
    is_active: bool = False
    is_configured: bool = False
    flags: dict[str, bool] = Field(default_factory=dict)
    telegram_enabled: bool = False
    cabinet_enabled: bool = True

    def offered_in(self, *, in_telegram: bool) -> bool:
        return self.telegram_enabled if in_telegram else self.cabinet_enabled


@dataclass(frozen=True, kw_only=True)
class UpdateGatewaySettingsDto:
    type: PaymentGatewayType
    values: dict[str, str]


@dataclass(frozen=True, kw_only=True)
class ReorderGatewaysDto:
    ordered_types: list[PaymentGatewayType]


@dataclass(frozen=True, kw_only=True)
class SetGatewayActiveDto:
    type: PaymentGatewayType
    is_active: bool


@dataclass(frozen=True, kw_only=True)
class UpdateGatewayChannelsDto:
    type: PaymentGatewayType
    telegram_enabled: Optional[bool] = None
    cabinet_enabled: Optional[bool] = None
