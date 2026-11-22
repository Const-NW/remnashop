from app.core.actor import Actor
from app.core.usecase import UseCase
from app.modules.notifications.sources import BotTokenSource
from app.modules.notifications.telegram_queue import AdminTelegramQueue


class DrainAdminTelegramUseCase(UseCase[None, int]):
    required_permission = None

    def __init__(
        self, bot_token_source: BotTokenSource, telegram_queue: AdminTelegramQueue
    ) -> None:
        self._bot_token_source = bot_token_source
        self._telegram_queue = telegram_queue

    async def _execute(self, actor: Actor, data: None) -> int:
        token, proxy = await self._bot_token_source.get_bot_token()
        if token is None:
            return 0
        return await self._telegram_queue.drain(token, proxy)
