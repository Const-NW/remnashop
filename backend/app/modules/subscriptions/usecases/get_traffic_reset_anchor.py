from datetime import datetime
from typing import Optional

from app.core.db.uow import UnitOfWork
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.modules.subscriptions.eligibility import get_owned_target
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.subscriptions.usage import PanelTrafficUsage
from app.modules.users import UserDto


class GetTrafficResetAnchorUseCase(UseCase[int, Optional[datetime], UserDto]):
    """The panel user's createdAt, which MONTH_ROLLING resets count from; None when
    neither the row nor the panel has it."""

    required_permission = Permission.PUBLIC

    def __init__(
        self,
        subscription_repository: SubscriptionRepository,
        usage: PanelTrafficUsage,
        uow: UnitOfWork,
    ) -> None:
        self._subscription_repository = subscription_repository
        self._usage = usage
        self._uow = uow

    async def _execute(self, actor: UserDto, data: int) -> Optional[datetime]:
        async with self._uow:
            subscription = await get_owned_target(self._subscription_repository, data, actor.id)
        if subscription.panel_created_at is not None:
            return subscription.panel_created_at
        # rows from before the column wait for a webhook; the panel is asked meanwhile
        created_at = await self._usage.created_at(subscription)
        if created_at is not None:
            async with self._uow:
                await self._subscription_repository.set_panel_created_at(
                    subscription.remnawave_user_id, created_at
                )
        return created_at
