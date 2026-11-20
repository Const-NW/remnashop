from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Callable, Optional

from loguru import logger
from remnapy import RemnawaveSDK
from remnapy.models import ExtendUserRequestDto, UpdateUserRequestDto

from app.core.actor import Actor
from app.core.db.uow import UnitOfWork
from app.core.enums import SubscriptionStatus
from app.core.error_codes import ErrorCode
from app.core.exceptions import ValidationError
from app.core.ports import EventPublisher
from app.core.utils.time import datetime_now, truncate_to_ms
from app.modules.pricing import PricingRepository
from app.modules.subscriptions.domain import OrderNotPaidError
from app.modules.subscriptions.dto import PurchaseSubscriptionDto, SubscriptionDto
from app.modules.subscriptions.eligibility import get_owned_target
from app.modules.subscriptions.events import SubscriptionRenewedEvent
from app.modules.subscriptions.idempotency_repository import PurchaseIdempotencyRepository
from app.modules.subscriptions.lifecycle import SubscriptionLifecycleService
from app.modules.subscriptions.panel import (
    configure_for_renewal,
    panel_update,
    reset_traffic_if_paid,
    revert_panel_to_owner,
)
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.subscriptions.unlimited import (
    expiry_after,
    is_unlimited_expiry,
    term_anchor,
    term_days,
)


