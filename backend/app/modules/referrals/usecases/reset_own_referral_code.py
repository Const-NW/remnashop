from datetime import timedelta

from loguru import logger

from app.core.db.uow import UnitOfWork
from app.core.error_codes import ErrorCode
from app.core.exceptions import CooldownError, NotFoundError, ValidationError
from app.core.ports import EventPublisher
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.core.utils.time import datetime_now
from app.modules.referrals.events import ReferralCodeResetEvent
from app.modules.settings import SettingsRepository
from app.modules.users import UserDto, UserRepository


class ResetOwnReferralCodeUseCase(UseCase[None, UserDto, UserDto]):
    required_permission = Permission.PUBLIC

    def __init__(
        self,
        user_repository: UserRepository,
        settings_repository: SettingsRepository,
        uow: UnitOfWork,
        event_publisher: EventPublisher,
    ) -> None:
        self._user_repository = user_repository
        self._settings_repository = settings_repository
        self._uow = uow
        self._event_publisher = event_publisher

    async def _execute(self, actor: UserDto, data: None) -> UserDto:
        async with self._uow:
            shop_settings = await self._settings_repository.get_shop_settings()
            if not shop_settings.referral_reset_enabled:
                raise ValidationError(
                    "Referral code reset is disabled", code=ErrorCode.REFERRAL_RESET_DISABLED
                )
            # under the row lock: a double tap waits here and then meets the cooldown
            user = await self._user_repository.find_live_for_update(actor.id)
            if user is None:
                raise NotFoundError(f"User '{actor.id}' not found", code=ErrorCode.USER_NOT_FOUND)
            now = datetime_now()
            cooldown = timedelta(hours=shop_settings.referral_reset_cooldown_hours)
            previous = user.referral_code_reset_at
            if previous is not None and now < previous + cooldown:
                raise CooldownError(previous + cooldown)
            updated = await self._user_repository.reset_own_referral_code(actor.id, now)
            await self._event_publisher.publish(ReferralCodeResetEvent(actor=actor, target=updated))

        logger.info(f"{actor.log} Reset own referral code")
        return updated
