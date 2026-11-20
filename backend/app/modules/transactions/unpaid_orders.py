from typing import Final

from loguru import logger

from app.core.enums import DiscountSource, FulfillmentHold, TransactionStatus
from app.core.ports import EventPublisher
from app.modules.transactions.dto import TransactionDto
from app.modules.transactions.events import PaidOrderNotFulfilledEvent
from app.modules.transactions.repository import TransactionRepository
from app.modules.users import UserRepository

# closed by the gateway (or the expiry sweep) without a payment
UNPAID_FINAL: Final[frozenset[TransactionStatus]] = frozenset(
    {TransactionStatus.CANCELED, TransactionStatus.FAILED}
)


async def give_back_purchase_discount(
    user_repository: UserRepository,
    transaction_repository: TransactionRepository,
    order: TransactionDto,
) -> None:
    # the one-shot discount was spent when the order was created; an order
    # nobody paid returns it, and remembers it did for a late payment
    if order.discount_source is not DiscountSource.PURCHASE:
        return
    if await user_repository.restore_purchase_discount(
        order.user_id,
        order.discount_percent,
        order.discount_promocode_id,
        from_promocode=order.discount_from_promocode,
    ):
        await transaction_repository.mark_discount_returned(order.idempotency_key)


async def hold_paid_order(
    order: TransactionDto,
    hold: FulfillmentHold,
    *,
    transaction_repository: TransactionRepository,
    event_publisher: EventPublisher,
) -> bool:
    """Leaves a paid order to an admin, who hears of it, as the customer does; False
    when it was issued or approved meanwhile."""
    if not await transaction_repository.set_fulfillment_hold(order.id, hold):
        return False
    await event_publisher.publish(
        PaidOrderNotFulfilledEvent(
            order_id=order.id,
            user_id=order.user_id,
            payment_key=order.idempotency_key,
            gateway_type=order.gateway_type,
            amount=str(order.final_price),
            currency=order.currency,
        )
    )
    logger.warning(f"Paid order '{order.id}' held for an admin: {hold.value}")
    return True


async def clear_late_payment(
    order: TransactionDto,
    *,
    transaction_repository: TransactionRepository,
    user_repository: UserRepository,
    event_publisher: EventPublisher,
) -> bool:
    """Whether an order just moved to PAID may be issued; `order` is the row as it was
    before, read under the lock that moved it. One paid after its cancel had its
    one-shot discount handed back: it's spent again, and when it's gone the order is
    held for an admin. Inside the transaction that marked it paid."""
    # not handed back (its promocode deleted, the slot taken): nothing to spend twice
    if order.status not in UNPAID_FINAL or not order.purchase_discount_returned:
        return True
    if await user_repository.spend_purchase_discount(order.user_id, order.discount_percent):
        return True
    await hold_paid_order(
        order,
        FulfillmentHold.PURCHASE_DISCOUNT_SPENT,
        transaction_repository=transaction_repository,
        event_publisher=event_publisher,
    )
    return False


class PaidOrderHolder:
    """Holds an order the subscriptions module can't issue as paid for."""

    def __init__(
        self, transaction_repository: TransactionRepository, event_publisher: EventPublisher
    ) -> None:
        self._transaction_repository = transaction_repository
        self._event_publisher = event_publisher

    async def hold_paid_order(self, idempotency_key: str, hold: FulfillmentHold) -> bool:
        order = await self._transaction_repository.find_by_idempotency_key(idempotency_key)
        # a retry landing after the order was issued, or after an admin chose to
        # issue it, has nothing left to hold
        if (
            order is None
            or order.status is not TransactionStatus.PAID
            or order.fulfilled_at is not None
            or order.fulfillment_approved
        ):
            return False
        return await hold_paid_order(
            order,
            hold,
            transaction_repository=self._transaction_repository,
            event_publisher=self._event_publisher,
        )
