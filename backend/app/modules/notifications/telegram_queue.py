from dataclasses import dataclass
from typing import Final, Optional

from loguru import logger
from pydantic import TypeAdapter
from redis.asyncio import Redis

from app.core.constants import REDIS_KEY_PREFIX, TTL_1D
from app.core.telegram_api import (
    BOT_API_ERRORS,
    BotApiRefusedError,
    bot_api_error_text,
    bot_client,
)
from app.modules.notifications.telegram import TELEGRAM_TIMEOUT_SECONDS

_HOLD: Final[str] = f"{REDIS_KEY_PREFIX}notification_telegram_hold"
_QUEUE: Final[str] = f"{REDIS_KEY_PREFIX}notification_telegram_queue"
# a flood lasting hours keeps the newest cards; older ones are what the bell still shows
_MAX_QUEUED: Final[int] = 500
_QUEUE_TTL_S: Final[int] = TTL_1D
# one drain fits the flush task's minute; the rest waits for the next run
_DRAIN_BATCH: Final[int] = 30


@dataclass(frozen=True, kw_only=True)
class _QueuedCard:
    chat_id: int
    text: str


_CARD_ADAPTER: Final[TypeAdapter[_QueuedCard]] = TypeAdapter(_QueuedCard)


async def _send(token: str, proxy: Optional[str], chat_id: int, text: str) -> Optional[float]:
    """Sends an HTML card; returns Telegram's flood wait when it refuses for one."""
    try:
        async with bot_client(token, proxy, timeout=TELEGRAM_TIMEOUT_SECONDS) as bot:
            await bot.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML")
    except BotApiRefusedError as error:
        if error.retry_after is not None:
            logger.warning(
                f"Telegram flood control: admin notifications held for {error.retry_after:.0f}s"
            )
            return error.retry_after
        logger.info(f"Telegram notification to {chat_id} refused: {error.description}")
    except BOT_API_ERRORS as error:
        logger.warning(f"Telegram notification to {chat_id} failed: {bot_api_error_text(error)}")
    return None


class AdminTelegramQueue:
    """Admin cards Telegram refused under flood control wait here, in order, until it
    lets the bot send again; `drain` runs from the notifications flush task."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def send(self, token: str, proxy: Optional[str], chat_id: int, text: str) -> None:
        # behind a flood wait or earlier cards: a direct send would jump the line
        if await self._redis.exists(_HOLD) or await self._redis.llen(_QUEUE):  # type: ignore[misc]
            await self._push(chat_id, text)
            return
        retry_after = await _send(token, proxy, chat_id, text)
        if retry_after is not None:
            await self._hold(retry_after)
            await self._push(chat_id, text)

    async def drain(self, token: str, proxy: Optional[str]) -> int:
        sent = 0
        while sent < _DRAIN_BATCH and not await self._redis.exists(_HOLD):
            raw = await self._redis.lindex(_QUEUE, 0)  # type: ignore[misc]
            if raw is None:
                break
            card = _CARD_ADAPTER.validate_json(raw)
            retry_after = await _send(token, proxy, card.chat_id, card.text)
            if retry_after is not None:
                await self._hold(retry_after)
                break
            # peeked, not popped: a card refused for flood stays first in line
            await self._redis.lpop(_QUEUE)  # type: ignore[misc]
            sent += 1
        return sent

    async def _hold(self, retry_after: float) -> None:
        await self._redis.set(_HOLD, "1", px=max(1, int(retry_after * 1000)))

    async def _push(self, chat_id: int, text: str) -> None:
        card = _CARD_ADAPTER.dump_json(_QueuedCard(chat_id=chat_id, text=text))
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.rpush(_QUEUE, card)
            pipe.ltrim(_QUEUE, -_MAX_QUEUED, -1)
            pipe.expire(_QUEUE, _QUEUE_TTL_S)
            await pipe.execute()
