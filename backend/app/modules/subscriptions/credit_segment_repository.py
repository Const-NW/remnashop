from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, true, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import CreditSegment
from app.core.enums import CreditSegmentKind
from app.modules.subscriptions.dto import CreateCreditSegmentDto, CreditSegmentDto


class CreditSegmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_dto(self, row: CreditSegment) -> CreditSegmentDto:
        return CreditSegmentDto.model_validate(row, from_attributes=True)

    async def list_for(self, subscription_id: int) -> list[CreditSegmentDto]:
        rows = await self.session.scalars(
            select(CreditSegment)
            .where(CreditSegment.subscription_id == subscription_id)
            .order_by(CreditSegment.id)
        )
        return [self._to_dto(row) for row in rows]

    async def find_by_payment_key(
        self, subscription_id: int, payment_key: str
    ) -> Optional[CreditSegmentDto]:
        row = await self.session.scalar(
            select(CreditSegment).where(
                CreditSegment.subscription_id == subscription_id,
                CreditSegment.payment_key == payment_key,
            )
        )
        return self._to_dto(row) if row is not None else None

    async def create(self, data: CreateCreditSegmentDto) -> CreditSegmentDto:
        row = CreditSegment(
            subscription_id=data.subscription_id,
            kind=data.kind,
            payment_key=data.payment_key,
            traffic_reset_at=data.traffic_reset_at,
            traffic_pace=data.traffic_pace,
            paid=data.paid,
            currency=data.currency,
            seconds=data.seconds,
            ends_at=data.ends_at,
            replaced_expire_at=data.replaced_expire_at,
        )
        self.session.add(row)
        await self.session.flush()
        return self._to_dto(row)

    async def copy_live(self, source_subscription_id: int, target_subscription_id: int) -> None:
        """The live segments of one subscription onto another, as carried over."""
        for segment in await self.list_for(source_subscription_id):
            if segment.is_live:
                self.session.add(
                    CreditSegment(
                        subscription_id=target_subscription_id,
                        kind=CreditSegmentKind.CARRIED,
                        # the counter still covers what it covered
                        traffic_reset_at=segment.traffic_reset_at,
                        traffic_pace=segment.traffic_pace,
                        paid=segment.paid,
                        currency=segment.currency,
                        seconds=segment.seconds,
                        ends_at=segment.ends_at,
                        replaced_expire_at=segment.replaced_expire_at,
                    )
                )
        await self.session.flush()

    async def delete(self, segment_id: int) -> None:
        row = await self.session.get(CreditSegment, segment_id)
        if row is not None:
            await self.session.delete(row)
            await self.session.flush()

    async def supersede_live(
        self, subscription_id: int, by_id: int, *, unlimited_only: bool = False
    ) -> None:
        await self.session.execute(
            update(CreditSegment)
            .where(
                CreditSegment.subscription_id == subscription_id,
                CreditSegment.superseded_by_id.is_(None),
                CreditSegment.id != by_id,
                CreditSegment.ends_at.is_(None) if unlimited_only else true(),
            )
            .values(superseded_by_id=by_id)
            .execution_options(synchronize_session=False)
        )

    async def release_superseded(self, by_id: int) -> None:
        await self.session.execute(
            update(CreditSegment)
            .where(CreditSegment.superseded_by_id == by_id)
            .values(superseded_by_id=None)
            .execution_options(synchronize_session=False)
        )

    async def move_back(self, subscription_id: int, after: datetime, seconds: int) -> None:
        """Every fixed-term segment ending after `after` moves `seconds` earlier: a
        refunded term before them no longer pushes them out."""
        await self.session.execute(
            update(CreditSegment)
            .where(
                CreditSegment.subscription_id == subscription_id,
                CreditSegment.ends_at > after,
            )
            .values(ends_at=CreditSegment.ends_at - timedelta(seconds=seconds))
            .execution_options(synchronize_session=False)
        )

    async def shrink(self, segment_id: int, *, seconds: int, paid: Decimal) -> None:
        """Takes a term without its own segment off the end of one that holds it."""
        row = await self.session.get(CreditSegment, segment_id, populate_existing=True)
        if row is None:
            return
        row.seconds = max(row.seconds - seconds, 0)
        row.paid = max(row.paid - paid, Decimal(0))
        if row.ends_at is not None:
            row.ends_at -= timedelta(seconds=seconds)
        await self.session.flush()

    async def mark_traffic_reset(
        self, subscription_id: int, payment_key: str, *, at: datetime, pace: Decimal
    ) -> None:
        await self.session.execute(
            update(CreditSegment)
            .where(
                CreditSegment.subscription_id == subscription_id,
                CreditSegment.payment_key == payment_key,
            )
            .values(traffic_reset_at=at, traffic_pace=pace)
            .execution_options(synchronize_session=False)
        )

    async def set_replaced_expire_at(
        self, segment_id: int, replaced_expire_at: Optional[datetime]
    ) -> None:
        row = await self.session.get(CreditSegment, segment_id, populate_existing=True)
        if row is not None:
            row.replaced_expire_at = replaced_expire_at
            await self.session.flush()
