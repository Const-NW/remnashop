from dataclasses import fields, replace
from typing import Any, Optional

from app.core.enums import PlanAvailability, PlanBadge, PlanType
from app.core.error_codes import ErrorCode
from app.core.exceptions import ValidationError
from app.modules.plans.dto import CreatePlanDto, PlanDto, SetPlanDurationDto, UpdatePlanDto


def zero_irrelevant_limits(
    plan_type: PlanType, traffic_limit: int, device_limit: int
) -> tuple[int, int]:
    """A plan's type decides which limit applies; the other is zeroed
    (0 = unlimited on the panel). An applying limit can't be 0: unlimited is
    the type's to say."""
    applies_traffic = plan_type in (PlanType.TRAFFIC, PlanType.BOTH)
    applies_devices = plan_type in (PlanType.DEVICES, PlanType.BOTH)
    if (applies_traffic and traffic_limit < 1) or (applies_devices and device_limit < 1):
        raise ValidationError(
            f"Plan type '{plan_type}' needs its limits of at least 1",
            code=ErrorCode.PLAN_LIMIT_REQUIRED,
        )
    return (traffic_limit if applies_traffic else 0), (device_limit if applies_devices else 0)


def check_trial_badge(is_trial: bool, badge: Optional[PlanBadge]) -> None:
    if is_trial and badge is not None:
        raise ValidationError("A trial plan can't carry a badge", code=ErrorCode.PLAN_TRIAL_BADGE)


def check_trial_availability(availability: PlanAvailability) -> None:
    # the trial button never offers an EXISTING trial
    if availability == PlanAvailability.EXISTING:
        raise ValidationError(
            "A trial plan can't be limited to existing users",
            code=ErrorCode.PLAN_TRIAL_EXISTING_ACCESS,
        )


def check_allowed_list(
    availability: PlanAvailability, telegram_ids: list[int], emails: list[str]
) -> None:
    if availability == PlanAvailability.ALLOWED and not telegram_ids and not emails:
        raise ValidationError(
            "availability=ALLOWED requires at least one allowed telegram id or email",
            code=ErrorCode.ALLOWED_LIST_EMPTY,
        )


def check_durations(plan: PlanDto, durations: list[SetPlanDurationDto]) -> None:
    if plan.is_active and not durations:
        raise ValidationError(
            "Cannot remove all durations from an active plan", code=ErrorCode.PLAN_NO_DURATIONS
        )
    seen_days: set[int] = set()
    for duration in durations:
        if duration.days in seen_days:
            raise ValidationError(
                f"Duplicate duration of {duration.days} days",
                code=ErrorCode.PLAN_DURATION_DUPLICATE,
            )
        seen_days.add(duration.days)


def normalize_new_plan(data: CreatePlanDto) -> CreatePlanDto:
    check_trial_badge(data.is_trial, data.badge)
    traffic_limit, device_limit = zero_irrelevant_limits(
        data.type, data.traffic_limit, data.device_limit
    )
    return replace(data, traffic_limit=traffic_limit, device_limit=device_limit)


def normalize_update(plan: PlanDto, data: UpdatePlanDto) -> UpdatePlanDto:
    """Checks the plan as it would stand after `data` and returns `data` with
    the limits its type implies and an orphaned icon fallback cleared."""
    changes: dict[str, Any] = {
        field.name: getattr(data, field.name)
        for field in fields(data)
        if field.name not in ("plan_id", "cleared", "translations")
        and getattr(data, field.name) is not None
    }
    after = plan.model_copy(update={**changes, **dict.fromkeys(data.cleared)})

    if after.is_trial and not plan.is_trial:
        check_trial_availability(after.availability)
    check_trial_badge(after.is_trial, after.badge)
    traffic_limit, device_limit = zero_irrelevant_limits(
        after.type, after.traffic_limit, after.device_limit
    )
    if after.is_active:
        if not after.internal_squads:
            raise ValidationError(
                "Cannot activate a plan with no squads", code=ErrorCode.PLAN_NO_SQUADS
            )
        if not after.durations:
            raise ValidationError(
                "Cannot activate a plan with no durations", code=ErrorCode.PLAN_NO_DURATIONS
            )

    cleared = data.cleared
    if after.icon_emoji_id is None:
        cleared = cleared | {"icon_emoji_fallback"}
    return replace(data, traffic_limit=traffic_limit, device_limit=device_limit, cleared=cleared)
