from loguru import logger

from app.core.actor import Actor
from app.core.db.uow import UnitOfWork
from app.core.events import changed_fields
from app.core.ports import EventPublisher
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.modules.gateways.dto import PaymentGatewayDto, UpdateGatewayChannelsDto
from app.modules.gateways.events import GatewayChangedEvent
from app.modules.gateways.repository import GatewayRepository


class UpdateGatewayChannelsUseCase(UseCase[UpdateGatewayChannelsDto, PaymentGatewayDto]):
    required_permission = Permission.GATEWAYS_CHANNELS_UPDATE

    def __init__(
        self,
        gateway_repository: GatewayRepository,
        event_publisher: EventPublisher,
        uow: UnitOfWork,
    ) -> None:
        self._gateway_repository = gateway_repository
        self._event_publisher = event_publisher
        self._uow = uow

    async def _execute(self, actor: Actor, data: UpdateGatewayChannelsDto) -> PaymentGatewayDto:
        async with self._uow:
            before = await self._gateway_repository.get_by_type(data.type)
            gateway = await self._gateway_repository.set_channels(
                data.type, data.telegram_enabled, data.cabinet_enabled
            )
            await self._event_publisher.publish(
                GatewayChangedEvent(
                    actor=actor,
                    action="channels",
                    gateway_type=data.type,
                    changed_fields=changed_fields(
                        before, gateway, ("telegram_enabled", "cabinet_enabled")
                    ),
                )
            )
        logger.info(f"{actor.log} Updated channels of gateway '{data.type.value}'")
        return gateway
