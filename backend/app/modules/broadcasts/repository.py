import builtins
from datetime import datetime, timedelta
from typing import Any, Final, Optional

from sqlalchemy import (
    ColumnElement,
    Text,
    cast,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONPATH
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.filters import in_ids
from app.core.db.models.ad_link import AdLink
from app.core.db.models.broadcast import Broadcast, BroadcastEmailImage, BroadcastRecipient
from app.core.db.models.plan import Plan
from app.core.db.models.user import User
from app.core.enums import (
    BroadcastRecipientStatus,
    BroadcastStatus,
    Locale,
)
from app.core.error_codes import ErrorCode
from app.core.exceptions import NotFoundError
from app.core.utils.time import datetime_now, range_end_utc, range_start_utc
from app.modules.broadcasts.audience import (
    deliverable_email,
)
from app.modules.broadcasts.dto import (
    BroadcastAudienceDto,
    BroadcastDto,
    BroadcastEmailContentDto,
    BroadcastMediaDto,
    BroadcastSummaryDto,
    EmailImageDto,
    ListBroadcastsDto,
    SaveBroadcastDto,
)

SUMMARY_WINDOW: Final[timedelta] = timedelta(days=30)
# only the message texts, so a search for "text" or "_" doesn't hit the JSON keys
_TEXTS_PATH: Final[str] = "$.*.text"
# what a duplicate takes over; media_file_id is left out, a new draft uploads its own copy
_DUPLICATED_COLUMNS: Final[tuple[str, ...]] = (
    "title",
    "category",
    "audience",
    "default_locale",
    "contents",
    "buttons",
    "promocode_id",
    "post_to_channel",
    "delivery",
    "email_fallback",
    "email_contents",
    "media_type",
    "media_content_type",
    "media_extension",
    "media_version",
    "media_size_bytes",
)
# pg_advisory_xact_lock key: one broadcast starts at a time
_START_LOCK_KEY: Final[int] = 8221941


_PROCESSED: Final[tuple[BroadcastRecipientStatus, ...]] = (
    BroadcastRecipientStatus.SENT,
    BroadcastRecipientStatus.FAILED,
    BroadcastRecipientStatus.BLOCKED,
    BroadcastRecipientStatus.RECALLED,
)


class BroadcastRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_dto(self, row: Broadcast, created_by_name: Optional[str] = None) -> BroadcastDto:
        return BroadcastDto.model_validate(row, from_attributes=True).model_copy(
            update={"created_by_name": created_by_name}
        )

    async def _creator_names(self, user_ids: set[int]) -> dict[int, str]:
        if not user_ids:
            return {}
        rows = await self.session.execute(
            select(User.id, func.coalesce(User.login, User.name)).where(in_ids(User.id, user_ids))
        )
        return dict(rows.tuples().all())

    async def _with_creator(self, row: Broadcast) -> BroadcastDto:
        names = await self._creator_names({row.created_by} if row.created_by is not None else set())
        return self._to_dto(row, names.get(row.created_by) if row.created_by is not None else None)

    @staticmethod
    def _values(data: SaveBroadcastDto) -> dict[str, Any]:
        return {
            "title": (data.title or "").strip() or None,
            "category": data.category,
            "audience": data.audience.model_dump(mode="json"),
            "default_locale": data.default_locale,
            "contents": {
                locale.value: content.model_dump(mode="json")
                for locale, content in data.contents.items()
            },
            "buttons": [button.model_dump(mode="json") for button in data.buttons],
            "promocode_id": data.promocode_id,
            "post_to_channel": data.post_to_channel,
            "delivery": data.delivery,
            "email_fallback": data.email_fallback,
            "email_contents": {
                locale.value: content.model_dump(mode="json")
                for locale, content in data.email_contents.items()
            },
        }

    async def _get_row(self, broadcast_id: int) -> Broadcast:
        row = await self.session.get(Broadcast, broadcast_id)
        if row is None:
            raise NotFoundError(
                f"Broadcast '{broadcast_id}' not found", code=ErrorCode.BROADCAST_NOT_FOUND
            )
        return row

    async def create(self, data: SaveBroadcastDto, created_by: Optional[int]) -> BroadcastDto:
        row = Broadcast(status=BroadcastStatus.DRAFT, created_by=created_by, **self._values(data))
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return await self._with_creator(row)

    async def duplicate(self, source_id: int, created_by: Optional[int]) -> BroadcastDto:
        source = await self._get_row(source_id)
        row = Broadcast(
            status=BroadcastStatus.DRAFT,
            created_by=created_by,
            **{column: getattr(source, column) for column in _DUPLICATED_COLUMNS},
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return await self._with_creator(row)

    async def set_email_contents(
        self, broadcast_id: int, email_contents: dict[Locale, BroadcastEmailContentDto]
    ) -> None:
        await self._write(
            broadcast_id,
            {
                "email_contents": {
                    locale.value: content.model_dump(mode="json")
                    for locale, content in email_contents.items()
                }
            },
        )

    async def find_by_id(self, broadcast_id: int) -> Optional[BroadcastDto]:
        # fresh read: the worker re-checks status written by another process
        row = await self.session.get(Broadcast, broadcast_id, populate_existing=True)
        return await self._with_creator(row) if row is not None else None

    async def get_by_id(self, broadcast_id: int) -> BroadcastDto:
        return await self._with_creator(await self._get_row(broadcast_id))

    async def has_missing_references(self, audience: BroadcastAudienceDto) -> bool:
        for model, ids in (
            (Plan, audience.plan_ids),
            (AdLink, audience.ad_link_ids),
            (User, audience.user_ids),
        ):
            unique = set(ids)
            if not unique:
                continue
            found = await self.session.scalar(
                select(func.count()).select_from(model).where(in_ids(model.id, unique))
            )
            if (found or 0) != len(unique):
                return True
        return False

    async def get_by_id_for_update(self, broadcast_id: int) -> BroadcastDto:
        row = await self.session.scalar(
            select(Broadcast)
            .where(Broadcast.id == broadcast_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFoundError(
                f"Broadcast '{broadcast_id}' not found", code=ErrorCode.BROADCAST_NOT_FOUND
            )
        return self._to_dto(row)

    async def try_lock(self, broadcast_id: int) -> Optional[BroadcastDto]:
        row = await self.session.scalar(
            select(Broadcast)
            .where(Broadcast.id == broadcast_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        return self._to_dto(row) if row is not None else None

    async def replace(self, broadcast_id: int, data: SaveBroadcastDto) -> BroadcastDto:
        row = await self._get_row(broadcast_id)
        for key, value in self._values(data).items():
            setattr(row, key, value)
        row.status = BroadcastStatus.DRAFT
        row.scheduled_at = None
        row.updated_at = datetime_now()
        await self.session.flush()
        return await self._with_creator(row)

    async def delete(self, broadcast_id: int) -> None:
        await self.session.delete(await self._get_row(broadcast_id))
        await self.session.flush()

    def _to_email_image_dto(self, row: BroadcastEmailImage) -> EmailImageDto:
        return EmailImageDto.model_validate(row, from_attributes=True)

    async def add_email_image(
        self, broadcast_id: int, content_type: str, extension: str, size: int
    ) -> EmailImageDto:
        row = BroadcastEmailImage(
            broadcast_id=broadcast_id,
            content_type=content_type,
            extension=extension,
            size_bytes=size,
        )
        self.session.add(row)
        await self.session.flush()
        return self._to_email_image_dto(row)

    async def list_email_images(self, broadcast_id: int) -> list[EmailImageDto]:
        rows = await self.session.scalars(
            select(BroadcastEmailImage)
            .where(BroadcastEmailImage.broadcast_id == broadcast_id)
            .order_by(BroadcastEmailImage.id)
        )
        return [self._to_email_image_dto(row) for row in rows]

    async def find_email_image(self, broadcast_id: int, image_id: int) -> Optional[EmailImageDto]:
        row = await self.session.scalar(
            select(BroadcastEmailImage).where(
                BroadcastEmailImage.broadcast_id == broadcast_id,
                BroadcastEmailImage.id == image_id,
            )
        )
        return None if row is None else self._to_email_image_dto(row)

    async def delete_email_images(self, ids: list[int]) -> None:
        await self.session.execute(
            delete(BroadcastEmailImage).where(in_ids(BroadcastEmailImage.id, ids))
        )

    async def _write(self, broadcast_id: int, values: dict[str, Any]) -> None:
        await self.session.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast_id)
            .values(**values, updated_at=datetime_now())
            .execution_options(synchronize_session="fetch")
        )

    async def touch(self, broadcast_id: int) -> None:
        # the stall watchdog reads updated_at
        await self._write(broadcast_id, {})

    async def schedule(self, broadcast_id: int, scheduled_at: datetime) -> None:
        await self._write(
            broadcast_id,
            {
                "status": BroadcastStatus.SCHEDULED,
                "scheduled_at": scheduled_at,
                "completed_at": None,
            },
        )

    async def mark_sending(self, broadcast_id: int, total_count: int, started_at: datetime) -> None:
        await self._write(
            broadcast_id,
            {
                "status": BroadcastStatus.SENDING,
                "total_count": total_count,
                "started_at": started_at,
            },
        )

    async def finish(
        self, broadcast_id: int, status: BroadcastStatus, completed_at: datetime
    ) -> None:
        await self._write(broadcast_id, {"status": status, "completed_at": completed_at})

    async def cancel_scheduled(self, broadcast_id: int, completed_at: datetime) -> None:
        await self._write(
            broadcast_id,
            {
                "status": BroadcastStatus.CANCELED,
                "scheduled_at": None,
                "completed_at": completed_at,
            },
        )

    async def set_media(self, broadcast_id: int, media: Optional[BroadcastMediaDto]) -> None:
        # any media change sends the broadcast back to draft; None removes the media
        values: dict[str, Any] = {
            "media_type": media.media_type if media else None,
            "media_content_type": media.content_type if media else None,
            "media_extension": media.extension if media else None,
            "media_size_bytes": media.size_bytes if media else None,
            "media_file_id": None,
            "status": BroadcastStatus.DRAFT,
            "scheduled_at": None,
        }
        if media is not None:
            values["media_version"] = media.version
        await self._write(broadcast_id, values)

    async def set_media_file_id(self, broadcast_id: int, file_id: str) -> None:
        await self._write(broadcast_id, {"media_file_id": file_id})

    async def set_channel_post(
        self, broadcast_id: int, chat_id: int, message_id: Optional[int], file_id: Optional[str]
    ) -> None:
        values: dict[str, Any] = {"channel_chat_id": chat_id, "channel_message_id": message_id}
        if file_id:
            values["media_file_id"] = file_id
        await self._write(broadcast_id, values)

    async def clear_channel_message(self, broadcast_id: int) -> None:
        await self._write(broadcast_id, {"channel_message_id": None})

    async def clear_promocode(self, broadcast_id: int) -> None:
        await self._write(broadcast_id, {"promocode_id": None})

    async def request_recall(self, broadcast_id: int, requested_at: datetime) -> None:
        await self._write(broadcast_id, {"recall_requested_at": requested_at})

    async def refresh_counts(self, broadcast_id: int) -> BroadcastDto:
        rows = await self.session.execute(
            select(BroadcastRecipient.status, func.count())
            .where(BroadcastRecipient.broadcast_id == broadcast_id)
            .group_by(BroadcastRecipient.status)
        )
        counts = dict(rows.tuples().all())
        row = await self.session.scalar(
            select(Broadcast)
            .where(Broadcast.id == broadcast_id)
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFoundError(
                f"Broadcast '{broadcast_id}' not found", code=ErrorCode.BROADCAST_NOT_FOUND
            )
        row.sent_count = counts.get(BroadcastRecipientStatus.SENT, 0)
        row.failed_count = counts.get(BroadcastRecipientStatus.FAILED, 0)
        row.blocked_count = counts.get(BroadcastRecipientStatus.BLOCKED, 0)
        row.recalled_count = counts.get(BroadcastRecipientStatus.RECALLED, 0)
        row.updated_at = datetime_now()
        await self.session.flush()
        return self._to_dto(row)

    async def lock_starts(self) -> None:
        await self.session.execute(select(func.pg_advisory_xact_lock(_START_LOCK_KEY)))

    async def has_sending(self) -> bool:
        return bool(
            await self.session.scalar(
                select(
                    select(Broadcast.id).where(Broadcast.status == BroadcastStatus.SENDING).exists()
                )
            )
        )

    async def next_due(self, now: datetime) -> Optional[int]:
        due: Optional[int] = await self.session.scalar(
            select(Broadcast.id)
            .where(Broadcast.status == BroadcastStatus.SCHEDULED, Broadcast.scheduled_at <= now)
            .order_by(Broadcast.scheduled_at, Broadcast.id)
            .limit(1)
        )
        return due

    async def list_failed_since(self, since: datetime) -> builtins.list[BroadcastDto]:
        rows = await self.session.scalars(
            select(Broadcast)
            .where(Broadcast.status == BroadcastStatus.FAILED, Broadcast.updated_at >= since)
            .order_by(Broadcast.updated_at.desc())
        )
        return [self._to_dto(row) for row in rows]

    async def list_stalled_broadcasts(self, before: datetime) -> builtins.list[BroadcastDto]:
        rows = await self.session.scalars(
            select(Broadcast)
            .where(Broadcast.status == BroadcastStatus.SENDING, Broadcast.updated_at < before)
            .order_by(Broadcast.updated_at)
        )
        return [self._to_dto(row) for row in rows]

    async def list_stalled(self, before: datetime) -> builtins.list[int]:
        rows = await self.session.scalars(
            select(Broadcast.id)
            .where(Broadcast.status == BroadcastStatus.SENDING, Broadcast.updated_at < before)
            .order_by(Broadcast.id)
        )
        return builtins.list(rows)

    async def channel_post(self, broadcast_id: int) -> Optional[tuple[int, int]]:
        row = (
            await self.session.execute(
                select(Broadcast.channel_chat_id, Broadcast.channel_message_id).where(
                    Broadcast.id == broadcast_id
                )
            )
        ).first()
        if row is None or row.channel_chat_id is None or row.channel_message_id is None:
            return None
        return row.channel_chat_id, row.channel_message_id

    async def list(self, data: ListBroadcastsDto) -> tuple[builtins.list[BroadcastDto], int]:
        conditions: builtins.list[ColumnElement[bool]] = []
        if data.statuses:
            conditions.append(Broadcast.status.in_(data.statuses))
        if data.search:
            texts = cast(
                func.jsonb_path_query_array(Broadcast.contents, cast(_TEXTS_PATH, JSONPATH)), Text
            )
            conditions.append(
                or_(
                    Broadcast.title.icontains(data.search, autoescape=True),
                    texts.icontains(data.search, autoescape=True),
                )
            )
        created_from = range_start_utc(data.created_from)
        created_to = range_end_utc(data.created_to)
        if created_from is not None:
            conditions.append(Broadcast.created_at >= created_from)
        if created_to is not None:
            conditions.append(Broadcast.created_at < created_to)
        total = await self.session.scalar(
            select(func.count()).select_from(Broadcast).where(*conditions)
        )
        rows = await self.session.scalars(
            select(Broadcast)
            .where(*conditions)
            .order_by(Broadcast.created_at.desc(), Broadcast.id.desc())
            .offset((data.page - 1) * data.page_size)
            .limit(data.page_size)
        )
        broadcasts = builtins.list(rows)
        names = await self._creator_names({r.created_by for r in broadcasts if r.created_by})
        return [self._to_dto(row, names.get(row.created_by or 0)) for row in broadcasts], total or 0

    async def summary(self, now: datetime) -> BroadcastSummaryDto:
        since = now - SUMMARY_WINDOW
        counts = dict(
            (
                await self.session.execute(
                    select(Broadcast.status, func.count()).group_by(Broadcast.status)
                )
            )
            .tuples()
            .all()
        )
        next_scheduled = await self.session.scalar(
            select(func.min(Broadcast.scheduled_at)).where(
                Broadcast.status == BroadcastStatus.SCHEDULED
            )
        )
        delivered = await self.session.scalar(
            select(func.count())
            .select_from(BroadcastRecipient)
            .join(Broadcast, Broadcast.id == BroadcastRecipient.broadcast_id)
            .where(
                Broadcast.started_at >= since,
                BroadcastRecipient.status == BroadcastRecipientStatus.SENT,
            )
        )
        processed = await self.session.scalar(
            select(func.count())
            .select_from(BroadcastRecipient)
            .join(Broadcast, Broadcast.id == BroadcastRecipient.broadcast_id)
            .where(
                Broadcast.started_at >= since,
                BroadcastRecipient.status.in_(_PROCESSED),
            )
        )
        customers = (
            User.deleted_at.is_(None),
            User.is_blocked.is_(False),
        )
        reachable = (*customers, or_(User.telegram_id.is_not(None), deliverable_email()))
        reachable_total = await self.session.scalar(select(func.count()).where(*reachable))
        by_email = await self.session.scalar(
            select(func.count()).where(*customers, deliverable_email())
        )
        opted_out = await self.session.scalar(
            select(func.count()).where(*reachable, User.marketing_opt_out.is_(True))
        )
        return BroadcastSummaryDto(
            by_status={status: counts.get(status, 0) for status in BroadcastStatus},
            next_scheduled_at=next_scheduled,
            delivered_30d=delivered or 0,
            processed_30d=processed or 0,
            opted_out=opted_out or 0,
            reachable_customers=reachable_total or 0,
            reachable_by_email=by_email or 0,
        )
