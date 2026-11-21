from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from loguru import logger
from remnapy import RemnawaveSDK

from app.core.actor import Actor
from app.core.constants import ZERO_AMOUNT
from app.core.db.uow import UnitOfWork
from app.core.enums import CreditSegmentKind, SubscriptionStatus
from app.core.error_codes import ErrorCode
from app.core.exceptions import ValidationError
from app.core.ports import EventPublisher
from app.core.utils.time import datetime_now
from app.modules.subscriptions.change_credit import ChangeCreditCalculator
from app.modules.subscriptions.domain import SupersededError
from app.modules.subscriptions.dto import PurchaseSubscriptionDto, SubscriptionDto
from app.modules.subscriptions.eligibility import get_owned_target
from app.modules.subscriptions.events import SubscriptionChangedEvent
from app.modules.subscriptions.idempotency_repository import PurchaseIdempotencyRepository
from app.modules.subscriptions.lifecycle import SubscriptionLifecycleService
from app.modules.subscriptions.panel import (
    configure_for_plan,
    reset_traffic_if_paid,
    restore,
    revert_panel_to_owner,
    subscription_row,
)
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.subscriptions.unlimited import expiry_after, term_anchor, term_days


class _TargetMovedError(Exception):
    """The target moved after its panel user was configured from it: a renewal
    shifted expire_at (the credit is stale), or an addon grant pushed its own
    limits over this change's."""

    def __init__(self, current: SubscriptionDto) -> None:
        self.current = current


@dataclass(frozen=True, kw_only=True)
class _NewTerm:
    starts_at: datetime
    # where the new plan's own term ends; the carried credit runs on to expire_at
    term_end: datetime
    expire_at: datetime
    carried: Decimal = ZERO_AMOUNT
    credit_days: int = 0
    anchor_day: Optional[int] = None
    # the new term's own days, for the event; the credit came from the old one
    own_days: int = 0


