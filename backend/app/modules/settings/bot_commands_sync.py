import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Final

from loguru import logger
from redis.asyncio import Redis
from redis.exceptions import LockNotOwnedError, RedisError

from app.core.constants import REDIS_KEY_PREFIX
from app.core.db.uow import UnitOfWork
from app.core.enums import Locale
from app.modules.settings.command_translations import (
    CommandMenu,
    build_command_menu,
    delete_bot_commands_for_locale,
    push_bot_commands_to_telegram,
)
from app.modules.settings.dto import GetBotCommandsDto
from app.modules.settings.repository import SettingsRepository
from app.modules.settings.usecases import GetBotCommandsUseCase
from app.modules.translations import TranslationRepository

# api and telegram push concurrently: a late push of an older menu must not
# land after a newer one's hash is stored
_LOCK_KEY: Final[str] = f"{REDIS_KEY_PREFIX}bot_commands_push"
# a push that finds the lock taken leaves this for the holder to send it
_PENDING_KEY: Final[str] = f"{REDIS_KEY_PREFIX}bot_commands_push_pending"
_PENDING: Final[str] = "1"
_PENDING_FORCE: Final[str] = "force"
# outlasts a push of every locale at the Bot API timeout
_LOCK_TTL_S: Final[int] = 900
_LOCK_WAIT_S: Final[float] = 30


class BotCommandsPusher:
    """BotCommandsSync over the Bot API: the command menu is built from the
    stored command order and the active locales' translations."""

    def __init__(
        self,
        settings_repository: SettingsRepository,
        translation_repository: TranslationRepository,
        get_bot_commands_use_case: GetBotCommandsUseCase,
        uow: UnitOfWork,
        redis: Redis,
    ) -> None:
        self._settings_repository = settings_repository
        self._translation_repository = translation_repository
        self._get_bot_commands_use_case = get_bot_commands_use_case
        self._uow = uow
        self._redis = redis

    async def push(self, *, force: bool = False) -> bool:
        # False when Telegram (or reading what to send) failed; True without a bot,
        # when Telegram already has this menu or a running push takes it over
        try:
            await self._redis.set(_PENDING_KEY, _PENDING_FORCE if force else _PENDING)
        except RedisError:
            logger.exception("Bot commands were not pushed: Redis is unavailable")
            return False
        pushed = True
        # the pending mark is set before the lock is tried, so a holder that releases
        # without seeing it leaves the lock free for this push
        while await self._has_pending():
            lock = self._redis.lock(_LOCK_KEY, timeout=_LOCK_TTL_S)
            try:
                if not await lock.acquire(blocking=False):
                    return pushed
            except RedisError:
                logger.exception("Bot commands were not pushed: Redis is unavailable")
                return False
            try:
                pending = await self._redis.getdel(_PENDING_KEY)
                pushed = await self._push(pending == _PENDING_FORCE)
            finally:
                with suppress(LockNotOwnedError, RedisError):
                    await lock.release()
        return pushed

    async def _has_pending(self) -> bool:
        try:
            return bool(await self._redis.exists(_PENDING_KEY))
        except RedisError:
            logger.exception("Failed to check for a pending bot commands push")
            return False

    async def _push(self, force: bool) -> bool:
        try:
            async with self._uow:
                bot_settings = await self._settings_repository.get_bot_settings()
            token = bot_settings.token
            if not bot_settings.is_configured or token is None:
                return True
            commands = await self._get_bot_commands_use_case.system(GetBotCommandsDto())
            async with self._uow:
                active_locales = await self._translation_repository.list_active_locales()
                translations_by_locale = (
                    await self._translation_repository.get_fluent_text_by_locale(active_locales)
                )
                default_locale = await self._translation_repository.get_default_locale()
            menu = build_command_menu(
                [item for item in commands if item.enabled],
                active_locales,
                translations_by_locale,
                default_locale,
            )
            token_value = token.get_secret_value()
            menu_hash = _menu_hash(token_value, menu)
            if not force and menu_hash == bot_settings.commands_hash:
                return True
            if bot_settings.commands_hash is not None:
                # a push failing halfway leaves Telegram on neither menu: until one
                # succeeds, no menu may count as sent
                async with self._uow:
                    await self._settings_repository.set_bot_commands_hash(None)
        except Exception:
            logger.exception("Failed to prepare bot commands for the Telegram push")
            return False
        if not await push_bot_commands_to_telegram(token_value, bot_settings.proxy, menu):
            return False
        try:
            async with self._uow:
                await self._settings_repository.set_bot_commands_hash(menu_hash)
        except Exception:
            # Telegram has the menu; the next push just sends it again
            logger.exception("Failed to record the pushed bot commands")
        return True

    async def drop_locale(self, locale: Locale) -> None:
        # unlocked as a last resort: clearing the hash only costs a push
        async with self._locked():
            async with self._uow:
                bot_settings = await self._settings_repository.get_bot_settings()
            if bot_settings.token is not None:
                # the locale may come back with the same texts: its next push must go out
                async with self._uow:
                    await self._settings_repository.set_bot_commands_hash(None)
                await delete_bot_commands_for_locale(
                    bot_settings.token.get_secret_value(), bot_settings.proxy, locale
                )

    @asynccontextmanager
    async def _locked(self) -> AsyncIterator[bool]:
        lock = self._redis.lock(_LOCK_KEY, timeout=_LOCK_TTL_S)
        try:
            acquired = bool(await lock.acquire(blocking_timeout=_LOCK_WAIT_S))
        except RedisError:
            logger.exception("Failed to take the bot commands push lock")
            acquired = False
        try:
            yield acquired
        finally:
            if acquired:
                with suppress(LockNotOwnedError, RedisError):
                    await lock.release()


def _menu_hash(token: str, menu: CommandMenu) -> str:
    # keyed by the bot id: a new bot gets the menu even with the same texts
    bot_id = token.partition(":")[0]
    payload = json.dumps([bot_id, sorted(menu.items(), key=lambda item: item[0] or "")])
    return hashlib.sha256(payload.encode()).hexdigest()
