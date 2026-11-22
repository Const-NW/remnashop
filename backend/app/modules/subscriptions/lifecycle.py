from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from app.core.constants import TIME_1D
from app.core.enums import CreditSegmentKind, Currency, SubscriptionStatus
from app.modules.subscriptions.credit import traffic_pace
from app.modules.subscriptions.credit_segment_repository import CreditSegmentRepository
from app.modules.subscriptions.domain import SubscriptionState
from app.modules.subscriptions.dto import (
    CreateCreditSegmentDto,
    CreditSegmentDto,
    SubscriptionDto,
)
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.subscriptions.unlimited import is_unlimited_expiry


def _kind_for(paid: Decimal) -> CreditSegmentKind:
    return CreditSegmentKind.PURCHASE if paid > 0 else CreditSegmentKind.BONUS


def _seconds(starts_at: datetime, expire_at: datetime) -> int:
    return max(int((expire_at - starts_at).total_seconds()), 0)


def _dto_to_state(dto: SubscriptionDto) -> SubscriptionState:
    return SubscriptionState(
        id=dto.id,
        status=dto.status,
        expire_at=dto.expire_at,
        plan_snapshot=dto.plan_snapshot,
        traffic_limit=dto.traffic_limit,
        device_limit=dto.device_limit,
        traffic_limit_strategy=dto.traffic_limit_strategy,
        internal_squads=dto.internal_squads,
        external_squad=dto.external_squad,
        is_trial=dto.is_trial,
    )