class ChangePurchase:
    def __init__(
        self,
        subscription_repository: SubscriptionRepository,
        idempotency_repository: PurchaseIdempotencyRepository,
        remnawave_sdk: RemnawaveSDK,
        uow: UnitOfWork,
        event_publisher: EventPublisher,
        change_credit_calculator: ChangeCreditCalculator,
        lifecycle_service: SubscriptionLifecycleService,
    ) -> None:
        self._subscription_repository = subscription_repository
        self._idempotency = idempotency_repository
        self._remnawave_sdk = remnawave_sdk
        self._uow = uow
        self._event_publisher = event_publisher
        self._change_credit_calculator = change_credit_calculator
        self._lifecycle_service = lifecycle_service

    async def run(self, actor: Actor, data: PurchaseSubscriptionDto) -> SubscriptionDto:
        if data.target_subscription_id is None:
            raise ValidationError(
                "target_subscription_id is required for a CHANGE",
                code=ErrorCode.TARGET_SUBSCRIPTION_REQUIRED,
            )
        if data.duration_days < 0:
            raise ValidationError(
                "duration_days can't be negative", code=ErrorCode.DURATION_NOT_POSITIVE
            )
        # the retry's update_user overwrites the stale expire_at the first one set
        with suppress(_TargetMovedError):
            return await self._attempt(actor, data, data.target_subscription_id)
        try:
            return await self._attempt(actor, data, data.target_subscription_id)
        except _TargetMovedError as moved:
            with suppress(Exception):
                await self._remnawave_sdk.users.update_user(restore(moved.current))
            raise SupersededError(
                "Subscription kept changing while this request was in flight",
                code=ErrorCode.PURCHASE_SUPERSEDED,
            ) from None

    async def _attempt(
        self, actor: Actor, data: PurchaseSubscriptionDto, target_id: int
    ) -> SubscriptionDto:
        async with self._uow:
            old = await get_owned_target(self._subscription_repository, target_id, data.user_id)
            if old.status is SubscriptionStatus.DELETED:
                raise ValidationError(
                    "Cannot change a deleted subscription", code=ErrorCode.SUBSCRIPTION_DELETED
                )

        term = await self._new_term(old, data)
        plan, final_expire_at, credit_days = data.plan_snapshot, term.expire_at, term.credit_days

        # keeps old's panel user (same id, same subscription_url the client
        # already has) and changes only the plan config (limits, squads,
        # expiry); the local row is replaced below
        panel_user = await self._remnawave_sdk.users.update_user(
            configure_for_plan(old.remnawave_user_id, plan, final_expire_at)
        )

        try:
            async with self._uow:
                # a second plan change or a delete may have raced this one

                locked_old = await self._subscription_repository.get_by_id_for_update(old.id)
                if locked_old.status is SubscriptionStatus.DELETED:
                    raise SupersededError(
                        "Subscription was already changed or deleted while this request "
                        "was in flight",
                        code=ErrorCode.PURCHASE_SUPERSEDED,
                    )
                if (
                    locked_old.expire_at != old.expire_at
                    or locked_old.addon_entitlements != old.addon_entitlements
                ):
                    raise _TargetMovedError(locked_old)
                await self._subscription_repository.mark_deleted(old.id)
                updated = await self._subscription_repository.create(
                    subscription_row(
                        data,
                        panel_user,
                        expire_at=final_expire_at,
                        # an admin change can reach a disabled or expired panel user,
                        # which configure_for_plan leaves as it is
                        status=SubscriptionStatus(panel_user.status),
                        term_anchor_day=term.anchor_day,
                    )
                )
                await self._record_credit(old, updated, data, term)
                if data.idempotency_key is not None:
                    await self._idempotency.link(data.idempotency_key, updated.id, data.claim_token)
                if data.publish_event:
                    await self._event_publisher.publish(
                        SubscriptionChangedEvent(
                            user=actor,
                            subscription_id=updated.id,
                            previous_subscription_id=old.id,
                            previous_plan_snapshot=old.plan_snapshot.to_json(),
                            payment_key=data.idempotency_key,
                            added_days=None if data.keep_expire_at else term.own_days,
                        )
                    )
        except SupersededError:
            # the row that replaced old owns the panel user now; give it its config back
            await revert_panel_to_owner(
                self._remnawave_sdk, self._subscription_repository, self._uow, old
            )
            raise
        except _TargetMovedError:
            raise
        except Exception:
            # no orphan: update_user changed old's own panel user. Best-effort
            # revert, so a DB failure doesn't leave the still-ACTIVE `old` on a
            # plan config nobody paid for
            with suppress(Exception):
                await self._remnawave_sdk.users.update_user(restore(old))
            raise

        if data.duration_days == 0 and old.is_unlimited and data.price_paid == ZERO_AMOUNT:
            # the credit paid for it all: switching back and forth mustn't buy a reset
            data = replace(data, reset_traffic=False)
        updated = await reset_traffic_if_paid(
            self._remnawave_sdk, self._lifecycle_service, self._uow, data, updated
        )

        logger.info(
            f"{actor.log} Changed subscription '{old.id}' to plan '{plan.plan_id}' "
            f"({credit_days}d credit applied)"
        )
        return updated

    async def _new_term(self, old: SubscriptionDto, data: PurchaseSubscriptionDto) -> _NewTerm:
        """The new row's expiry, what it carries over from the old one and the day of
        the month its terms land on."""
        now = datetime_now()
        if data.keep_expire_at:
            return _NewTerm(
                starts_at=now,
                term_end=old.expire_at,
                expire_at=old.expire_at,
                anchor_day=old.term_anchor_day,
            )
        if data.duration_days == 0:
            unlimited = expiry_after(now, 0, exact_days=True)
            # checkout took what an unlimited one was paid off the price; carried
            # on, so the next change to unlimited credits all of it again
            carried = data.change_credit
            if carried is None:
                carried = await self._change_credit_calculator.unlimited_credit(old, data.currency)
            return _NewTerm(starts_at=now, term_end=unlimited, expire_at=unlimited, carried=carried)
        # depends on `old` alone, so the panel gets the final expire_at on the first call
        credit, credit_days = await self._change_credit_calculator.credit(
            old,
            currency=data.currency,
            list_price=data.base_price if data.base_price is not None else data.price_paid,
            duration_days=data.duration_days,
            now=now,
        )
        term_end = expiry_after(now, data.duration_days, exact_days=data.exact_days)
        return _NewTerm(
            starts_at=now,
            term_end=term_end,
            expire_at=term_end + timedelta(days=credit_days),
            carried=credit if credit_days > 0 else ZERO_AMOUNT,
            credit_days=credit_days,
            # credited days push the end off the calendar date; it anchors itself
            anchor_day=(
                term_anchor(now, data.duration_days, exact_days=data.exact_days)
                if credit_days == 0
                else None
            ),
            own_days=term_days(now, term_end, data.duration_days),
        )

    async def _record_credit(
        self,
        old: SubscriptionDto,
        new: SubscriptionDto,
        data: PurchaseSubscriptionDto,
        term: _NewTerm,
    ) -> None:
        """The new row's segments: its own term, then what it carries from the old row
        at the end of it."""
        if data.keep_expire_at:
            # the term didn't change, so neither did the money paid for it
            await self._lifecycle_service.carry_credit(old.id, new.id)
            return
        # an admin's change leaves the counter running from the reset before
        reset_at, pace = (
            (None, Decimal(1))
            if data.reset_traffic
            else await self._lifecycle_service.last_traffic_reset(old.id, term.starts_at)
        )
        await self._lifecycle_service.add_term(
            new.id,
            starts_at=term.starts_at,
            expire_at=term.term_end,
            paid=data.price_paid,
            currency=data.currency,
            payment_key=data.idempotency_key,
            traffic_reset_at=reset_at,
            traffic_pace=pace,
        )
        if term.carried > 0:
            await self._lifecycle_service.add_term(
                new.id,
                starts_at=term.term_end,
                expire_at=term.expire_at,
                paid=term.carried,
                currency=data.currency,
                payment_key=None,
                kind=CreditSegmentKind.CARRIED,
            )
