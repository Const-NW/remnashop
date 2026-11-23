from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final, Optional
from uuid import UUID

from remnapy.enums import TrafficLimitStrategy

from app.core.enums import SubscriptionStatus
from app.core.error_codes import ErrorCode
from app.core.exceptions import ValidationError
from app.modules.subscriptions.dto import PlanSnapshot
from app.modules.subscriptions.unlimited import is_unlimited_expiry


class SupersededError(ValidationError):
    """A concurrent purchase committed first and now owns the panel identity
    this one reconfigured; reverting it would overwrite the winner's config."""


class OrderNotPaidError(SupersededError):
    """The order behind this purchase was refunded or failed while it was in flight."""


# a subscription that still holds a panel identity and counts as owned
OWNED_STATUSES: Final[tuple[SubscriptionStatus, ...]] = (
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.DISABLED,
    SubscriptionStatus.LIMITED,
)

# a status may always go to itself (a redelivered webhook); DELETED is terminal
VALID_TRANSITIONS: Final[dict[SubscriptionStatus, frozenset[SubscriptionStatus]]] = {
    SubscriptionStatus.ACTIVE: frozenset(
        {
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.LIMITED,
            SubscriptionStatus.DISABLED,
            SubscriptionStatus.EXPIRED,
            SubscriptionStatus.DELETED,
        }
    ),
    SubscriptionStatus.LIMITED: frozenset(
        {
            SubscriptionStatus.LIMITED,
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.DISABLED,
            SubscriptionStatus.EXPIRED,
            SubscriptionStatus.DELETED,
        }
    ),
    SubscriptionStatus.DISABLED: frozenset(
        {
            SubscriptionStatus.DISABLED,
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.LIMITED,
            SubscriptionStatus.EXPIRED,
            SubscriptionStatus.DELETED,
        }
    ),
    SubscriptionStatus.EXPIRED: frozenset(
        {
            SubscriptionStatus.EXPIRED,
            SubscriptionStatus.ACTIVE,  # renew reactivates, see reactivate_on_renew()
            SubscriptionStatus.DELETED,
        }
    ),
    SubscriptionStatus.DELETED: frozenset({SubscriptionStatus.DELETED}),
}


def assert_transition(current: SubscriptionStatus, target: SubscriptionStatus) -> None:
    if target not in VALID_TRANSITIONS[current]:
        raise ValidationError(
            f"Subscription status '{current}' -> '{target}' is not allowed",
            code=ErrorCode.SUBSCRIPTION_STATUS_TRANSITION_INVALID,
        )


@dataclass(frozen=True, kw_only=True)
class SubscriptionState:
    """Behavioural counterpart to SubscriptionDto: the repository maps a row to
    it and the returned state back. Every method returns a new instance."""

    id: int
    status: SubscriptionStatus
    expire_at: datetime
    plan_snapshot: PlanSnapshot
    traffic_limit: int
    device_limit: int
    traffic_limit_strategy: TrafficLimitStrategy
    internal_squads: list[UUID]
    external_squad: Optional[UUID]
    is_trial: bool
    # read for mark_deleted only; apply_state leaves them alone
    grace_until: Optional[datetime] = None
    grace_ended_at: Optional[datetime] = None

    def sync_status(self, target: SubscriptionStatus) -> SubscriptionState:
        """The panel is authoritative: any status it reports is taken, except
        that a DELETED row is never resurrected by a stale panel event."""
        if self.status is SubscriptionStatus.DELETED or target is self.status:
            return self
        return replace(self, status=target)

    def sync_expire_at(self, new_expire_at: datetime) -> SubscriptionState:
        """Status is never inferred from expire_at: the panel reports its own."""
        if self.status is SubscriptionStatus.DELETED:
            return self
        return replace(self, expire_at=new_expire_at)

    def reactivate_on_renew(self, new_expire_at: datetime) -> SubscriptionState:
        """Extends expire_at and brings an EXPIRED subscription back to ACTIVE.
        LIMITED/DISABLED stay: a payment doesn't resolve traffic exhaustion or an
        admin's action, and the next sync corrects them once the panel agrees."""
        if self.status is SubscriptionStatus.DELETED:
            raise ValidationError(
                "Cannot renew a deleted subscription", code=ErrorCode.SUBSCRIPTION_DELETED
            )
        is_expired = self.status is SubscriptionStatus.EXPIRED
        target = SubscriptionStatus.ACTIVE if is_expired else self.status
        assert_transition(self.status, target)
        return replace(self, status=target, expire_at=new_expire_at)

    def shorten_after_refund(self, days: int, now: datetime) -> SubscriptionState:
        """Takes a refunded renewal's days back off the end; a later unlimited
        renewal that replaced the term keeps the subscription unlimited."""
        if self.status is SubscriptionStatus.DELETED:
            raise ValidationError(
                "Cannot shorten a deleted subscription", code=ErrorCode.SUBSCRIPTION_DELETED
            )
        if is_unlimited_expiry(self.expire_at):
            return self
        return self._end_at(self.expire_at - timedelta(days=days), now)

    def end_unlimited(self, restore_to: datetime, now: datetime) -> SubscriptionState:
        """A refunded unlimited renewal: back to the term it replaced."""
        if self.status is SubscriptionStatus.DELETED:
            raise ValidationError(
                "Cannot shorten a deleted subscription", code=ErrorCode.SUBSCRIPTION_DELETED
            )
        return self._end_at(restore_to, now)

    def _end_at(self, expire_at: datetime, now: datetime) -> SubscriptionState:
        status = SubscriptionStatus.EXPIRED if expire_at <= now else self.status
        assert_transition(self.status, status)
        return replace(self, status=status, expire_at=expire_at)

    def restore_after_refund(self, now: datetime) -> SubscriptionState:
        """The one way out of DELETED: a refunded plan change puts the replaced
        row back, so VALID_TRANSITIONS is bypassed on purpose."""
        if self.status is not SubscriptionStatus.DELETED:
            raise ValidationError(
                f"Only a deleted subscription can be restored, not '{self.status}'",
                code=ErrorCode.SUBSCRIPTION_STATUS_TRANSITION_INVALID,
            )
        expired = self.expire_at <= now
        status = SubscriptionStatus.EXPIRED if expired else SubscriptionStatus.ACTIVE
        return replace(self, status=status)

    def mark_deleted(self) -> SubscriptionState:
        assert_transition(self.status, SubscriptionStatus.DELETED)
        # a refund may bring the row back: as a plain expired one, its grace used up
        return replace(
            self,
            status=SubscriptionStatus.DELETED,
            grace_until=None,
            grace_ended_at=self.grace_until or self.grace_ended_at,
        )
