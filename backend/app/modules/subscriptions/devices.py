from typing import Optional

from remnapy import RemnawaveSDK

from app.core.db.uow import UnitOfWork
from app.modules.subscriptions.device_name_repository import DeviceNameRepository
from app.modules.subscriptions.dto import DeviceDto, SubscriptionDto


def client_app(user_agent: Optional[str]) -> Optional[str]:
    """`Happ/2.1.0/ios`, `v2rayNG/1.9.30`, `HiddifyNext/2.5.7 (android)` -> `Happ 2.1.0`."""
    tokens = (user_agent or "").split()
    if not tokens:
        return None
    name, _, rest = tokens[0].partition("/")
    if not name:
        return None
    version = rest.split("/", 1)[0].removeprefix("v")
    return f"{name} {version}" if any(char.isdigit() for char in version) else name


async def list_devices(
    remnawave_sdk: RemnawaveSDK,
    device_names: DeviceNameRepository,
    uow: UnitOfWork,
    subscription: SubscriptionDto,
) -> list[DeviceDto]:
    response = await remnawave_sdk.hwid.get_hwid_user(subscription.remnawave_user_id)
    async with uow:
        names = await device_names.names_for(subscription.id)
    return [
        DeviceDto(
            hwid=device.hwid,
            name=names.get(device.hwid),
            platform=device.platform,
            os_version=device.os_version,
            device_model=device.device_model,
            app=client_app(device.user_agent),
            user_agent=device.user_agent,
            created_at=device.created_at,
            last_fetched_at=device.updated_at,
        )
        for device in response.devices
    ]
