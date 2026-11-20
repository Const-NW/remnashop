from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import BaseSql
from app.core.db.timestamp import TimestampMixin


class SubscriptionPurchaseIdempotency(BaseSql, TimestampMixin):
    __tablename__ = "subscription_purchase_idempotency"

    key: Mapped[str] = mapped_column(primary_key=True)
    subscription_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(
            "subscriptions.id",
            ondelete="CASCADE",
            name="fk_subscription_purchase_idempotency_subscription_id",
        ),
        index=True,
    )
    # RENEW only: the absolute expiry this purchase writes to the panel, pinned
    # before the panel call so a retry re-applies the same value instead of
    # extending a second time.
    renew_expire_at: Mapped[Optional[datetime]]
    # the days that expiry adds, for a refund of an unfinished renewal to take back
    renew_added_days: Mapped[Optional[int]]
    # the anchor day it leaves on the row: a retry can't work it out from the end
    # alone, a shorter month having cut it
    renew_anchor_day: Mapped[Optional[int]]
    # the row that expiry was worked out for: a plan change replacing it leaves the
    # pin stale, the renewal going to the row put in its place
    renew_subscription_id: Mapped[Optional[int]]
