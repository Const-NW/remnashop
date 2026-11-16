import asyncio

from app.core.db.uow import UnitOfWork
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.modules.plans import PlanRepository
from app.modules.subscriptions.dto import OwnSubscriptionUsageDto
from app.modules.subscriptions.plan_labels import with_current_plan_labels
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.subscriptions.usage import PanelTrafficUsage
from app.modules.users import UserDto


class ListOwnSubscriptionsUsageUseCase(UseCase[None, list[OwnSubscriptionUsageDto], UserDto]):
    required_permission = Permission.PUBLIC

    def __init__(
        self,
        subscription_repository: SubscriptionRepository,
        plan_repository: PlanRepository,
        usage: PanelTrafficUsage,
        uow: UnitOfWork,
    ) -> None:
        self._subscription_repository = subscription_repository
        self._plan_repository = plan_repository
        self._usage = usage
        self._uow = uow

    async def _execute(self, actor: UserDto, data: None) -> list[OwnSubscriptionUsageDto]:
        async with self._uow:
            # EXPIRED included: the cabinet shows it to renew, as the bot does
            subscriptions = await with_current_plan_labels(
                await self._subscription_repository.get_live_by_user(actor.id),
                self._plan_repository,
            )
        # the panel is queried outside the transaction so a slow panel can't hold a connection
        used = await asyncio.gather(*(self._usage.used_traffic(s) for s in subscriptions))
        return [
            OwnSubscriptionUsageDto(subscription=s, used_traffic=u)
            for s, u in zip(subscriptions, used, strict=True)
        ]
