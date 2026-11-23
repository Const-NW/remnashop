from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from remnapy.enums import TrafficLimitStrategy
from sqlalchemy import ForeignKey, Index, SmallInteger, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import BaseSql
from app.core.db.timestamp import TimestampMixin
from app.core.enums import SubscriptionStatus


class Subscription(BaseSql, TimestampMixin):
    __tablename__ = "subscriptions"
    __table_args__ = (
        Index(
            "ux_subscriptions_remnawave_user_id_live",
            "remnawave_user_id",
            unique=True,
            postgresql_where=text(f"status != '{SubscriptionStatus.DELETED.name}'"),
        ),
        # the grace sweep's end lookup; almost every row has none
        Index(
            "ix_subscriptions_grace_until",
            "grace_until",
            postgresql_where=text("grace_until IS NOT NULL"),
        ),
        Index(
            "ix_subscriptions_panel_restore_due_at",
            "panel_restore_due_at",
            postgresql_where=text("panel_restore_due_at IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    # Remnawave 3.x addresses users by numeric id, not UUID
    # (UserResponseDto.id is `int`). Two live rows must never share a panel
    # user: a partial unique index, not `unique=True`, since a trial->paid
    # conversion or a plan CHANGE marks the row DELETED and inserts a new one
    # with the same remnawave_user_id.
    remnawave_user_id: Mapped[int] = mapped_column(index=True)

    status: Mapped[SubscriptionStatus] = mapped_column(index=True)
    is_trial: Mapped[bool] = mapped_column(default=False, server_default="false")

    traffic_limit: Mapped[int]
    device_limit: Mapped[int]
    traffic_limit_strategy: Mapped[TrafficLimitStrategy]

    tag: Mapped[Optional[str]]
    internal_squads: Mapped[list[UUID]]
    external_squad: Mapped[Optional[UUID]]

    expire_at: Mapped[datetime] = mapped_column(index=True)
    url: Mapped[str]
    # Read/written as one unit (a frozen snapshot of what was purchased),
    # never filtered/joined on a field inside it.
    plan_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)

    device_single_reset_at: Mapped[Optional[datetime]]
    device_all_reset_at: Mapped[Optional[datetime]]
    link_reset_at: Mapped[Optional[datetime]]
    # disabled because the customer left the required channel; only these come back on rejoin
    paused_by_channel_leave: Mapped[bool] = mapped_column(default=False, server_default="false")
    # set while the grace period holds the panel user active past expiry; cleared on renewal
    grace_until: Mapped[Optional[datetime]]
    # the grace entry finished (pushed, traffic reset, customer told); a claim without it
    # is an entry a retry still has to finish
    grace_started_at: Mapped[Optional[datetime]]
    # the last grace's end: the panel keeps it as its expiry, so a sync brings it into
    # expire_at and the row would look freshly expired again
    grace_ended_at: Mapped[Optional[datetime]]
    # the grace sweep's last pick: a row that keeps failing goes to the back of its queue
    grace_attempted_at: Mapped[Optional[datetime]]
    # a push putting the panel user back to the row's config (or grace's) failed after a
    # commit that can't be retried; a cron pushes it again until it lands
    panel_restore_due_at: Mapped[Optional[datetime]]
    # kept on DELETED rows: a trial->paid or plan change replaces the row, not the customer
    first_connected_at: Mapped[Optional[datetime]]
    # the panel user's createdAt: its day of the month is when MONTH_ROLLING resets
    panel_created_at: Mapped[Optional[datetime]]
    # day of the month calendar terms land on: started on the 31st, back on the 31st
    # after a shorter month cut a term to the 28th
    term_anchor_day: Mapped[Optional[int]] = mapped_column(SmallInteger)
