from remnapy import RemnawaveSDK

from app.core.actor import Actor
from app.core.db.uow import UnitOfWork
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.modules.subscriptions.device_name_repository import DeviceNameRepository
from app.modules.subscriptions.devices import list_devices
from app.modules.subscriptions.dto import DeviceDto
from app.modules.subscriptions.eligibility import get_manageable_subscription
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.users import UserRepository


class AdminListDevicesUseCase(UseCase[int, list[DeviceDto]]):
    required_permission = Permission.SUBSCRIPTIONS_DEVICES_VIEW

    def __init__(
        self,
        subscription_repository: SubscriptionRepository,
        user_repository: UserRepository,
        device_name_repository: DeviceNameRepository,
        remnawave_sdk: RemnawaveSDK,
        uow: UnitOfWork,
    ) -> None:
        self._device_name_repository = device_name_repository
        self._subscription_repository = subscription_repository
        self._user_repository = user_repository
        self._remnawave_sdk = remnawave_sdk
        self._uow = uow

    async def _execute(self, actor: Actor, data: int) -> list[DeviceDto]:
        async with self._uow:
            subscription = await get_manageable_subscription(
                self._subscription_repository, self._user_repository, actor, data, for_read=True
            )
        return await list_devices(
            self._remnawave_sdk, self._device_name_repository, self._uow, subscription
        )
