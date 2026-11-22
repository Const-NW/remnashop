from remnapy import RemnawaveSDK

from app.core.constants import DEVICE_NAME_MAX_LENGTH
from app.core.db.uow import UnitOfWork
from app.core.error_codes import ErrorCode
from app.core.exceptions import NotFoundError, ValidationError
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.modules.subscriptions.device_name_repository import DeviceNameRepository
from app.modules.subscriptions.dto import RenameDeviceDto
from app.modules.subscriptions.eligibility import get_owned_live_target
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.users import UserDto


class RenameDeviceUseCase(UseCase[RenameDeviceDto, None, UserDto]):
    required_permission = Permission.PUBLIC

    def __init__(
        self,
        subscription_repository: SubscriptionRepository,
        device_name_repository: DeviceNameRepository,
        remnawave_sdk: RemnawaveSDK,
        uow: UnitOfWork,
    ) -> None:
        self._subscription_repository = subscription_repository
        self._device_name_repository = device_name_repository
        self._remnawave_sdk = remnawave_sdk
        self._uow = uow

    async def _execute(self, actor: UserDto, data: RenameDeviceDto) -> None:
        # zero-width and control characters would leave a blank button in the bot
        visible = "".join(char for char in data.name or "" if char.isprintable() or char.isspace())
        name = " ".join(visible.split())
        if len(name) > DEVICE_NAME_MAX_LENGTH:
            raise ValidationError("Device name is too long", code=ErrorCode.DEVICE_NAME_TOO_LONG)

        async with self._uow:
            subscription = await get_owned_live_target(
                self._subscription_repository, data.subscription_id, actor.id
            )

        if not name:
            async with self._uow:
                await self._device_name_repository.clear(subscription.id, data.hwid)
            return

        # only a device the panel knows: no rows for made-up HWIDs
        response = await self._remnawave_sdk.hwid.get_hwid_user(subscription.remnawave_user_id)
        if not any(device.hwid == data.hwid for device in response.devices):
            raise NotFoundError("Device not found", code=ErrorCode.DEVICE_NOT_FOUND)
        async with self._uow:
            await self._device_name_repository.set(subscription.id, data.hwid, name)
