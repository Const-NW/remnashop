import re
from contextlib import suppress
from datetime import datetime
from decimal import Decimal
from typing import Any, Final, Literal, Optional
from uuid import uuid4

from loguru import logger
from remnapy import RemnawaveSDK
from remnapy.enums import ErrorCode as RemnawaveErrorCode
from remnapy.enums import UserStatus
from remnapy.exceptions import ApiError as RemnawaveApiError
from remnapy.exceptions import NotFoundError as RemnawaveNotFoundError
from remnapy.models import CreateUserRequestDto, UpdateUserRequestDto, UserResponseDto

from app.core.constants import GB_TO_BYTES, INT32_MAX, INT64_MAX, MB_TO_BYTES, WEB_PREFIX
from app.core.db.uow import UnitOfWork
from app.core.enums import AddOnType, SubscriptionStatus
from app.core.utils.time import datetime_now
from app.modules.settings import GraceSettingsDto
from app.modules.subscriptions.credit import traffic_left_share
from app.modules.subscriptions.dto import (
    CreateSubscriptionDto,
    PlanSnapshot,
    PurchaseSubscriptionDto,
    SubscriptionDto,
)
from app.modules.subscriptions.lifecycle import SubscriptionLifecycleService
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.users import UserDto

# null clears these in the panel; the SDK sends every field it was given, and
# the panel refuses null in the others with a 400
_PANEL_NULLABLE: Final[frozenset[str]] = frozenset({"tag", "external_squad_uuid"})


def _sent(fields: dict[str, Any]) -> dict[str, Any]:
    """The fields to send: None means "leave as is" except where null clears."""
    return {
        name: value
        for name, value in fields.items()
        if value is not None or name in _PANEL_NULLABLE
    }


def panel_update(**fields: Any) -> UpdateUserRequestDto:
    return UpdateUserRequestDto(**_sent(fields))


def _panel_username(prefix: str, user_id: int) -> str:
    return f"{prefix}{user_id}_{uuid4().hex[:8]}"


def shop_user_key_from_username(
    username: str, prefix: str
) -> Optional[tuple[Literal["id", "telegram_id"], int]]:
    """The shop user a panel user was named for: by id in the current format
    and the legacy web one, by Telegram id in the legacy `<prefix><telegram_id>`."""
    keys: tuple[tuple[str, Literal["id", "telegram_id"], int], ...] = (
        (r"(\d+)_[0-9a-f]{8}", "id", INT32_MAX),
        (rf"{WEB_PREFIX}(\d+)", "id", INT32_MAX),
        (r"(\d+)", "telegram_id", INT64_MAX),
    )
    for pattern, key, limit in keys:
        if match := re.fullmatch(re.escape(prefix) + pattern, username):
            # usernames are panel-side input; a number past the column is no match
            value = int(match.group(1))
            return (key, value) if value <= limit else None
    return None


def new_panel_user(
    user: UserDto, plan: PlanSnapshot, expire_at: datetime, prefix: str
) -> CreateUserRequestDto:
    return CreateUserRequestDto(
        **_sent(
            {
                "username": _panel_username(prefix, user.id),
                "telegram_id": user.telegram_id,
                "email": user.email,
                "expire_at": expire_at,
                "traffic_limit_bytes": plan.traffic_limit * GB_TO_BYTES,
                "traffic_limit_strategy": plan.traffic_limit_strategy,
                "hwid_device_limit": plan.device_limit,
                "active_internal_squads": plan.internal_squads or None,
                "external_squad_uuid": plan.external_squad,
                "tag": plan.tag,
            }
        )
    )


def panel_user_from_row(
    subscription: SubscriptionDto, user: UserDto, prefix: str
) -> CreateUserRequestDto:
    """A new panel user for a row whose own one is gone."""
    return CreateUserRequestDto(
        **_sent(
            {
                "username": _panel_username(prefix, user.id),
                "telegram_id": user.telegram_id,
                "email": user.email,
                # the panel takes a past date on create, unlike on update
                "expire_at": subscription.expire_at,
                "status": (
                    UserStatus.DISABLED
                    if subscription.status is SubscriptionStatus.DISABLED
                    else None
                ),
                "traffic_limit_bytes": subscription.effective_traffic_limit * GB_TO_BYTES,
                "traffic_limit_strategy": subscription.traffic_limit_strategy,
                "hwid_device_limit": subscription.effective_device_limit,
                "active_internal_squads": subscription.internal_squads or None,
                "external_squad_uuid": subscription.external_squad,
                "tag": subscription.tag,
            }
        )
    )


