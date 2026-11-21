from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import BaseSql
from app.core.db.timestamp import TimestampMixin
from app.core.enums import CreditSegmentKind, Currency


class CreditSegment(BaseSql, TimestampMixin):
    """A stretch of a subscription's term and what was paid for it: a plan change
    credits what's left of each at its own price."""

    __tablename__ = "credit_segments"
    __table_args__ = (
        CheckConstraint("paid >= 0", name="paid_non_negative"),
        CheckConstraint("seconds >= 0", name="seconds_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[CreditSegmentKind]
    # the order's purchase key: a refund takes its own segment back
    payment_key: Mapped[Optional[str]] = mapped_column(String())
    # when this term's order zeroed the panel's traffic counter: one counter covers all
    # the time from then to the expiry
    traffic_reset_at: Mapped[Optional[datetime]]
    # how far the traffic ran ahead of time before that reset, carried over it so a
    # renewal bought only to zero the counter unlocks nothing; 1 = not ahead
    traffic_pace: Mapped[Decimal] = mapped_column(Numeric(7, 6), default=1, server_default="1")
    paid: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[Currency]
    # the stretch is (ends_at - seconds, ends_at]; an unlimited term has no end and 0
    seconds: Mapped[int] = mapped_column(BigInteger)
    ends_at: Mapped[Optional[datetime]]
    # an unlimited term: the expiry it replaced, which its refund puts back
    replaced_expire_at: Mapped[Optional[datetime]]
    # replaced by an unlimited term: credits nothing until that term is refunded
    superseded_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("credit_segments.id", ondelete="SET NULL")
    )
