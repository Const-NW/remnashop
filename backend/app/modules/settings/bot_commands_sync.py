import hashlib
import json

from loguru import logger

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


class BotCommandsPusher:
    """BotCommandsSync over the Bot API: the command menu is built from the
    stored command order and the active locales' translations."""

    def __init__(
        self,
        settings_repository: SettingsRepository,
        translation_repository: TranslationRepository,
        get_bot_commands_use_case: GetBotCommandsUseCase,
        uow: UnitOfWork,
    ) -> None:
        self._settings_repository = settings_repository
        self._translation_repository = translation_repository
        self._get_bot_commands_use_case = get_bot_commands_use_case
        self._uow = uow

    async def push(self, *, force: bool = False) -> bool:
        # False when Telegram (or reading what to send) failed; True without a bot
        # or when Telegram already has this menu
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
        except Exception:
            logger.exception("Failed to prepare bot commands for the Telegram push")
            return False
        token_value = token.get_secret_value()
        menu_hash = _menu_hash(token_value, menu)
        if not force and menu_hash == bot_settings.commands_hash:
            return True
        if not await push_bot_commands_to_telegram(token_value, bot_settings.proxy, menu):
            return False
        async with self._uow:
            await self._settings_repository.set_bot_commands_hash(menu_hash)
        return True

    async def drop_locale(self, locale: Locale) -> None:
        async with self._uow:
            bot_settings = await self._settings_repository.get_bot_settings()
        if bot_settings.token is not None:
            # the locale may come back with the same texts: its next push must go out
            async with self._uow:
                await self._settings_repository.set_bot_commands_hash(None)
            await delete_bot_commands_for_locale(
                bot_settings.token.get_secret_value(), bot_settings.proxy, locale
            )


def _menu_hash(token: str, menu: CommandMenu) -> str:
    # keyed by the bot id: a new bot gets the menu even with the same texts
    bot_id = token.partition(":")[0]
    payload = json.dumps([bot_id, sorted(menu.items(), key=lambda item: item[0] or "")])
    return hashlib.sha256(payload.encode()).hexdigest()
