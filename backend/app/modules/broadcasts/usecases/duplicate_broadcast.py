import asyncio
import shutil
from pathlib import Path

from loguru import logger

from app.core.actor import Actor, actor_user_id
from app.core.config import AppConfig
from app.core.db.uow import UnitOfWork
from app.core.ports import EventPublisher
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.modules.broadcasts.dto import BroadcastDto, BroadcastEmailContentDto, EmailImageDto
from app.modules.broadcasts.email_html import replace_email_image_ids
from app.modules.broadcasts.events import BroadcastChangedEvent
from app.modules.broadcasts.repository import BroadcastRepository
from app.modules.broadcasts.storage import (
    delete_all_email_images,
    delete_media,
    email_image_path,
    media_path,
)


def _copy(source: Path, target: Path) -> None:
    # a source file missing on disk leaves the copy as broken as the source, nothing more
    if not source.exists():
        logger.warning(f"Broadcast file '{source}' is missing, not copied")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


class DuplicateBroadcastUseCase(UseCase[int, BroadcastDto]):
    required_permission = Permission.BROADCASTS_DUPLICATE

    def __init__(
        self,
        broadcast_repository: BroadcastRepository,
        config: AppConfig,
        uow: UnitOfWork,
        event_publisher: EventPublisher,
    ) -> None:
        self._broadcast_repository = broadcast_repository
        self._config = config
        self._uow = uow
        self._event_publisher = event_publisher

    async def _execute(self, actor: Actor, data: int) -> BroadcastDto:
        async with self._uow:
            source = await self._broadcast_repository.get_by_id(data)
            images = await self._broadcast_repository.list_email_images(source.id)
            copy = await self._broadcast_repository.duplicate(source.id, actor_user_id(actor))
            new_images: dict[int, EmailImageDto] = {}
            for image in images:
                new_images[image.id] = await self._broadcast_repository.add_email_image(
                    copy.id, image.content_type, image.extension, image.size_bytes
                )
            if new_images:
                new_ids = {old: new.id for old, new in new_images.items()}
                await self._broadcast_repository.set_email_contents(
                    copy.id,
                    {
                        locale: BroadcastEmailContentDto(
                            subject=content.subject,
                            html=replace_email_image_ids(content.html, new_ids),
                        )
                        for locale, content in source.email_contents.items()
                    },
                )
            await self._event_publisher.publish(
                BroadcastChangedEvent(
                    actor=actor,
                    action="duplicate",
                    broadcast_id=copy.id,
                    source_broadcast_id=source.id,
                )
            )
            result = await self._broadcast_repository.get_by_id(copy.id)

        # the files land only after the rows commit
        try:
            await asyncio.to_thread(self._copy_files, source, copy, images, new_images)
        except BaseException:
            await self._discard(actor, copy)
            raise
        logger.info(f"{actor.log} Duplicated broadcast '{source.id}' as '{copy.id}'")
        return result

    def _copy_files(
        self,
        source: BroadcastDto,
        copy: BroadcastDto,
        images: list[EmailImageDto],
        new_images: dict[int, EmailImageDto],
    ) -> None:
        media_dir = self._config.media_dir
        if source.media_extension is not None:
            _copy(
                media_path(media_dir, source.id, source.media_version, source.media_extension),
                media_path(media_dir, copy.id, copy.media_version, source.media_extension),
            )
        for image in images:
            _copy(
                email_image_path(media_dir, source.id, image.id, image.extension),
                email_image_path(media_dir, copy.id, new_images[image.id].id, image.extension),
            )

    async def _discard(self, actor: Actor, copy: BroadcastDto) -> None:
        if copy.media_extension is not None:
            delete_media(self._config.media_dir, copy.id, copy.media_version, copy.media_extension)
        delete_all_email_images(self._config.media_dir, copy.id)
        async with self._uow:
            await self._broadcast_repository.delete(copy.id)
            await self._event_publisher.publish(
                BroadcastChangedEvent(actor=actor, action="delete", broadcast_id=copy.id)
            )
