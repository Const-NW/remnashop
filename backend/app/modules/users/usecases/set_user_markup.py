from loguru import logger

from app.core.actor import Actor
from app.core.constants import MAX_MARKUP_PERCENT
from app.core.db.uow import UnitOfWork
from app.core.error_codes import ErrorCode
from app.core.exceptions import NotFoundError, ValidationError
from app.core.ports import EventPublisher
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.modules.users.dto import SetUserMarkupDto, UpdateUserDto, UserListItemDto
from app.modules.users.events import UserMarkupChangedEvent
from app.modules.users.listing import detail_roles
from app.modules.users.repository import UserRepository
from app.modules.users.visibility import assert_own_or_manageable, get_visible_target


class SetUserMarkupUseCase(UseCase[SetUserMarkupDto, UserListItemDto]):
    required_permission = Permission.USERS_MARKUP_MANAGE

    def __init__(
        self, user_repository: UserRepository, uow: UnitOfWork, event_publisher: EventPublisher
    ) -> None:
        self._user_repository = user_repository
        self._uow = uow
        self._event_publisher = event_publisher

    async def _execute(self, actor: Actor, data: SetUserMarkupDto) -> UserListItemDto:
        if not 0 <= data.markup_percent <= MAX_MARKUP_PERCENT:
            raise ValidationError(
                f"markup must be between 0 and {MAX_MARKUP_PERCENT}",
                code=ErrorCode.MARKUP_PERCENT_INVALID,
            )

        async with self._uow:
            target = await get_visible_target(
                self._user_repository, actor, data.user_id, for_update=True
            )
            # dropping one's own markup is money handed to oneself, as with a discount
            assert_own_or_manageable(actor, target)
            if data.markup_percent != target.markup_percent:
                updated = await self._user_repository.update_fields(
                    target.id, UpdateUserDto(markup_percent=data.markup_percent)
                )
                if updated is None:
                    raise NotFoundError(
                        f"User '{data.user_id}' not found", code=ErrorCode.USER_NOT_FOUND
                    )
                await self._event_publisher.publish(
                    UserMarkupChangedEvent(
                        actor=actor,
                        target=target,
                        old_percent=target.markup_percent,
                        new_percent=updated.markup_percent,
                        ip_address=data.client_ip,
                    )
                )
                logger.info(
                    f"{actor.log} Set markup of user '{target.id}' to {data.markup_percent}%"
                )
            return await self._user_repository.get_list_item(
                target.id, detail_roles(actor, target.id)
            )