def configure_for_plan(
    remnawave_user_id: int,
    plan: PlanSnapshot,
    expire_at: datetime,
    *,
    enable: bool = False,
) -> UpdateUserRequestDto:
    return panel_update(
        id=remnawave_user_id,
        # None keeps the panel's status
        status=UserStatus.ACTIVE if enable else None,
        expire_at=expire_at,
        traffic_limit_bytes=plan.traffic_limit * GB_TO_BYTES,
        hwid_device_limit=plan.device_limit,
        traffic_limit_strategy=plan.traffic_limit_strategy,
        active_internal_squads=plan.internal_squads or None,
        external_squad_uuid=plan.external_squad,
        tag=plan.tag,
    )


def configure_for_renewal(
    subscription: SubscriptionDto, plan: PlanSnapshot
) -> UpdateUserRequestDto:
    """The plan's current terms for a renewed subscription; its add-ons stay on top."""
    # from the entitlements: on an unlimited row effective - base is 0 whatever they add
    bonus = {
        addon_type: sum(e.amount for e in subscription.addon_entitlements if e.type is addon_type)
        for addon_type in AddOnType
    }
    traffic_bonus = bonus[AddOnType.TRAFFIC]
    device_bonus = bonus[AddOnType.DEVICES]
    return panel_update(
        id=subscription.remnawave_user_id,
        # 0 = unlimited; an add-on on top must not turn it into a finite cap
        traffic_limit_bytes=(plan.traffic_limit and plan.traffic_limit + traffic_bonus)
        * GB_TO_BYTES,
        hwid_device_limit=plan.device_limit and plan.device_limit + device_bonus,
        traffic_limit_strategy=plan.traffic_limit_strategy,
        active_internal_squads=plan.internal_squads or None,
        external_squad_uuid=plan.external_squad,
    )


def grace_panel_update(
    subscription: SubscriptionDto,
    settings: GraceSettingsDto,
    grace_until: datetime,
    **extra: Any,
) -> UpdateUserRequestDto:
    fields: dict[str, Any] = {
        "status": UserStatus.ACTIVE,
        "expire_at": grace_until,
        "traffic_limit_bytes": settings.traffic_limit_mb * MB_TO_BYTES,
        "traffic_limit_strategy": settings.traffic_limit_strategy,
        "hwid_device_limit": subscription.effective_device_limit,
        "active_internal_squads": settings.internal_squads or None,
    }
    # no squad or tag in the grace settings keeps the subscription's own on the panel
    if settings.external_squad is not None:
        fields["external_squad_uuid"] = settings.external_squad
    if settings.tag:
        fields["tag"] = settings.tag
    return panel_update(**(fields | extra))


# what the panel says about the request itself; anything else (auth, rate limit,
# outage) may pass on a retry
_REFUSAL_STATUSES: Final[frozenset[int]] = frozenset({400, 404, 409, 422})


def panel_user_gone(error: Exception) -> bool:
    return (
        isinstance(error, RemnawaveNotFoundError)
        and error.code == RemnawaveErrorCode.USER_NOT_FOUND
    )


def panel_refused(error: Exception) -> bool:
    return isinstance(error, RemnawaveApiError) and error.status_code in _REFUSAL_STATUSES


def restore(
    subscription: SubscriptionDto,
    owner: Optional[UserDto] = None,
    *,
    grace: Optional[GraceSettingsDto] = None,
    left_grace: Optional[GraceSettingsDto] = None,
) -> UpdateUserRequestDto:
    """Puts a panel user back to the configuration its subscription row holds,
    or to the grace config while the row is in grace; with `owner`, also to the
    owner's Telegram and email where the shop knows them. `left_grace`: the grace
    settings the row is just leaving, whose squads the panel still holds."""
    contact: dict[str, Any] = {
        "id": subscription.remnawave_user_id,
        "telegram_id": owner.telegram_id if owner else None,
        "email": owner.email if owner else None,
    }
    if subscription.grace_until is not None and grace is not None:
        if subscription.status is SubscriptionStatus.DISABLED:
            # an admin's switch-off outlives grace: None leaves the panel's status as is
            contact["status"] = None
        if subscription.grace_until <= datetime_now():
            # grace ended, End hasn't run yet: a past expireAt is refused, and ACTIVE
            # would revive the panel user it expired
            contact["expire_at"] = None
            contact["status"] = None
        return grace_panel_update(subscription, grace, subscription.grace_until, **contact)
    return panel_update(
        **contact,
        # the panel refuses a past expireAt on update and expires the user itself
        expire_at=subscription.expire_at if subscription.expire_at > datetime_now() else None,
        traffic_limit_bytes=subscription.effective_traffic_limit * GB_TO_BYTES,
        hwid_device_limit=subscription.effective_device_limit,
        traffic_limit_strategy=subscription.traffic_limit_strategy,
        # left out when empty, the panel keeps its own; out of grace those are grace's
        active_internal_squads=(
            subscription.internal_squads
            if left_grace is not None and left_grace.internal_squads
            else subscription.internal_squads or None
        ),
        external_squad_uuid=subscription.external_squad,
        tag=subscription.tag,
    )


