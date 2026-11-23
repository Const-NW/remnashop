from datetime import datetime
from typing import Optional

from app.core.constants import GRACE_ENTRY_WINDOW
from app.core.enums import SubscriptionStatus
from app.modules.plans import PlanDto
from app.modules.settings import GraceSettingsDto
from app.modules.subscriptions.dto import SubscriptionDto
from app.modules.subscriptions.unlimited import expiry_after


def can_renew(subscription: SubscriptionDto, plan: Optional[PlanDto]) -> bool:
    if subscription.is_trial or subscription.is_unlimited or plan is None:
        return False
    # a free duration isn't renewed either: the renewal catalog would come up empty
    return plan.allow_renewal and bool(plan.renewable_durations())


def grace_entry_allowed(
    subscription: SubscriptionDto,
    plan: Optional[PlanDto],
    settings: GraceSettingsDto,
    now: datetime,
) -> bool:
    return (
        settings.enabled
        and (
            subscription.status is SubscriptionStatus.EXPIRED
            # a lost expiry webhook leaves the row ACTIVE past its expiry
            or (subscription.status is SubscriptionStatus.ACTIVE and subscription.expire_at <= now)
        )
        and subscription.grace_until is None
        # an expiry no later than the last grace's end is that grace's own
        and (
            subscription.grace_ended_at is None
            or subscription.expire_at > subscription.grace_ended_at
        )
        and now - subscription.expire_at < GRACE_ENTRY_WINDOW
        and can_renew(subscription, plan)
    )


def grace_until_for(settings: GraceSettingsDto, now: datetime) -> datetime:
    return expiry_after(now, settings.duration_days, exact_days=True)
