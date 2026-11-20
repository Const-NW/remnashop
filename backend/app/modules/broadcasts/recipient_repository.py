import builtins
from datetime import datetime, timedelta
from typing import Any, Final, Optional
from typing import cast as typing_cast

from sqlalchemy import (
    ColumnElement,
    CursorResult,
    Row,
    Select,
    and_,
    case,
    delete,
    func,
    literal,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.db.filters import in_ids
from app.core.db.models.active_locale import ActiveLocale
from app.core.db.models.broadcast import Broadcast, BroadcastRecipient
from app.core.db.models.plan import Plan
from app.core.db.models.plan_translation import PlanTranslation
from app.core.db.models.subscription import Subscription
from app.core.db.models.user import User
from app.core.enums import (
    BroadcastCategory,
    BroadcastChannel,
    BroadcastDelivery,
    BroadcastRecipientStatus,
    SubscriptionStatus,
)
from app.core.utils.time import datetime_now
from app.modules.broadcasts.audience import (
    audience_predicate,
    channel_condition,
)
from app.modules.broadcasts.dto import (
    AudiencePreviewDto,
    BroadcastAudienceDto,
    BroadcastRecipientDto,
    ListBroadcastRecipientsDto,
    PendingEmailRecipientDto,
    PendingRecipientDto,
    RecipientContextDto,
    SentRecipientDto,
)

NO_TELEGRAM_ERROR: Final[str] = "no telegram"
NO_EMAIL_ERROR: Final[str] = "no email"
UNDELIVERABLE_ERROR: Final[str] = "undeliverable"
OPTED_OUT_ERROR: Final[str] = "opted out"
BLOCKED_ERROR: Final[str] = "blocked"
BOT_BLOCKED_ERROR: Final[str] = "bot blocked"
RECALL_FAILED_ERROR: Final[str] = "recall failed"


def _channel_conditions(
    delivery: BroadcastDelivery, email_fallback: bool
) -> dict[BroadcastChannel, ColumnElement[bool]]:
    conditions = {
        channel: channel_condition(channel, delivery, email_fallback)
        for channel in BroadcastChannel
    }
    return {channel: c for channel, c in conditions.items() if c is not None}


class BroadcastRecipientRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def count_audience(
        self,
        audience: BroadcastAudienceDto,
        category: BroadcastCategory,
        delivery: BroadcastDelivery,
        email_fallback: bool,
        now: datetime,
    ) -> AudiencePreviewDto:
        predicate = audience_predicate(audience, category, now)
        channels = _channel_conditions(delivery, email_fallback)
        by_channel: dict[BroadcastChannel, int] = {}
        for channel, condition in channels.items():
            count = await self.session.scalar(select(func.count()).where(predicate, condition))
            by_channel[channel] = count or 0
        rows = await self.session.execute(
            select(User.language, func.count())
            .where(predicate, or_(*channels.values()))
            .group_by(User.language)
        )
        by_locale = dict(rows.tuples().all())
        return AudiencePreviewDto(
            total=sum(by_locale.values()), by_locale=by_locale, by_channel=by_channel
        )

    async def materialize(
        self,
        broadcast_id: int,
        audience: BroadcastAudienceDto,
        category: BroadcastCategory,
        delivery: BroadcastDelivery,
        email_fallback: bool,
        now: datetime,
    ) -> int:
        predicate = audience_predicate(audience, category, now)
        for channel, condition in _channel_conditions(delivery, email_fallback).items():
            source = (
                select(
                    literal(broadcast_id),
                    User.id,
                    literal(BroadcastRecipientStatus.PENDING, type_=BroadcastRecipient.status.type),
                    literal(channel, type_=BroadcastRecipient.channel.type),
                )
                .where(predicate, condition)
                .order_by(User.id)
            )
            await self.session.execute(
                pg_insert(BroadcastRecipient)
                .from_select(["broadcast_id", "user_id", "status", "channel"], source)
                .on_conflict_do_nothing(constraint="uq_broadcast_recipients_user_channel")
            )
        total = await self.session.scalar(
            select(func.count()).where(BroadcastRecipient.broadcast_id == broadcast_id)
        )
        return int(total or 0)

    async def _drop_unreachable(
        self,
        recipient_ids: builtins.list[int],
        marketing: bool,
        unreachable: builtins.list[tuple[ColumnElement[bool], str]],
        refused: builtins.list[tuple[ColumnElement[bool], str]],
    ) -> None:
        # audience was built at materialization; a block or opt-out since then
        # still wins. Only the batch about to go out is checked, so the cost per
        # batch stays flat however large the broadcast is.
        cancel = [(User.is_blocked.is_(True), BLOCKED_ERROR)]
        if marketing:
            cancel.append((User.marketing_opt_out.is_(True), OPTED_OUT_ERROR))
        for status, conditions in (
            (BroadcastRecipientStatus.CANCELED, cancel),
            (BroadcastRecipientStatus.FAILED, unreachable),
            (BroadcastRecipientStatus.BLOCKED, refused),
        ):
            for condition, error in conditions:
                await self.session.execute(
                    update(BroadcastRecipient)
                    .where(
                        in_ids(BroadcastRecipient.id, recipient_ids),
                        BroadcastRecipient.status == BroadcastRecipientStatus.PENDING,
                        BroadcastRecipient.user_id == User.id,
                        condition,
                    )
                    .values(status=status, error=error)
                    .execution_options(synchronize_session=False)
                )

    def _claimable(
        self, broadcast_id: int, channel: BroadcastChannel, limit: int, now: datetime
    ) -> Select[tuple[int]]:
        return (
            select(BroadcastRecipient.id)
            .where(
                BroadcastRecipient.broadcast_id == broadcast_id,
                BroadcastRecipient.channel == channel,
                BroadcastRecipient.status == BroadcastRecipientStatus.PENDING,
                or_(
                    BroadcastRecipient.claimed_until.is_(None),
                    BroadcastRecipient.claimed_until < now,
                ),
            )
            .order_by(BroadcastRecipient.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )

    async def _claim(
        self,
        broadcast_id: int,
        channel: BroadcastChannel,
        limit: int,
        lease: timedelta,
        marketing: bool,
        unreachable: builtins.list[tuple[ColumnElement[bool], str]],
        refused: builtins.list[tuple[ColumnElement[bool], str]],
        contact: InstrumentedAttribute[Any],
    ) -> builtins.list[Row[Any]]:
        now = datetime_now()
        # a batch whose every recipient turned unreachable claims nothing: take
        # the next one, so the chain doesn't stop while rows are still pending
        while True:
            candidates = list(
                await self.session.scalars(self._claimable(broadcast_id, channel, limit, now))
            )
            if not candidates:
                return []
            await self._drop_unreachable(candidates, marketing, unreachable, refused)
            rows = (
                await self.session.execute(
                    update(BroadcastRecipient)
                    .where(
                        in_ids(BroadcastRecipient.id, candidates),
                        BroadcastRecipient.status == BroadcastRecipientStatus.PENDING,
                        User.id == BroadcastRecipient.user_id,
                    )
                    .values(claimed_until=now + lease)
                    .returning(
                        BroadcastRecipient.id,
                        BroadcastRecipient.user_id,
                        contact,
                        BroadcastRecipient.attempts,
                    )
                    .execution_options(synchronize_session=False)
                )
            ).all()
            if rows:
                return sorted(rows, key=lambda r: r[0])

    async def claim_pending(
        self, broadcast_id: int, limit: int, lease: timedelta, *, marketing: bool
    ) -> builtins.list[PendingRecipientDto]:
        rows = await self._claim(
            broadcast_id,
            BroadcastChannel.TELEGRAM,
            limit,
            lease,
            marketing,
            # a user who lost their telegram_id since materialization can never be reached
            [(User.telegram_id.is_(None), NO_TELEGRAM_ERROR)],
            [(User.is_bot_blocked.is_(True), BOT_BLOCKED_ERROR)],
            User.telegram_id,
        )
        return [
            PendingRecipientDto(id=r[0], user_id=r[1], telegram_id=r[2], attempts=r[3])
            for r in rows
        ]

    async def claim_pending_email(
        self, broadcast_id: int, limit: int, lease: timedelta, *, marketing: bool
    ) -> builtins.list[PendingEmailRecipientDto]:
        rows = await self._claim(
            broadcast_id,
            BroadcastChannel.EMAIL,
            limit,
            lease,
            marketing,
            [
                (User.email.is_(None), NO_EMAIL_ERROR),
                (User.email_undeliverable_at.is_not(None), UNDELIVERABLE_ERROR),
            ],
            [],
            User.email,
        )
        return [
            PendingEmailRecipientDto(id=r[0], user_id=r[1], email=r[2], attempts=r[3]) for r in rows
        ]

    async def release_claims(self, ids: builtins.list[int]) -> None:
        await self.session.execute(
            update(BroadcastRecipient)
            .where(in_ids(BroadcastRecipient.id, ids))
            .values(claimed_until=None)
        )

    async def mark_recipient(
        self,
        recipient_id: int,
        status: BroadcastRecipientStatus,
        *,
        message_id: Optional[int] = None,
        error: Optional[str] = None,
    ) -> None:
        values: dict[str, Any] = {"status": status}
        if status is BroadcastRecipientStatus.SENT:
            values["error"] = None
            values["sent_at"] = datetime_now()
            values["telegram_message_id"] = message_id
        if error:
            values["error"] = error[:255]
        await self.session.execute(
            update(BroadcastRecipient).where(BroadcastRecipient.id == recipient_id).values(**values)
        )

    async def mark_recall_failed(self, recipient_id: int, error: str) -> None:
        await self.session.execute(
            update(BroadcastRecipient)
            .where(BroadcastRecipient.id == recipient_id)
            .values(error=f"{RECALL_FAILED_ERROR}: {error}"[:255])
        )

    async def count_recallable(self, broadcast_id: int) -> int:
        # recall failures count too: a repeat clears them first
        count = await self.session.scalar(
            select(func.count())
            .select_from(BroadcastRecipient)
            .join(User, User.id == BroadcastRecipient.user_id)
            .where(
                BroadcastRecipient.broadcast_id == broadcast_id,
                BroadcastRecipient.status == BroadcastRecipientStatus.SENT,
                BroadcastRecipient.telegram_message_id.is_not(None),
                User.telegram_id.is_not(None),
            )
        )
        return count or 0

    async def clear_recall_failures(self, broadcast_id: int) -> None:
        await self.session.execute(
            update(BroadcastRecipient)
            .where(
                BroadcastRecipient.broadcast_id == broadcast_id,
                BroadcastRecipient.status == BroadcastRecipientStatus.SENT,
                BroadcastRecipient.error.startswith(RECALL_FAILED_ERROR),
            )
            .values(error=None)
        )

    async def bump_attempt(self, recipient_id: int, max_attempts: int, error: str) -> None:
        status_type = BroadcastRecipient.status.type
        await self.session.execute(
            update(BroadcastRecipient)
            # an admin cancel in the meantime wins
            .where(
                BroadcastRecipient.id == recipient_id,
                BroadcastRecipient.status == BroadcastRecipientStatus.PENDING,
            )
            .values(
                attempts=BroadcastRecipient.attempts + 1,
                claimed_until=None,
                error=error[:255],
                status=case(
                    (
                        BroadcastRecipient.attempts + 1 >= max_attempts,
                        literal(BroadcastRecipientStatus.FAILED, type_=status_type),
                    ),
                    else_=literal(BroadcastRecipientStatus.PENDING, type_=status_type),
                ),
            )
        )

    async def cancel_pending(self, broadcast_id: int) -> None:
        await self.session.execute(
            update(BroadcastRecipient)
            .where(
                BroadcastRecipient.broadcast_id == broadcast_id,
                BroadcastRecipient.status == BroadcastRecipientStatus.PENDING,
            )
            .values(status=BroadcastRecipientStatus.CANCELED)
        )

    async def fail_pending(self, broadcast_id: int, error: str) -> None:
        await self.session.execute(
            update(BroadcastRecipient)
            .where(
                BroadcastRecipient.broadcast_id == broadcast_id,
                BroadcastRecipient.status == BroadcastRecipientStatus.PENDING,
            )
            .values(status=BroadcastRecipientStatus.FAILED, error=error, claimed_until=None)
        )

    async def reset_failed(self, broadcast_id: int) -> int:
        result = await self.session.execute(
            update(BroadcastRecipient)
            .where(
                BroadcastRecipient.broadcast_id == broadcast_id,
                BroadcastRecipient.status == BroadcastRecipientStatus.FAILED,
            )
            .values(
                status=BroadcastRecipientStatus.PENDING, attempts=0, error=None, claimed_until=None
            )
        )
        return typing_cast(CursorResult[Any], result).rowcount

    async def list_recallable(
        self, broadcast_id: int, limit: int
    ) -> builtins.list[SentRecipientDto]:
        rows = await self.session.execute(
            select(BroadcastRecipient.id, User.telegram_id, BroadcastRecipient.telegram_message_id)
            .join(User, User.id == BroadcastRecipient.user_id)
            .where(
                BroadcastRecipient.broadcast_id == broadcast_id,
                BroadcastRecipient.status == BroadcastRecipientStatus.SENT,
                BroadcastRecipient.telegram_message_id.is_not(None),
                # a failed delete stays SENT but is skipped, or the chain would loop over it
                BroadcastRecipient.error.is_(None),
                User.telegram_id.is_not(None),
            )
            .order_by(BroadcastRecipient.id)
            .limit(limit)
        )
        return [
            SentRecipientDto(
                id=r.id, telegram_id=r.telegram_id, telegram_message_id=r.telegram_message_id
            )
            for r in rows
        ]

    async def has_pending(self, broadcast_id: int) -> bool:
        return bool(
            await self.session.scalar(
                select(
                    select(BroadcastRecipient.id)
                    .where(
                        BroadcastRecipient.broadcast_id == broadcast_id,
                        BroadcastRecipient.status == BroadcastRecipientStatus.PENDING,
                    )
                    .exists()
                )
            )
        )

    async def list_recipients(
        self, data: ListBroadcastRecipientsDto
    ) -> tuple[builtins.list[BroadcastRecipientDto], int]:
        conditions: builtins.list[ColumnElement[bool]] = [
            BroadcastRecipient.broadcast_id == data.broadcast_id
        ]
        if data.status is not None:
            conditions.append(BroadcastRecipient.status == data.status)
        if data.channel is not None:
            conditions.append(BroadcastRecipient.channel == data.channel)
        total = await self.session.scalar(
            select(func.count()).select_from(BroadcastRecipient).where(*conditions)
        )
        rows = await self.session.execute(
            select(BroadcastRecipient, User)
            .join(User, User.id == BroadcastRecipient.user_id)
            .where(*conditions)
            .order_by(BroadcastRecipient.id)
            .offset((data.page - 1) * data.page_size)
            .limit(data.page_size)
        )
        return [
            BroadcastRecipientDto(
                id=row.id,
                broadcast_id=row.broadcast_id,
                user_id=row.user_id,
                channel=row.channel,
                status=row.status,
                attempts=row.attempts,
                telegram_message_id=row.telegram_message_id,
                error=row.error,
                sent_at=row.sent_at,
                user_name=user.name,
                username=user.username,
                telegram_id=user.telegram_id,
                email=user.email,
                language=user.language,
            )
            for row, user in rows.tuples()
        ], total or 0

    async def recipient_contexts(
        self, user_ids: builtins.list[int]
    ) -> dict[int, RecipientContextDto]:
        if not user_ids:
            return {}
        live = (
            select(
                Subscription.user_id,
                Subscription.plan_snapshot["plan_id"].as_integer().label("plan_id"),
                Subscription.plan_snapshot["name"].as_string().label("plan_name"),
                Subscription.plan_snapshot["names"].label("plan_names"),
                Subscription.expire_at,
            )
            .where(
                in_ids(Subscription.user_id, user_ids),
                Subscription.status.in_((SubscriptionStatus.ACTIVE, SubscriptionStatus.LIMITED)),
            )
            .order_by(Subscription.user_id, Subscription.expire_at.desc())
            .distinct(Subscription.user_id)
            .subquery()
        )
        rows = await self.session.execute(
            select(
                User,
                # the plan as it reads now; the snapshot's once the plan is deleted
                func.coalesce(PlanTranslation.name, Plan.name),
                live.c.plan_name,
                live.c.plan_names,
                live.c.expire_at,
            )
            .outerjoin(live, live.c.user_id == User.id)
            .outerjoin(Plan, Plan.id == live.c.plan_id)
            .outerjoin(
                PlanTranslation,
                and_(
                    PlanTranslation.plan_id == Plan.id,
                    PlanTranslation.locale == User.language,
                    PlanTranslation.locale.in_(select(ActiveLocale.locale)),
                ),
            )
            .where(in_ids(User.id, user_ids))
        )
        return {
            user.id: RecipientContextDto(
                user_id=user.id,
                name=user.name,
                username=user.username,
                language=user.language,
                bonus_days=user.bonus_days,
                points=int(user.points),
                plan_name=current_name or (plan_names or {}).get(user.language.value, plan_name),
                expire_at=expire_at,
                referral_code=user.referral_code,
            )
            for user, current_name, plan_name, plan_names, expire_at in rows.tuples()
        }

    async def is_recipient(self, broadcast_id: int, user_id: int) -> bool:
        found = await self.session.scalar(
            select(BroadcastRecipient.id).where(
                BroadcastRecipient.broadcast_id == broadcast_id,
                BroadcastRecipient.user_id == user_id,
            )
        )
        return found is not None

    async def prune_recipients(self, completed_before: datetime) -> int:
        result = await self.session.execute(
            delete(BroadcastRecipient).where(
                BroadcastRecipient.broadcast_id.in_(
                    select(Broadcast.id).where(Broadcast.completed_at < completed_before)
                )
            )
        )
        return typing_cast(CursorResult[Any], result).rowcount