class SubscriptionLifecycleService:
    """Every status/expire_at change to a Subscription, and the credit segments that
    record what its term was paid with, goes through here;
    PurchaseSubscriptionUseCase and SyncSubscriptionStatusUseCase never write
    row.status/row.expire_at themselves."""

    def __init__(
        self,
        subscription_repository: SubscriptionRepository,
        credit_segment_repository: CreditSegmentRepository,
    ) -> None:
        self._subscription_repository = subscription_repository
        self._credit_segment_repository = credit_segment_repository

    async def sync_status(
        self, subscription_id: int, target: SubscriptionStatus
    ) -> SubscriptionDto:
        dto = await self._subscription_repository.get_by_id_for_update(subscription_id)
        state = _dto_to_state(dto)
        new_state = state.sync_status(target)
        return await self._subscription_repository.apply_state(new_state)

    async def sync_expire_at(
        self, subscription_id: int, new_expire_at: datetime
    ) -> SubscriptionDto:
        dto = await self._subscription_repository.get_by_id_for_update(subscription_id)
        state = _dto_to_state(dto)
        new_state = state.sync_expire_at(new_expire_at)
        await self._subscription_repository.apply_state(new_state)
        # moved from outside the shop: the next calendar term counts from the new day
        return await self._subscription_repository.set_term_anchor_day(subscription_id, None)

    async def credit_segments(self, subscription_id: int) -> list[CreditSegmentDto]:
        return await self._credit_segment_repository.list_for(subscription_id)

    async def add_term(
        self,
        subscription_id: int,
        *,
        starts_at: datetime,
        expire_at: datetime,
        paid: Decimal,
        currency: Currency,
        payment_key: Optional[str],
        kind: Optional[CreditSegmentKind] = None,
        traffic_reset_at: Optional[datetime] = None,
        traffic_pace: Decimal = Decimal(1),
    ) -> None:
        """Records a term and what was paid for it; an unlimited one runs without end."""
        await self._add_segment(
            subscription_id,
            starts_at=starts_at,
            expire_at=expire_at,
            paid=paid,
            currency=currency,
            payment_key=payment_key,
            kind=kind,
            traffic_reset_at=traffic_reset_at,
            traffic_pace=traffic_pace,
        )

    async def _add_segment(
        self,
        subscription_id: int,
        *,
        starts_at: datetime,
        expire_at: datetime,
        paid: Decimal,
        currency: Currency,
        payment_key: Optional[str],
        kind: Optional[CreditSegmentKind] = None,
        traffic_reset_at: Optional[datetime] = None,
        traffic_pace: Decimal = Decimal(1),
    ) -> CreditSegmentDto:
        unlimited = is_unlimited_expiry(expire_at)
        return await self._credit_segment_repository.create(
            CreateCreditSegmentDto(
                subscription_id=subscription_id,
                kind=kind or _kind_for(paid),
                paid=paid,
                currency=currency,
                seconds=0 if unlimited else _seconds(starts_at, expire_at),
                ends_at=None if unlimited else expire_at,
                payment_key=payment_key,
                # a term an admin gave without zeroing the counter: it still runs from
                # the reset before
                traffic_reset_at=traffic_reset_at,
                traffic_pace=traffic_pace,
            )
        )

    async def last_traffic_reset(
        self, subscription_id: int, now: datetime
    ) -> tuple[Optional[datetime], Decimal]:
        """When the traffic counter was last zeroed and the pace carried over it; without
        a reset on record, the start of the term running now."""
        segments = [
            segment
            for segment in await self._credit_segment_repository.list_for(subscription_id)
            if segment.is_live and segment.ends_at is not None
        ]
        resets = [segment for segment in segments if segment.traffic_reset_at is not None]
        if resets:
            last = max(
                resets, key=lambda s: s.traffic_reset_at.timestamp() if s.traffic_reset_at else 0.0
            )
            return last.traffic_reset_at, last.traffic_pace
        running = [
            segment.ends_at - timedelta(seconds=segment.seconds)
            for segment in segments
            if segment.ends_at is not None
            and segment.ends_at - timedelta(seconds=segment.seconds) <= now < segment.ends_at
        ]
        return (min(running) if running else None), Decimal(1)

    async def pace_before_reset(
        self, subscription_id: int, payment_key: str, traffic_left: Decimal, now: datetime
    ) -> Decimal:
        """The pace a renewal's reset carries: how far traffic ran ahead of time over the
        time the counter covered before it, weighted with the renewal's own term at 1, so
        an early run fades as terms are paid and a renewal bought only to zero the counter
        unlocks just its own money."""
        segments = await self._credit_segment_repository.list_for(subscription_id)
        own = [segment for segment in segments if segment.payment_key == payment_key]
        earlier = [segment for segment in segments if segment.payment_key != payment_key]
        if not own or own[0].ends_at is None:
            return Decimal(1)
        own_end = own[0].ends_at
        previous_end = max(own_end - timedelta(seconds=own[0].seconds), now)
        span = int((own_end - now).total_seconds())
        if span <= 0:
            return Decimal(1)
        before = int((previous_end - now).total_seconds())
        pace = traffic_pace(earlier, now=now, expire_at=previous_end, traffic_left=traffic_left)
        return (Decimal(before) * pace + Decimal(span - before)) / Decimal(span)

    async def record_traffic_reset(
        self, subscription_id: int, payment_key: str, *, at: datetime, pace: Decimal
    ) -> None:
        await self._credit_segment_repository.mark_traffic_reset(
            subscription_id, payment_key, at=at, pace=pace
        )

    async def carry_credit(self, from_subscription_id: int, to_subscription_id: int) -> None:
        """A plan change that keeps the term keeps what was paid for it."""
        await self._credit_segment_repository.copy_live(from_subscription_id, to_subscription_id)

    async def renew(
        self,
        subscription_id: int,
        new_expire_at: datetime,
        *,
        paid: Decimal,
        currency: Currency,
        previous_expire_at: datetime,
        starts_at: datetime,
        payment_key: Optional[str],
    ) -> SubscriptionDto:
        # previous_expire_at is read before the panel call: the panel's webhook may
        # have synced the new expiry into the row by now
        dto = await self._subscription_repository.get_by_id_for_update(subscription_id)
        if dto.grace_until is not None:
            # every renewal ends grace; the sync heeds the panel again from here
            await self._subscription_repository.set_grace_until(subscription_id, None)
        renewed = await self._subscription_repository.apply_state(
            _dto_to_state(dto).reactivate_on_renew(new_expire_at)
        )
        if not is_unlimited_expiry(new_expire_at):
            # a lapsed term restarts today; its old segments lie in the past and credit nothing
            term = await self._add_segment(
                subscription_id,
                starts_at=starts_at,
                expire_at=new_expire_at,
                paid=paid,
                currency=currency,
                payment_key=payment_key,
            )
            # an unlimited term an admin made finite was used up; its refund brings it back
            await self._credit_segment_repository.supersede_live(
                subscription_id, term.id, unlimited_only=True
            )
            return renewed
        # what an unlimited term replaced, kept for its refund to put back
        unlimited = await self._credit_segment_repository.create(
            CreateCreditSegmentDto(
                subscription_id=subscription_id,
                kind=_kind_for(paid),
                paid=paid,
                currency=currency,
                seconds=0,
                ends_at=None,
                payment_key=payment_key,
                replaced_expire_at=previous_expire_at,
            )
        )
        await self._credit_segment_repository.supersede_live(subscription_id, unlimited.id)
        return renewed

    async def refunded_unlimited_expiry(
        self, subscription_id: int, payment_key: Optional[str], now: datetime
    ) -> datetime:
        """Where a refunded unlimited renewal goes back to; without a record of the
        term it replaced, access ends now."""
        segment = await self._find_unlimited(subscription_id, payment_key)
        if segment is None or segment.replaced_expire_at is None:
            return now
        return segment.replaced_expire_at

    async def shorten_after_refund(
        self,
        subscription_id: int,
        *,
        days: int,
        payment_key: Optional[str],
        paid: Decimal,
        unlimited: bool,
        now: datetime,
    ) -> SubscriptionDto:
        """Takes a refunded renewal back: its days off the end and its segment out.
        `paid`, in the snapshot's currency, comes off a pre-segment term that holds it."""
        dto = await self._subscription_repository.get_by_id_for_update(subscription_id)
        state = _dto_to_state(dto)
        if unlimited:
            segment = await self._find_unlimited(subscription_id, payment_key)
            restore_to = await self.refunded_unlimited_expiry(subscription_id, payment_key, now)
            shortened = await self._subscription_repository.apply_state(
                state.end_unlimited(restore_to, now)
            )
            if segment is not None:
                await self._credit_segment_repository.release_superseded(segment.id)
                await self._credit_segment_repository.delete(segment.id)
            return shortened
        segment = await self._find_segment(subscription_id, payment_key)
        segments = await self._credit_segment_repository.list_for(subscription_id)
        if is_unlimited_expiry(dto.expire_at):
            # a later unlimited renewal replaced the refunded term: what it gives back
            # on its own refund loses the refunded days
            for live in segments:
                if live.is_live and live.ends_at is None and live.replaced_expire_at is not None:
                    await self._credit_segment_repository.set_replaced_expire_at(
                        live.id, live.replaced_expire_at - timedelta(days=days)
                    )
            replaced = [s for s in segments if not s.is_live and s.ends_at is not None]
            await self._take_off(subscription_id, segment, replaced, days=days, paid=paid)
            return dto
        shortened = await self._subscription_repository.apply_state(
            state.shorten_after_refund(days, now)
        )
        live_terms = [s for s in segments if s.is_live and s.ends_at is not None]
        await self._take_off(subscription_id, segment, live_terms, days=days, paid=paid)
        return shortened

    async def _take_off(
        self,
        subscription_id: int,
        segment: Optional[CreditSegmentDto],
        candidates: list[CreditSegmentDto],
        *,
        days: int,
        paid: Decimal,
    ) -> None:
        if segment is not None and segment.ends_at is not None:
            await self._credit_segment_repository.delete(segment.id)
            # the terms after it move up as the expiry does
            await self._credit_segment_repository.move_back(
                subscription_id, segment.ends_at, days * TIME_1D
            )
            return
        # renewed before segments existed: its money and days sit in the latest one
        if candidates:
            latest = max(candidates, key=lambda s: s.ends_at.timestamp() if s.ends_at else 0.0)
            await self._credit_segment_repository.shrink(
                latest.id, seconds=days * TIME_1D, paid=paid
            )

    async def _find_unlimited(
        self, subscription_id: int, payment_key: Optional[str]
    ) -> Optional[CreditSegmentDto]:
        """The refunded unlimited term: by its order's key, else the live one, for an
        order whose key was pruned."""
        segment = await self._find_segment(subscription_id, payment_key)
        if segment is not None:
            return segment
        live = [
            segment
            for segment in await self._credit_segment_repository.list_for(subscription_id)
            if segment.is_live and segment.ends_at is None
        ]
        return live[-1] if live else None

    async def _find_segment(
        self, subscription_id: int, payment_key: Optional[str]
    ) -> Optional[CreditSegmentDto]:
        if payment_key is None:
            return None
        return await self._credit_segment_repository.find_by_payment_key(
            subscription_id, payment_key
        )

    async def restore_after_refund(self, subscription_id: int, now: datetime) -> SubscriptionDto:
        dto = await self._subscription_repository.get_by_id_for_update(subscription_id)
        new_state = _dto_to_state(dto).restore_after_refund(now)
        return await self._subscription_repository.apply_state(new_state)
