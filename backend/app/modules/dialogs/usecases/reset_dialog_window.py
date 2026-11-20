from app.core.actor import Actor
from app.core.db.uow import UnitOfWork
from app.core.enums import DialogWindowKind
from app.core.error_codes import ErrorCode
from app.core.exceptions import ValidationError
from app.core.ports import BotSignals, EventPublisher
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.modules.dialogs.dto import DialogWindowDto
from app.modules.dialogs.events import DialogChangedEvent
from app.modules.dialogs.repository import DialogRepository


class ResetDialogWindowUseCase(UseCase[str, DialogWindowDto]):
    required_permission = Permission.DIALOGS_WINDOWS_RESET

    def __init__(
        self,
        dialog_repository: DialogRepository,
        uow: UnitOfWork,
        bot_signals: BotSignals,
        event_publisher: EventPublisher,
    ) -> None:
        self._dialog_repository = dialog_repository
        self._uow = uow
        self._bot_signals = bot_signals
        self._event_publisher = event_publisher

    async def _execute(self, actor: Actor, data: str) -> DialogWindowDto:
        async with self._uow:
            current = await self._dialog_repository.get_by_key_or_raise(data)
            if current.kind == DialogWindowKind.CUSTOM:
                raise ValidationError(
                    "Custom windows have no default to reset to; delete instead",
                    code=ErrorCode.DIALOG_WINDOW_NOT_RESETTABLE,
                )
            window = await self._dialog_repository.reset_to_snapshot(data)
            await self._event_publisher.publish(
                DialogChangedEvent(actor=actor, action="reset", window_key=data)
            )
        await self._bot_signals.dialogs_changed()
        return window
