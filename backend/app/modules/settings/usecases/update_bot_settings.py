import secrets
from dataclasses import replace
from typing import Optional

from loguru import logger

from app.core.actor import Actor
from app.core.db.uow import UnitOfWork
from app.core.events import changed_fields
from app.core.ports import EventPublisher
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.modules.settings.dto import BotSettingsDto, UpdateBotSettingsDto
from app.modules.settings.events import BotSettingsChangedEvent
from app.modules.settings.repository import SettingsRepository


def _token(settings: BotSettingsDto) -> Optional[str]:
    return settings.token.get_secret_value() if settings.token is not None else None


class UpdateBotSettingsUseCase(UseCase[UpdateBotSettingsDto, BotSettingsDto]):
    required_permission = Permission.BOT_SETTINGS_MANAGE

    def __init__(
        self,
        settings_repository: SettingsRepository,
        uow: UnitOfWork,
        event_publisher: EventPublisher,
    ) -> None:
        self._settings_repository = settings_repository
        self._uow = uow
        self._event_publisher = event_publisher

    async def _execute(self, actor: Actor, data: UpdateBotSettingsDto) -> BotSettingsDto:
        async with self._uow:
            before = await self._settings_repository.get_bot_settings()
            # the webhook secret only links us to Telegram: nobody needs to type it
            if before.secret_token is None and data.secret_token is None:
                data = replace(data, secret_token=secrets.token_urlsafe(32))
            if data.token is not None and data.token != _token(before):
                # a new bot: the next start reports its username
                await self._settings_repository.set_bot_username(None)
            settings = await self._settings_repository.update_bot_settings(data)
            await self._event_publisher.publish(
                BotSettingsChangedEvent(
                    actor=actor,
                    action="update",
                    changed_fields=changed_fields(before, settings, vars(data)),
                )
            )
        logger.info(f"{actor.log} Updated bot settings")
        return settings
