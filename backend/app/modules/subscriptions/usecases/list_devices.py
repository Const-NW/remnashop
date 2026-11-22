from remnapy import RemnawaveSDK

from app.core.db.uow import UnitOfWork
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.modules.subscriptions.device_name_repository import DeviceNameRepository
from app.modules.subscriptions.devices import list_devices
from app.modules.subscriptions.dto import DeviceDto, SubscriptionRefDto
from app.modules.subscriptions.eligibility import get_owned_live_target
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.users import UserDto


class ListDevicesUseCase(UseCase[SubscriptionRefDto, list[DeviceDto], UserDto]):
    required_permission = Permission.PUBLIC

    def __init__(
        self,
        subscription_repository: SubscriptionRepository,
        device_name_repository: DeviceNameRepository,
        remnawave_sdk: RemnawaveSDK,
        uow: UnitOfWork,
    ) -> None:
        self._device_name_repository = device_name_repository
        self._subscription_repository = subscription_repository
        self._remnawave_sdk = remnawave_sdk
        self._uow = uow

    async def _execute(self, actor: UserDto, data: SubscriptionRefDto) -> list[DeviceDto]:
        async with self._uow:
            subscription = await get_owned_live_target(
                self._subscription_repository, data.subscription_id, actor.id
            )
        return await list_devices(
            self._remnawave_sdk, self._device_name_repository, self._uow, subscription
        )