class RenewPurchase:
    def __init__(
        self,
        subscription_repository: SubscriptionRepository,
        idempotency_repository: PurchaseIdempotencyRepository,
        remnawave_sdk: RemnawaveSDK,
        uow: UnitOfWork,
        event_publisher: EventPublisher,
        lifecycle_service: SubscriptionLifecycleService,
        pricing_repository: PricingRepository,
    ) -> None:
        self._subscription_repository = subscription_repository
        self._idempotency = idempotency_repository
        self._remnawave_sdk = remnawave_sdk
        self._uow = uow
        self._event_publisher = event_publisher
        self._lifecycle_service = lifecycle_service
        self._pricing_repository = pricing_repository

    async def run(
        self, actor: Actor, data: PurchaseSubscriptionDto, keep_key: Callable[[], None]
    ) -> SubscriptionDto:
        async with self._uow:
            if data.target_subscription_id is None:
                raise ValidationError(
                    "target_subscription_id is required for a RENEW",
                    code=ErrorCode.TARGET_SUBSCRIPTION_REQUIRED,
                )

            # validated unlocked, then the panel call, then lock, re-check and
            # apply: a row lock held across the panel call lasts up to its timeout
            target = await get_owned_target(
                self._subscription_repository, data.target_subscription_id, data.user_id
            )
            if target.status is SubscriptionStatus.DELETED:
                target = await self._successor(target)
            # before the panel call: refusing after it would leave the panel
            # extended and a pinned key a caller can only retry, never undo
            if data.refuse_disabled_target and target.status is SubscriptionStatus.DISABLED:
                raise ValidationError(
                    "Cannot renew a disabled subscription", code=ErrorCode.SUBSCRIPTION_DISABLED
                )
            if target.plan_snapshot.plan_id != data.plan_snapshot.plan_id:
                if not data.renew_other_plan:
                    raise ValidationError(
                        "Renewing a different plan is a CHANGE, not a RENEW",
                        code=ErrorCode.RENEW_PLAN_MISMATCH,
                    )
                # the old plan's terms and tag would land on the new plan's subscription
                data = replace(data, apply_current_plan=False)
            # nothing to add to: a paid order for it stays unfulfilled for an admin to settle
            if target.is_unlimited:
                raise ValidationError(
                    "An unlimited subscription can't be renewed",
                    code=ErrorCode.SUBSCRIPTION_UNLIMITED,
                )

        expire_at, added_days, anchor_day = await self._extend_panel(target, data, keep_key)

        tag_applied, terms_applied = await self._apply_current_plan(data, target)

        try:
            subscription, renewed = await self._finish(
                actor,
                data,
                target,
                expire_at,
                added_days=added_days,
                anchor_day=anchor_day,
                tag_applied=tag_applied,
                terms_applied=terms_applied,
            )
        except OrderNotPaidError:
            # refunded while the panel was being extended: take the extension back
            await revert_panel_to_owner(
                self._remnawave_sdk, self._subscription_repository, self._uow, target
            )
            raise
        if not renewed:
            return subscription
        # a paid renewal is the way back from an exhausted NO_RESET limit
        return await reset_traffic_if_paid(
            self._remnawave_sdk, self._subscription_repository, self._uow, data, subscription
        )

    async def _extend_panel(
        self,
        target: SubscriptionDto,
        data: PurchaseSubscriptionDto,
        keep_key: Callable[[], None],
    ) -> tuple[datetime, int, Optional[int]]:
        """The panel's new expiry, the days it added and the anchor day it leaves."""
        if data.idempotency_key is None and data.duration_days == 0:
            # the panel's extend takes days, not "unlimited"
            updated = await self._remnawave_sdk.users.update_user(
                UpdateUserRequestDto(
                    id=target.remnawave_user_id,
                    expire_at=expiry_after(datetime_now(), 0, exact_days=True),
                )
            )
            expire_at, added_days, anchor_day = updated.expire_at, 0, None
        elif data.idempotency_key is None:
            # No retry path re-runs a keyless grant, so the panel's atomic
            # relative extend is safe here. A calendar term's days are counted
            # from the row's expiry, as the panel's extend counts them from its
            # own: a row behind the panel still adds them all, its end a day or
            # two off the same date
            base = max(target.expire_at, datetime_now())
            anchor = _live_anchor(target)
            added_days = term_days(
                base,
                expiry_after(
                    base, data.duration_days, exact_days=data.exact_days, anchor_day=anchor
                ),
                data.duration_days,
            )
            anchor_day = term_anchor(
                base, data.duration_days, exact_days=data.exact_days, anchor_day=anchor
            )
            extended = await self._remnawave_sdk.users.extend_user(
                target.remnawave_user_id, ExtendUserRequestDto(days=added_days)
            )
            expire_at = extended.expire_at
        else:
            expire_at, added_days, anchor_day = await self._apply_pinned_renewal(
                target, data.idempotency_key, data.claim_token, data, keep_key
            )
        return expire_at, added_days, anchor_day

    async def _successor(self, target: SubscriptionDto) -> SubscriptionDto:
        """What a renewal of a deleted subscription goes to: the row a plan change put in
        its place, on the same panel user. The plan check below decides whether it may."""
        successor = await self._subscription_repository.find_by_remnawave_user_id(
            target.remnawave_user_id
        )
        if (
            successor is None
            or successor.status is SubscriptionStatus.DELETED
            or successor.user_id != target.user_id
        ):
            raise ValidationError(
                "Cannot renew a deleted subscription", code=ErrorCode.SUBSCRIPTION_DELETED
            )
        return successor

    async def _finish(
        self,
        actor: Actor,
        data: PurchaseSubscriptionDto,
        target: SubscriptionDto,
        expire_at: datetime,
        *,
        added_days: int,
        anchor_day: Optional[int],
        tag_applied: bool,
        terms_applied: bool,
    ) -> tuple[SubscriptionDto, bool]:
        async with self._uow:
            if data.idempotency_key is not None:
                finished_id = await self._idempotency.lock(data.idempotency_key, data.claim_token)
                if finished_id is not None:
                    return await self._subscription_repository.get_by_id(finished_id), False
            locked = await self._subscription_repository.get_by_id_for_update(target.id)
            if locked.status is SubscriptionStatus.DELETED:
                # deleted by a concurrent plan change or admin while the panel
                # call ran; the extend landed on a live panel user, so there's
                # nothing to undo and the next sync settles the difference
                raise ValidationError(
                    "Subscription was deleted while this renewal was in flight",
                    code=ErrorCode.SUBSCRIPTION_DELETED,
                )
            paid = await self._paid_in_snapshot_currency(data, locked)
            subscription = await self._lifecycle_service.renew(
                locked.id,
                expire_at,
                paid=paid,
                days=added_days,
                previous_expire_at=target.expire_at,
            )
            # plain days and day terms clear it: their end sits off any anchor
            if anchor_day != subscription.term_anchor_day:
                subscription = await self._subscription_repository.set_term_anchor_day(
                    subscription.id, anchor_day
                )
            # only what reached the panel: the row must not claim a tag the panel lacks
            if tag_applied and subscription.tag != data.plan_snapshot.tag:
                subscription = await self._subscription_repository.set_tag(
                    subscription.id, data.plan_snapshot.tag
                )
            if terms_applied:
                subscription = await self._subscription_repository.apply_plan_terms(
                    subscription.id, data.plan_snapshot
                )
            if data.idempotency_key is not None:
                await self._idempotency.link(
                    data.idempotency_key, subscription.id, data.claim_token
                )
            await self._event_publisher.publish(
                SubscriptionRenewedEvent(
                    user=actor,
                    subscription_id=subscription.id,
                    duration_days=data.duration_days,
                    added_days=added_days,
                    payment_key=data.idempotency_key,
                )
            )

        logger.info(
            f"{actor.log} Renewed subscription '{subscription.id}' by {data.duration_days}d"
        )
        return subscription, True

    async def _apply_current_plan(
        self, data: PurchaseSubscriptionDto, target: SubscriptionDto
    ) -> tuple[bool, bool]:
        """(tag applied, terms applied) in the panel; the row follows only what landed.
        The days are paid for: a failure here must not hold them back."""
        if not data.apply_current_plan:
            return False, False
        tag_applied = False
        # a renewal takes the plan's current tag, as a new purchase would
        if data.plan_snapshot.tag != target.tag:
            try:
                await self._remnawave_sdk.users.update_user(
                    panel_update(id=target.remnawave_user_id, tag=data.plan_snapshot.tag)
                )
                tag_applied = True
            except Exception:
                logger.exception(f"Failed to give subscription '{target.id}' its plan's tag")
        terms_applied = False
        # the plan was edited since this subscription was sold: the renewal is paid at the
        # new price, so it moves to the new terms too
        if target.plan_snapshot.terms_differ(data.plan_snapshot):
            try:
                await self._remnawave_sdk.users.update_user(
                    configure_for_renewal(target, data.plan_snapshot)
                )
                terms_applied = True
            except Exception:
                logger.exception(f"Failed to give subscription '{target.id}' its plan's terms")
        return tag_applied, terms_applied

    async def _paid_in_snapshot_currency(
        self, data: PurchaseSubscriptionDto, target: SubscriptionDto
    ) -> Decimal:
        currency = target.plan_snapshot.currency
        if data.price_paid <= 0 or data.currency is currency:
            return data.price_paid
        rates = await self._pricing_repository.get_rate_table()
        return rates.convert(data.price_paid, data.currency, currency)

    async def _renewal_base(self, target: SubscriptionDto) -> datetime:
        """What the panel's extend adds to: max(expire_at, now)."""
        panel_user = await self._remnawave_sdk.users.get_user_by_id(target.remnawave_user_id)
        # made unlimited on the panel, its webhook not here yet
        if is_unlimited_expiry(panel_user.expire_at):
            raise ValidationError(
                "An unlimited subscription can't be renewed",
                code=ErrorCode.SUBSCRIPTION_UNLIMITED,
            )
        return max(panel_user.expire_at, datetime_now())

    async def _apply_pinned_renewal(
        self,
        target: SubscriptionDto,
        key: str,
        token: Optional[datetime],
        data: PurchaseSubscriptionDto,
        keep_key: Callable[[], None],
    ) -> tuple[datetime, int, Optional[int]]:
        """The expiry, the days it added and the anchor day it leaves. The panel's
        extend as an absolute value pinned before the call, so a retry can't extend
        twice. Known gap: an extend from elsewhere between read and write is
        overwritten."""
        async with self._uow:
            pinned = await self._idempotency.find_pending_renew_expire_at(key)
            added_days = await self._idempotency.find_pending_renew_added_days(key)
            anchor_day = await self._idempotency.find_pending_renew_anchor_day(key)
        anchor = _live_anchor(target)
        if pinned is None:
            base = await self._renewal_base(target)
            # ms-truncated so the pinned value compares equal to the panel's
            pinned = truncate_to_ms(
                expiry_after(
                    base, data.duration_days, exact_days=data.exact_days, anchor_day=anchor
                )
            )
            anchor_day = term_anchor(
                base, data.duration_days, exact_days=data.exact_days, anchor_day=anchor
            )
            added_days = term_days(base, pinned, data.duration_days)
            async with self._uow:
                await self._idempotency.pin_renew_expire_at(
                    key, pinned, added_days, anchor_day, token
                )
            keep_key()
        else:
            keep_key()
            if added_days is None:
                # pinned before the column existed, when a term was plain days
                added_days = data.duration_days
            panel_user = await self._remnawave_sdk.users.get_user_by_id(target.remnawave_user_id)
            if panel_user.expire_at >= pinned:
                return pinned, added_days, anchor_day

        await self._remnawave_sdk.users.update_user(
            UpdateUserRequestDto(id=target.remnawave_user_id, expire_at=pinned)
        )
        return pinned, added_days, anchor_day


def _live_anchor(target: SubscriptionDto) -> Optional[int]:
    """A lapsed subscription starts a new term today: its old anchor doesn't apply."""
    return target.term_anchor_day if target.expire_at > datetime_now() else None
