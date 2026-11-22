from loguru import logger

from app.core.actor import Actor
from app.core.db.uow import UnitOfWork
from app.core.error_codes import ErrorCode
from app.core.events import changed_fields
from app.core.exceptions import ValidationError
from app.core.ports import EventPublisher
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.modules.settings.dto import GraceSettingsDto, UpdateGraceSettingsDto
from app.modules.settings.events import GraceSettingsChangedEvent
from app.modules.settings.repository import SettingsRepository


class UpdateGraceSettingsUseCase(UseCase[UpdateGraceSettingsDto, GraceSettingsDto]):
    required_permission = Permission.GRACE_SETTINGS_MANAGE

    def __init__(
        self,
        settings_repository: SettingsRepository,
        uow: UnitOfWork,
        event_publisher: EventPublisher,
    ) -> None:
        self._settings_repository = settings_repository
        self._uow = uow
        self._event_publisher = event_publisher

    async def _execute(self, actor: Actor, data: UpdateGraceSettingsDto) -> GraceSettingsDto:
        if (data.traffic_limit_mb is not None and data.traffic_limit_mb < 0) or (
            data.duration_days is not None and data.duration_days < 0
        ):
            raise ValidationError(
                "traffic_limit_mb and duration_days cannot be negative",
                code=ErrorCode.GRACE_SETTINGS_INVALID,
            )

        async with self._uow:
            before = await self._settings_repository.get_grace_settings()
            settings = await self._settings_repository.update_grace_settings(data)
            await self._event_publisher.publish(
                GraceSettingsChangedEvent(
                    actor=actor, changed_fields=changed_fields(before, settings, data.fields_set)
                )
            )
        logger.info(f"{actor.log} Updated grace settings")
        return settings