async def revert_panel_to_owner(
    sdk: RemnawaveSDK,
    repository: SubscriptionRepository,
    uow: UnitOfWork,
    current: SubscriptionDto,
    grace: Optional[GraceSettingsDto] = None,
) -> None:
    """Best effort: puts the panel user back to the row that owns it in the
    database, `current` or the newer row that took the panel user over."""

    with suppress(Exception):
        owner: Optional[SubscriptionDto] = None
        with suppress(Exception):
            async with uow:
                owner = await repository.find_by_remnawave_user_id(current.remnawave_user_id)
        # unreadable database: current is the best guess left
        owner = owner or current
        if owner.status is SubscriptionStatus.DELETED:
            return
        await sdk.users.update_user(restore(owner, grace=grace))


def subscription_row(
    data: PurchaseSubscriptionDto,
    panel_user: UserResponseDto,
    expire_at: Optional[datetime] = None,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    term_anchor_day: Optional[int] = None,
) -> CreateSubscriptionDto:
    plan = data.plan_snapshot
    return CreateSubscriptionDto(
        user_id=data.user_id,
        remnawave_user_id=panel_user.id,
        status=status,
        traffic_limit=plan.traffic_limit,
        device_limit=plan.device_limit,
        traffic_limit_strategy=plan.traffic_limit_strategy,
        is_trial=plan.is_trial,
        internal_squads=plan.internal_squads,
        external_squad=plan.external_squad,
        tag=plan.tag,
        expire_at=expire_at or panel_user.expire_at,
        url=panel_user.subscription_url,
        plan_snapshot=plan,
        panel_created_at=panel_user.created_at,
        term_anchor_day=term_anchor_day,
    )


async def reset_traffic_if_paid(
    sdk: RemnawaveSDK,
    lifecycle: SubscriptionLifecycleService,
    uow: UnitOfWork,
    data: PurchaseSubscriptionDto,
    subscription: SubscriptionDto,
    *,
    pace_of: Optional[SubscriptionDto] = None,
) -> SubscriptionDto:
    """`pace_of`, the renewed subscription as it was before, when the plan change credit
    counts traffic: how far its traffic ran ahead of time carries over the reset."""
    if not data.reset_traffic:
        return subscription
    pace = (
        await _pace_before_reset(sdk, lifecycle, uow, data, pace_of)
        if pace_of is not None
        else Decimal(1)
    )
    # a failure leaves the old counter running, nothing to revert
    try:
        panel_user = await sdk.users.reset_user_traffic(subscription.remnawave_user_id)
    except Exception:
        logger.exception(f"Failed to reset traffic of subscription '{subscription.id}'")
        return subscription
    if data.idempotency_key is not None:
        # only once the panel took it: the plan change credit counts traffic from here
        try:
            async with uow:
                await lifecycle.record_traffic_reset(
                    subscription.id, data.idempotency_key, at=datetime_now(), pace=pace
                )
        except Exception:
            logger.exception(f"Failed to record the traffic reset of '{subscription.id}'")
    return await sync_status_after_reset(lifecycle, uow, subscription, panel_user)


async def _pace_before_reset(
    sdk: RemnawaveSDK,
    lifecycle: SubscriptionLifecycleService,
    uow: UnitOfWork,
    data: PurchaseSubscriptionDto,
    subscription: SubscriptionDto,
) -> Decimal:
    """How far traffic ran ahead of time before a renewal zeroes the counter, so a
    renewal bought only for that unlocks no credit; counted against the limit it had."""
    if data.idempotency_key is None or traffic_left_share(subscription, 0) is None:
        return Decimal(1)
    try:
        panel_user = await sdk.users.get_user_by_id(subscription.remnawave_user_id)
        left = traffic_left_share(subscription, int(panel_user.used_traffic_bytes))
        if left is None:
            return Decimal(1)
        async with uow:
            return await lifecycle.pace_before_reset(
                subscription.id, data.idempotency_key, left, datetime_now()
            )
    except Exception:
        # in the customer's favour, as if the traffic went no faster than time
        logger.exception(f"Failed to read the traffic of '{subscription.id}' before a reset")
        return Decimal(1)


async def sync_status_after_reset(
    lifecycle: SubscriptionLifecycleService,
    uow: UnitOfWork,
    subscription: SubscriptionDto,
    panel_user: UserResponseDto,
) -> SubscriptionDto:
    # the reset lifts LIMITED, but the row holds the panel's state from before
    # it, and the webhook that would sync it may never come
    if subscription.status is not SubscriptionStatus.LIMITED or (
        SubscriptionStatus(panel_user.status) is not SubscriptionStatus.ACTIVE
    ):
        return subscription
    try:
        async with uow:
            return await lifecycle.sync_status(subscription.id, SubscriptionStatus.ACTIVE)
    except Exception:
        logger.exception(f"Failed to sync status of subscription '{subscription.id}'")
        return subscription
