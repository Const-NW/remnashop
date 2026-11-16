from typing import Any, Awaitable, Callable, ClassVar, cast

from aiogram.types import ErrorEvent as AiogramErrorEvent
from aiogram.types import TelegramObject
from loguru import logger

from app.core.enums import MiddlewareEventType
from app.telegram.middlewares.base import EventTypedMiddleware


class ErrorMiddleware(EventTypedMiddleware):
    __event_types__: ClassVar[list[MiddlewareEventType]] = [MiddlewareEventType.ERROR]

    async def middleware_logic(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        error_event = cast(AiogramErrorEvent, event)
        logger.opt(exception=error_event.exception).error("Unhandled error in Telegram handler")
        return await handler(event, data)
