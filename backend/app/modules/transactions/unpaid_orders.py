from typing import Final

from loguru import logger

from app.core.enums import DiscountSource, TransactionStatus
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
    user_repository: UserRepository, order: TransactionDto
) -> None:
    # the one-shot discount was spent when the order was created; an order
    # nobody paid returns it
    if order.discount_source is not DiscountSource.PURCHASE:
        return
    await user_repository.restore_purchase_discount(
        order.user_id,
        order.discount_percent,
        order.discount_promocode_id,
        from_promocode=order.discount_from_promocode,
    )


async def clear_late_payment(
    order: TransactionDto,
    *,
    transaction_repository: TransactionRepository,
    user_repository: UserRepository,
    event_publisher: EventPublisher,
) -> bool:
    """Whether an order just moved to PAID may be issued. One paid after its cancel
    had its one-shot discount handed back: it's spent again, and when it's gone the
    order is held for an admin. Inside the transaction that marked it paid."""
    if order.status not in UNPAID_FINAL or order.discount_source is not DiscountSource.PURCHASE:
        return True
    if await user_repository.spend_purchase_discount(order.user_id, order.discount_percent):
        return True
    await transaction_repository.set_fulfillment_held(order.id, True)
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
    logger.warning(
        f"Late payment for canceled order '{order.id}': its purchase discount was spent "
        "again meanwhile, held for an admin to issue or refund"
    )
    return False
