from app.core.actor import Actor
from app.core.db.uow import UnitOfWork
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.modules.settings.dto import GraceSettingsDto
from app.modules.settings.repository import SettingsRepository


class GetGraceSettingsUseCase(UseCase[None, GraceSettingsDto]):
    required_permission = Permission.GRACE_SETTINGS_VIEW

    def __init__(self, settings_repository: SettingsRepository, uow: UnitOfWork) -> None:
        self._settings_repository = settings_repository
        self._uow = uow

    async def _execute(self, actor: Actor, data: None) -> GraceSettingsDto:
        async with self._uow:
            return await self._settings_repository.get_grace_settings()
