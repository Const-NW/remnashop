from app.core.actor import Actor
from app.core.db.uow import UnitOfWork
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.modules.subscriptions.dto import SubscriptionDetailsDto
from app.modules.subscriptions.eligibility import get_visible_subscription
from app.modules.subscriptions.lifecycle import SubscriptionLifecycleService
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.subscriptions.usage import PanelTrafficUsage
from app.modules.users import UserRepository


class GetSubscriptionUseCase(UseCase[int, SubscriptionDetailsDto]):
    required_permission = Permission.SUBSCRIPTIONS_DETAIL_VIEW

    def __init__(
        self,
        subscription_repository: SubscriptionRepository,
        user_repository: UserRepository,
        usage: PanelTrafficUsage,
        uow: UnitOfWork,
        lifecycle_service: SubscriptionLifecycleService,
    ) -> None:
        self._subscription_repository = subscription_repository
        self._user_repository = user_repository
        self._usage = usage
        self._uow = uow
        self._lifecycle_service = lifecycle_service

    async def _execute(self, actor: Actor, data: int) -> SubscriptionDetailsDto:
        async with self._uow:
            subscription = await get_visible_subscription(
                self._subscription_repository, self._user_repository, actor, data
            )
            credit_segments = await self._lifecycle_service.credit_segments(subscription.id)
        return SubscriptionDetailsDto(
            subscription=subscription,
            activity=await self._usage.activity(subscription),
            credit_segments=credit_segments,
        )
