from datetime import datetime, timedelta
from typing import Final

from loguru import logger

from app.core.actor import Actor
from app.core.db.uow import UnitOfWork
from app.core.exceptions import ConflictError, PaymentGatewayError
from app.core.ports import EventPublisher
from app.core.usecase import UseCase
from app.core.utils.time import datetime_now
from app.modules.transactions.dto import CheckTransactionPaymentDto, TransactionDto
from app.modules.transactions.events import TransactionExpiredEvent
from app.modules.transactions.repository import TransactionRepository
from app.modules.transactions.unpaid_orders import give_back_purchase_discount
from app.modules.transactions.usecases.check_transaction_payment import (
    CheckTransactionPaymentUseCase,
)
from app.modules.users import UserRepository

# a gateway still unanswering this long after expiry no longer holds the order
# open; a late webhook still settles it
_STATUS_CHECK_GIVE_UP: Final[timedelta] = timedelta(hours=1)


class PruneExpiredTransactionsUseCase(UseCase[None, None]):
    required_permission = None

    def __init__(
        self,
        transaction_repository: TransactionRepository,
        user_repository: UserRepository,
        uow: UnitOfWork,
        event_publisher: EventPublisher,
        check_payment_use_case: CheckTransactionPaymentUseCase,
    ) -> None:
        self._transaction_repository = transaction_repository
        self._user_repository = user_repository
        self._uow = uow
        self._event_publisher = event_publisher
        self._check_payment_use_case = check_payment_use_case

    async def _execute(self, actor: Actor, data: None) -> None:
        now = datetime_now()
        async with self._uow:
            expired = await self._transaction_repository.list_expired_pending(now)
        # a lost webhook must not cancel a paid order: the gateway is asked first
        to_cancel = [order.id for order in expired if not await self._keep_pending(order, now)]
        async with self._uow:
            canceled = await self._transaction_repository.cancel_expired_pending(now, to_cancel)
            for order in canceled:
                await self._event_publisher.publish(
                    TransactionExpiredEvent(order_id=order.id, user_id=order.user_id)
                )
                await give_back_purchase_discount(
                    self._user_repository, self._transaction_repository, order
                )
        logger.info(
            f"Canceled {len(canceled)} of {len(expired)} unpaid transaction(s) past their expiry"
        )

    async def _keep_pending(self, order: TransactionDto, now: datetime) -> bool:
        """True when the gateway couldn't say whether the order was paid, so it
        waits for the next sweep. A paid order leaves PENDING here."""
        if order.gateway_type is None or order.expires_at <= now - _STATUS_CHECK_GIVE_UP:
            return False
        try:
            await self._check_payment_use_case.system(
                CheckTransactionPaymentDto(transaction_id=order.id, client_ip=None)
            )
        except ConflictError:
            return False  # no status API for this gateway, or the customer is gone
        except PaymentGatewayError as error:
            logger.warning(f"Kept expired transaction '{order.id}' pending: {error}")
            return True
        except Exception:  # one order's failure must not hold back the rest of the sweep
            logger.exception(f"Kept expired transaction '{order.id}' pending")
            return True
        return False
