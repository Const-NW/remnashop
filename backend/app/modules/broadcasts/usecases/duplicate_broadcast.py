import shutil
from pathlib import Path

from loguru import logger

from app.core.actor import Actor, actor_user_id
from app.core.config import AppConfig
from app.core.db.uow import UnitOfWork
from app.core.ports import EventPublisher
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.modules.broadcasts.dto import BroadcastDto, BroadcastEmailContentDto
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
        media_dir = self._config.media_dir
        async with self._uow:
            # the lock keeps the source's files in place while they are copied
            source = await self._broadcast_repository.get_by_id_for_update(data)
            copy = await self._broadcast_repository.duplicate(source.id, actor_user_id(actor))
            try:
                if source.media_extension is not None:
                    _copy(
                        media_path(
                            media_dir, source.id, source.media_version, source.media_extension
                        ),
                        media_path(media_dir, copy.id, copy.media_version, source.media_extension),
                    )
                new_ids: dict[int, int] = {}
                for image in await self._broadcast_repository.list_email_images(source.id):
                    added = await self._broadcast_repository.add_email_image(
                        copy.id, image.content_type, image.extension, image.size_bytes
                    )
                    _copy(
                        email_image_path(media_dir, source.id, image.id, image.extension),
                        email_image_path(media_dir, copy.id, added.id, image.extension),
                    )
                    new_ids[image.id] = added.id
            except BaseException:
                if source.media_extension is not None:
                    delete_media(media_dir, copy.id, copy.media_version, source.media_extension)
                delete_all_email_images(media_dir, copy.id)
                raise
            if new_ids:
                email_contents = {
                    locale: BroadcastEmailContentDto(
                        subject=content.subject,
                        html=replace_email_image_ids(content.html, new_ids),
                    )
                    for locale, content in source.email_contents.items()
                }
                await self._broadcast_repository.set_email_contents(copy.id, email_contents)
            await self._event_publisher.publish(
                BroadcastChangedEvent(actor=actor, action="duplicate", broadcast_id=copy.id)
            )
        logger.info(f"{actor.log} Duplicated broadcast '{source.id}' as '{copy.id}'")
        return await self._broadcast_repository.get_by_id(copy.id)
