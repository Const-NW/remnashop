from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import SubscriptionDeviceName
from app.core.utils.time import datetime_now


class DeviceNameRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def names_for(self, subscription_id: int) -> dict[str, str]:
        rows = await self.session.execute(
            select(SubscriptionDeviceName.hwid, SubscriptionDeviceName.name).where(
                SubscriptionDeviceName.subscription_id == subscription_id
            )
        )
        return dict(rows.tuples().all())

    async def set(self, subscription_id: int, hwid: str, name: str) -> None:
        statement = insert(SubscriptionDeviceName).values(
            subscription_id=subscription_id, hwid=hwid, name=name
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    SubscriptionDeviceName.subscription_id,
                    SubscriptionDeviceName.hwid,
                ],
                set_={"name": name, "updated_at": datetime_now()},
            )
        )

    async def clear(self, subscription_id: int, hwid: str) -> None:
        await self.session.execute(
            delete(SubscriptionDeviceName).where(
                SubscriptionDeviceName.subscription_id == subscription_id,
                SubscriptionDeviceName.hwid == hwid,
            )
        )
