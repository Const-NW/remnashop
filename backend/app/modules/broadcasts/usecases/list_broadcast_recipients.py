from app.core.actor import Actor
from app.core.db.uow import UnitOfWork
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.modules.broadcasts.dto import BroadcastRecipientDto, ListBroadcastRecipientsDto
from app.modules.broadcasts.recipient_repository import BroadcastRecipientRepository
from app.modules.broadcasts.repository import BroadcastRepository


class ListBroadcastRecipientsUseCase(
    UseCase[ListBroadcastRecipientsDto, tuple[list[BroadcastRecipientDto], int]]
):
    required_permission = Permission.BROADCASTS_RECIPIENTS_VIEW

    def __init__(
        self,
        broadcast_repository: BroadcastRepository,
        recipient_repository: BroadcastRecipientRepository,
        uow: UnitOfWork,
    ) -> None:
        self._broadcast_repository = broadcast_repository
        self._recipient_repository = recipient_repository
        self._uow = uow

    async def _execute(
        self, actor: Actor, data: ListBroadcastRecipientsDto
    ) -> tuple[list[BroadcastRecipientDto], int]:
        async with self._uow:
            await self._broadcast_repository.get_by_id(data.broadcast_id)
            return await self._recipient_repository.list_recipients(data)
