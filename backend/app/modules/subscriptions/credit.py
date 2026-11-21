from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from remnapy.enums import TrafficLimitStrategy

from app.core.constants import GB_TO_BYTES, ZERO_AMOUNT
from app.core.enums import Currency
from app.modules.subscriptions.dto import CreditSegmentDto, SubscriptionDto

# a segment's money in the currency the credit is counted in
Convert = Callable[[Decimal, Currency], Decimal]


def remaining_term_credit(
    segments: Iterable[CreditSegmentDto],
    *,
    now: datetime,
    expire_at: datetime,
    convert: Convert,
) -> Decimal:
    """What's left of a fixed term, from the end back, each stretch at the price paid
    for it. Days past the last segment (an admin's or the panel's extension) are free;
    a term cut short loses its tail, and a renewal that starts inside an earlier
    segment covers those days once."""
    total = ZERO_AMOUNT
    cursor = expire_at
    for ends_at, segment in sorted(_terms(segments), key=lambda term: term[0], reverse=True):
        starts_at = ends_at - timedelta(seconds=segment.seconds)
        left = int((min(ends_at, cursor) - max(starts_at, now)).total_seconds())
        cursor = min(cursor, starts_at)
        if left > 0:
            total += convert(
                segment.paid * Decimal(left) / Decimal(segment.seconds), segment.currency
            )
    return total


def traffic_pace(
    segments: Iterable[CreditSegmentDto],
    *,
    now: datetime,
    expire_at: datetime,
    traffic_left: Decimal,
) -> Decimal:
    """The share of the credit a never-resetting traffic limit leaves: 1 while traffic
    goes no faster than time, less for what was used ahead. One counter covers all the
    time from the last order that zeroed it to the expiry, terms paid ahead included;
    how far it ran ahead before that reset carries over it."""
    terms = list(_terms(segments))
    resets = [segment for _, segment in terms if segment.traffic_reset_at is not None]
    if resets:
        last = max(resets, key=lambda segment: segment.traffic_reset_at or now)
        reset_at, carried = last.traffic_reset_at or now, last.traffic_pace
    else:
        # a term from before segments: its counter runs from its start
        running = [
            ends_at - timedelta(seconds=segment.seconds)
            for ends_at, segment in terms
            if ends_at - timedelta(seconds=segment.seconds) <= now < ends_at
        ]
        if not running:
            return Decimal(1)
        reset_at, carried = min(running), Decimal(1)
    if expire_at <= now:
        return carried
    time_left = Decimal(int((expire_at - now).total_seconds())) / Decimal(
        max(int((expire_at - reset_at).total_seconds()), 1)
    )
    return carried * min(Decimal(1), traffic_left / time_left)


def traffic_left_share(subscription: SubscriptionDto, used_bytes: int) -> Optional[Decimal]:
    """The share of a never-resetting limit not used yet; a resetting counter covers its
    period, not the term, and an unlimited one caps nothing."""
    limit = subscription.effective_traffic_limit
    if limit <= 0 or subscription.traffic_limit_strategy is not TrafficLimitStrategy.NO_RESET:
        return None
    limit_bytes = limit * GB_TO_BYTES
    return Decimal(max(0, limit_bytes - used_bytes)) / Decimal(limit_bytes)


def _terms(segments: Iterable[CreditSegmentDto]) -> Iterable[tuple[datetime, CreditSegmentDto]]:
    for segment in segments:
        if segment.is_live and segment.ends_at is not None and segment.seconds > 0:
            yield segment.ends_at, segment


def unlimited_credit(
    segments: Iterable[CreditSegmentDto], *, now: datetime, convert: Convert
) -> Decimal:
    """What a move to another unlimited plan takes off the price: the unlimited terms
    paid for, and what's left of fixed ones an admin or the panel turned unlimited."""
    segments = list(segments)
    paid_unlimited = sum(
        (
            convert(segment.paid, segment.currency)
            for segment in segments
            if segment.is_live and segment.ends_at is None
        ),
        ZERO_AMOUNT,
    )
    unused = remaining_term_credit(
        segments,
        now=now,
        expire_at=max((s.ends_at or now for s in segments), default=now),
        convert=convert,
    )
    return paid_unlimited + unused
