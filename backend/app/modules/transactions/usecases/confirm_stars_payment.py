from functools import partial

from loguru import logger

from app.core.actor import Actor
from app.core.db.uow import UnitOfWork
from app.core.enums import TransactionStatus
from app.core.ports import EventPublisher
from app.core.usecase import UseCase
from app.modules.subscriptions import PurchaseSubscriptionDispatch
from app.modules.transactions.dto import StarsPaymentDto
from app.modules.transactions.fulfillment import dispatch_fulfillment
from app.modules.transactions.repository import TransactionRepository
from app.modules.transactions.unpaid_orders import clear_late_payment
from app.modules.transactions.usecases._stars import find_matching_stars_order
from app.modules.users import UserRepository


class ConfirmStarsPaymentUseCase(UseCase[StarsPaymentDto, None]):
    required_permission = None

    def __init__(
        self,
        transaction_repository: TransactionRepository,
        user_repository: UserRepository,
        uow: UnitOfWork,
        purchase_dispatch: PurchaseSubscriptionDispatch,
        event_publisher: EventPublisher,
    ) -> None:
        self._transaction_repository = transaction_repository
        self._user_repository = user_repository
        self._uow = uow
        self._purchase_dispatch = purchase_dispatch
        self._event_publisher = event_publisher

    async def _execute(self, actor: Actor, data: StarsPaymentDto) -> None:
        # Telegram has charged already: a charge left unfulfilled here is refunded by hand
        async with self._uow:
            order = await find_matching_stars_order(
                self._transaction_repository,
                self._user_repository,
                telegram_id=data.telegram_id,
                payload=data.payload,
                currency=data.currency,
                total_amount=data.total_amount,
            )
            if order is None:
                logger.warning(
                    f"Telegram Stars charge '{data.charge_id}' from telegram_id "
                    f"'{data.telegram_id}' (payload '{data.payload}') matches no order "
                    "-- refund it manually"
                )
                return
            # locked: the expiry sweep may cancel it and hand its discount back
            # meanwhile, which the late-payment check below has to see
            order = await self._transaction_repository.get_by_id_for_update(order.id)
            # a canceled order still takes a late payment
            moved = await self._transaction_repository.mark_status_if(
                order.id,
                TransactionStatus.PAID,
                current=frozenset({TransactionStatus.PENDING, TransactionStatus.CANCELED}),
                external_payment_id=data.charge_id,
            )
            if not moved:
                current = await self._transaction_repository.get_by_id(order.id)
                if current.external_payment_id != data.charge_id:
                    logger.warning(
                        f"Telegram Stars charge '{data.charge_id}' from telegram_id "
                        f"'{data.telegram_id}' (payload '{data.payload}') for order "
                        f"'{order.id}' ({current.status.value}) was not fulfilled "
                        "-- refund it manually"
                    )
                return
            if order.is_test:
                logger.info(f"Test transaction '{order.id}' paid with Telegram Stars")
                return
            if not await clear_late_payment(
                order,
                transaction_repository=self._transaction_repository,
                user_repository=self._user_repository,
                event_publisher=self._event_publisher,
            ):
                return
            await self._uow.after_commit(
                partial(dispatch_fulfillment, self._purchase_dispatch, order)
            )
        logger.info(f"Order '{order.id}' paid with Telegram Stars")
