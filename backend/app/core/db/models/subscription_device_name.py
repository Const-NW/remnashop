from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import DEVICE_NAME_MAX_LENGTH
from app.core.db.base import BaseSql
from app.core.db.timestamp import TimestampMixin


class SubscriptionDeviceName(BaseSql, TimestampMixin):
    """A customer's own name for a device: the panel keeps none. Kept after the
    device is removed, so the same device connecting again gets it back."""

    __tablename__ = "subscription_device_names"

    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), primary_key=True
    )
    # the panel's HWID column is varchar(64)
    hwid: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(DEVICE_NAME_MAX_LENGTH))
