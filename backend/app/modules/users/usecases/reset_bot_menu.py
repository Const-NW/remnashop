from loguru import logger

from app.core.actor import Actor
from app.core.db.uow import UnitOfWork
from app.core.error_codes import ErrorCode
from app.core.exceptions import ConflictError, ValidationError
from app.core.ports import BotSignals, EventPublisher
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.modules.users.dto import ResetBotMenuDto
from app.modules.users.events import UserBotMenuResetEvent
from app.modules.users.repository import UserRepository
from app.modules.users.visibility import assert_manageable, get_visible_target


class ResetBotMenuUseCase(UseCase[ResetBotMenuDto, None]):
    """Support's way out of a stuck bot: the customer's dialog starts over at the main menu."""

    required_permission = Permission.USERS_BOT_MENU_RESET

    def __init__(
        self,
        user_repository: UserRepository,
        bot_signals: BotSignals,
        uow: UnitOfWork,
        event_publisher: EventPublisher,
    ) -> None:
        self._user_repository = user_repository
        self._bot_signals = bot_signals
        self._uow = uow
        self._event_publisher = event_publisher

    async def _execute(self, actor: Actor, data: ResetBotMenuDto) -> None:
        async with self._uow:
            target = await get_visible_target(self._user_repository, actor, data.user_id)
            assert_manageable(actor, target)
        if target.telegram_id is None:
            raise ValidationError("User has no Telegram account", code=ErrorCode.USER_NO_TELEGRAM)
        if target.is_blocked:
            # the bot answers a blocked user with nothing but the block notice
            raise ValidationError("User is blocked", code=ErrorCode.USER_TARGET_BLOCKED)
        if target.is_bot_blocked:
            # Telegram refuses every message to a chat that blocked the bot
            raise ValidationError("User blocked the bot", code=ErrorCode.USER_TARGET_BOT_BLOCKED)

        if await self._bot_signals.reset_customer_menu(target.telegram_id) == 0:
            raise ConflictError("The bot is not running", code=ErrorCode.BOT_NOT_RUNNING)

        async with self._uow:
            await self._event_publisher.publish(
                UserBotMenuResetEvent(actor=actor, target=target, ip_address=data.client_ip)
            )
        logger.info(f"{actor.log} Reset the bot menu of user '{data.user_id}'")
