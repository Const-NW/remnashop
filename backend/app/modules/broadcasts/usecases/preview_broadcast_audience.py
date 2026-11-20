from app.core.actor import Actor
from app.core.db.uow import UnitOfWork
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.core.utils.time import datetime_now
from app.modules.broadcasts.dto import AudiencePreviewDto, PreviewAudienceDto
from app.modules.broadcasts.recipient_repository import BroadcastRecipientRepository
from app.modules.broadcasts.repository import BroadcastRepository
from app.modules.broadcasts.validation import validate_audience


class PreviewBroadcastAudienceUseCase(UseCase[PreviewAudienceDto, AudiencePreviewDto]):
    required_permission = Permission.BROADCASTS_AUDIENCE_PREVIEW

    def __init__(
        self,
        broadcast_repository: BroadcastRepository,
        recipient_repository: BroadcastRecipientRepository,
        uow: UnitOfWork,
    ) -> None:
        self._broadcast_repository = broadcast_repository
        self._recipient_repository = recipient_repository
        self._uow = uow

    async def _execute(self, actor: Actor, data: PreviewAudienceDto) -> AudiencePreviewDto:
        validate_audience(data.audience)
        async with self._uow:
            return await self._recipient_repository.count_audience(
                data.audience, data.category, data.delivery, data.email_fallback, datetime_now()
            )
