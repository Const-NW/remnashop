from dataclasses import replace
from datetime import datetime
from typing import Optional

from loguru import logger
from remnapy import RemnawaveSDK
from remnapy.models import ExtendUserRequestDto, UpdateUserRequestDto, UserResponseDto

from app.core.actor import Actor
from app.core.db.uow import UnitOfWork
from app.core.enums import SubscriptionStatus
from app.core.error_codes import ErrorCode
from app.core.exceptions import ValidationError
from app.core.ports import EventPublisher
from app.core.utils.time import datetime_now, truncate_to_ms
from app.modules.settings import GraceSettingsDto, SettingsRepository
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
    restore,
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
        settings_repository: SettingsRepository,
    ) -> None:
        self._subscription_repository = subscription_repository
        self._idempotency = idempotency_repository
        self._remnawave_sdk = remnawave_sdk
        self._uow = uow
        self._event_publisher = event_publisher
        self._lifecycle_service = lifecycle_service
        self._settings_repository = settings_repository

    async def run(self, actor: Actor, data: PurchaseSubscriptionDto) -> SubscriptionDto:
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
                target = await self._successor(target, data)
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
            # a refund before the renewal lands puts the panel back on it
            grace: Optional[GraceSettingsDto] = None
            if target.grace_until is not None:
                grace = await self._settings_repository.get_grace_settings()

        expire_at, added_days, anchor_day = await self._extend_panel(target, data)

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
                self._remnawave_sdk, self._subscription_repository, self._uow, target, grace
            )
            raise
        if not renewed:
            return subscription
        # a paid renewal is the way back from an exhausted NO_RESET limit
        return await reset_traffic_if_paid(
            self._remnawave_sdk,
            self._lifecycle_service,
            self._uow,
            data,
            subscription,
            pace_of=target if data.reset_traffic and await self._counts_traffic() else None,
        )

    async def _counts_traffic(self) -> bool:
        """Whether a plan change credit is cut by traffic used: only then is the traffic
        read before a reset."""
        async with self._uow:
            shop = await self._settings_repository.get_shop_settings()
        return shop.plan_change_credit_enabled and shop.plan_change_traffic_cap_enabled

    async def _extend_panel(
        self,
        target: SubscriptionDto,
        data: PurchaseSubscriptionDto,
    ) -> tuple[datetime, int, Optional[int]]:
        """The panel's new expiry, the days it added and the anchor day it leaves."""
        if data.idempotency_key is None and data.duration_days == 0:
            # the panel's extend takes days, not "unlimited"
            updated = await self._set_expiry(
                target, data, expiry_after(datetime_now(), 0, exact_days=True)
            )
            expire_at, added_days, anchor_day = updated.expire_at, 0, None
        elif data.idempotency_key is None:
            # No retry path re-runs a keyless grant, so the panel's atomic
            # relative extend is safe here. A calendar term's days are counted
            # from the row's expiry, as the panel's extend counts them from its
            # own: a row behind the panel still adds them all, its end a day or
            # two off the same date
            if target.grace_until is not None:
                base, anchor = _grace_base(target, data)
            else:
                base, anchor = max(target.expire_at, datetime_now()), _live_anchor(target)
            end = expiry_after(
                base, data.duration_days, exact_days=data.exact_days, anchor_day=anchor
            )
            added_days = term_days(base, end, data.duration_days)
            anchor_day = term_anchor(
                base, data.duration_days, exact_days=data.exact_days, anchor_day=anchor
            )
            if target.grace_until is not None:
                # the panel's extend would add to the grace end
                expire_at = (await self._set_expiry(target, data, end)).expire_at
            else:
                extended = await self._remnawave_sdk.users.extend_user(
                    target.remnawave_user_id, ExtendUserRequestDto(days=added_days)
                )
                expire_at = extended.expire_at
        else:
            expire_at, added_days, anchor_day = await self._apply_pinned_renewal(
                target, data.idempotency_key, data.claim_token, data
            )
        return expire_at, added_days, anchor_day

    async def _successor(
        self, target: SubscriptionDto, data: PurchaseSubscriptionDto
    ) -> SubscriptionDto:
        """What a paid renewal of a deleted subscription goes to: the row a plan change
        put in its place, on the same panel user. The plan check below decides whether
        it may. An admin's keyless grant names its row and gets no stand-in."""
        successor = await self._subscription_repository.find_by_remnawave_user_id(
            target.remnawave_user_id
        )
        if (
            data.idempotency_key is None
            or successor is None
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
            # grace entered, or pushed again, while the panel call ran: the grace config
            # may sit on the panel over this renewal's write
            left_grace: Optional[GraceSettingsDto] = None
            if target.grace_until is not None or locked.grace_until is not None:
                left_grace = await self._settings_repository.get_grace_settings()
            subscription = await self._lifecycle_service.renew(
                locked.id,
                expire_at,
                paid=data.price_paid,
                currency=data.currency,
                previous_expire_at=target.expire_at,
                starts_at=_term_start(target, data),
                payment_key=data.idempotency_key,
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

        if left_grace is not None:
            await self._push_out_of_grace(subscription, left_grace)

        logger.info(
            f"{actor.log} Renewed subscription '{subscription.id}' by {data.duration_days}d"
        )
        return subscription, True

    async def _push_out_of_grace(
        self, subscription: SubscriptionDto, grace: GraceSettingsDto
    ) -> None:
        # overwrites a grace push that landed after this renewal's write, whether grace
        # was entered mid-flight or pushed again. Known gap: grace entered mid-flight,
        # the days may still count from the grace end the panel showed. The renewal is
        # committed, so a failed push is left to the restore cron
        try:
            await self._remnawave_sdk.users.update_user(restore(subscription, left_grace=grace))
        except Exception:
            logger.exception(f"Failed to take subscription '{subscription.id}' out of grace")
            async with self._uow:
                # the minute cron pushes it again
                await self._subscription_repository.mark_panel_restore_due(
                    subscription.id, datetime_now()
                )

    async def _apply_current_plan(
        self, data: PurchaseSubscriptionDto, target: SubscriptionDto
    ) -> tuple[bool, bool]:
        """(tag applied, terms applied) in the panel; the row follows only what landed.
        The days are paid for: a failure here must not hold them back."""
        if target.grace_until is not None:
            # went with the expiry (_set_expiry)
            terms = data.apply_current_plan and target.plan_snapshot.terms_differ(
                data.plan_snapshot
            )
            return data.apply_current_plan, terms
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
    ) -> tuple[datetime, int, Optional[int]]:
        """The expiry, the days it added and the anchor day it leaves. The panel's
        extend as an absolute value pinned before the call, so a retry can't extend
        twice. Known gap: an extend from elsewhere between read and write is
        overwritten."""
        async with self._uow:
            pinned = await self._idempotency.find_pending_renew_expire_at(key)
            added_days = await self._idempotency.find_pending_renew_added_days(key)
            anchor_day = await self._idempotency.find_pending_renew_anchor_day(key)
            pinned_for = await self._idempotency.find_pending_renew_subscription_id(key)
        replacing = pinned
        if pinned is not None and pinned_for is not None and pinned_for != target.id:
            panel_user = await self._remnawave_sdk.users.get_user_by_id(target.remnawave_user_id)
            # worked out from the row a plan change replaced, whose expiry the change
            # overwrote on the panel: counted again from the successor's, unless the
            # first attempt's write landed after the change
            if panel_user.expire_at != pinned:
                pinned = None
            elif added_days is not None:
                # moved to the successor so a refund finds it; the days and anchor
                # stay the renewal's own, the successor's row may already show the pin
                async with self._uow:
                    if not await self._idempotency.pin_renew_expire_at(
                        key,
                        pinned,
                        added_days,
                        anchor_day,
                        token,
                        subscription_id=target.id,
                        replacing=pinned,
                    ):
                        # a concurrent resume re-pinned first: its pin stands
                        pinned = await self._idempotency.find_pending_renew_expire_at(key)
                        added_days = await self._idempotency.find_pending_renew_added_days(key)
                        anchor_day = await self._idempotency.find_pending_renew_anchor_day(key)
        if pinned is None:
            if target.grace_until is not None:
                # the panel's date is the grace end, not what the renewal adds to
                base, anchor = _grace_base(target, data)
            else:
                base = await self._renewal_base(target)
                anchor = _live_anchor(target)
            # ms-truncated so the pinned value compares equal to the panel's
            fresh = truncate_to_ms(
                expiry_after(
                    base, data.duration_days, exact_days=data.exact_days, anchor_day=anchor
                )
            )
            fresh_anchor_day = term_anchor(
                base, data.duration_days, exact_days=data.exact_days, anchor_day=anchor
            )
            fresh_days = term_days(base, fresh, data.duration_days)
            async with self._uow:
                won = await self._idempotency.pin_renew_expire_at(
                    key,
                    fresh,
                    fresh_days,
                    fresh_anchor_day,
                    token,
                    subscription_id=target.id,
                    replacing=replacing,
                )
                if not won:
                    # a concurrent resume re-pinned first: its pin stands
                    pinned = await self._idempotency.find_pending_renew_expire_at(key)
                    added_days = await self._idempotency.find_pending_renew_added_days(key)
                    anchor_day = await self._idempotency.find_pending_renew_anchor_day(key)
            if won:
                await self._set_expiry(target, data, fresh)
                return fresh, fresh_days, fresh_anchor_day
            assert pinned is not None  # the winner's pin, on a row still unlinked
        if added_days is None:
            # pinned before the column existed, when a term was plain days
            added_days = data.duration_days
        await self._reapply_pin(target, data, pinned)
        return pinned, added_days, anchor_day

    async def _reapply_pin(
        self, target: SubscriptionDto, data: PurchaseSubscriptionDto, pinned: datetime
    ) -> None:
        if target.grace_until is None:
            panel_user = await self._remnawave_sdk.users.get_user_by_id(target.remnawave_user_id)
            if panel_user.expire_at >= pinned:
                return
        # in grace the panel's date is the grace end, which may lie past the pin
        await self._set_expiry(target, data, pinned)

    async def _set_expiry(
        self, target: SubscriptionDto, data: PurchaseSubscriptionDto, expire_at: datetime
    ) -> UserResponseDto:
        """In grace the real config goes in the same write: an expiry alone echoes back
        as an admin's extension, and the sync would take the grace config into the row."""
        request = UpdateUserRequestDto(id=target.remnawave_user_id, expire_at=expire_at)
        if target.grace_until is not None:
            async with self._uow:
                grace = await self._settings_repository.get_grace_settings()
            request = _out_of_grace(target, data, grace).model_copy(update={"expire_at": expire_at})
        return await self._remnawave_sdk.users.update_user(request)


def _live_anchor(target: SubscriptionDto) -> Optional[int]:
    """A lapsed subscription starts a new term today: its old anchor doesn't apply."""
    return target.term_anchor_day if target.expire_at > datetime_now() else None


def _term_start(target: SubscriptionDto, data: PurchaseSubscriptionDto) -> datetime:
    """Where the renewed term starts, for its credit: a lapsed term restarts today,
    a paid one in grace from the real expiry it counts from."""
    if target.grace_until is not None:
        return _grace_base(target, data)[0]
    return max(target.expire_at, datetime_now())


def _out_of_grace(
    target: SubscriptionDto, data: PurchaseSubscriptionDto, grace: GraceSettingsDto
) -> UpdateUserRequestDto:
    """The config a renewal takes the panel back to from grace: the row's, with an
    order's plan tag, and the plan's terms only where they changed since the sale."""
    request = restore(target, left_grace=grace)
    if not data.apply_current_plan:
        return request
    plan = data.plan_snapshot
    request = request.model_copy(update={"tag": plan.tag})
    if not target.plan_snapshot.terms_differ(plan):
        return request
    # an empty list too: left out, the panel would keep the grace squads
    return request.model_copy(
        update=configure_for_renewal(target, plan).model_dump(exclude_unset=True)
        | {"active_internal_squads": plan.internal_squads}
    )


def _grace_base(
    target: SubscriptionDto, data: PurchaseSubscriptionDto
) -> tuple[datetime, Optional[int]]:
    """What a renewal in grace counts from, and its anchor: a paid one from the real
    expiry, the grace days already used are not a gift; free days and a term that
    would still end in the past from today."""
    if (
        data.price_paid > 0
        and expiry_after(
            target.expire_at,
            data.duration_days,
            exact_days=data.exact_days,
            anchor_day=target.term_anchor_day,
        )
        > datetime_now()
    ):
        return target.expire_at, target.term_anchor_day
    return datetime_now(), None
