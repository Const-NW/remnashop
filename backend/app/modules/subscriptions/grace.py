from datetime import datetime
from typing import Optional

from remnapy.enums import UserStatus
from remnapy.models import UpdateUserRequestDto

from app.core.constants import GRACE_ENTRY_WINDOW, MB_TO_BYTES
from app.core.enums import SubscriptionStatus
from app.modules.plans import PlanDto
from app.modules.settings import GraceSettingsDto
from app.modules.subscriptions.dto import SubscriptionDto
from app.modules.subscriptions.panel import panel_update
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
        and subscription.status is SubscriptionStatus.EXPIRED
        and subscription.grace_until is None
        and now - subscription.expire_at < GRACE_ENTRY_WINDOW
        and can_renew(subscription, plan)
    )


def grace_until_for(settings: GraceSettingsDto, now: datetime) -> datetime:
    return expiry_after(now, settings.duration_days, exact_days=True)


def grace_panel_update(
    subscription: SubscriptionDto, settings: GraceSettingsDto, grace_until: datetime
) -> UpdateUserRequestDto:
    return panel_update(
        status=UserStatus.ACTIVE,
        expire_at=grace_until,
        traffic_limit_bytes=settings.traffic_limit_mb * MB_TO_BYTES,
        traffic_limit_strategy=settings.traffic_limit_strategy,
        hwid_device_limit=subscription.effective_device_limit,
        active_internal_squads=settings.internal_squads or None,
        external_squad_uuid=settings.external_squad,
        tag=settings.tag,
    )
