from app.modules.subscriptions import PurchaseIdempotencyRepository, PurchaseSubscriptionDispatch
from app.modules.transactions.dto import TransactionDto
from app.modules.transactions.repository import TransactionRepository


async def dispatch_fulfillment(
    dispatch: PurchaseSubscriptionDispatch, order: TransactionDto, *, by_admin: bool = False
) -> None:
    # Keyed by the order's idempotency_key, so a redispatch of the same order
    # provisions it once.
    await dispatch.kiq(
        user_id=order.user_id,
        plan_snapshot=order.plan_snapshot.to_json(),
        duration_days=order.duration_days,
        price_paid=str(order.final_price),
        base_price=str(order.base_price),
        change_credit=str(order.change_credit),
        currency=order.currency.value,
        purchase_type=order.purchase_type.value,
        target_subscription_id=order.target_subscription_id,
        idempotency_key=order.idempotency_key,
        # an admin issuing it has decided a renewal of a since-changed plan goes on
        renew_other_plan=by_admin,
    )


async def link_fulfilled_order(
    transaction_repository: TransactionRepository,
    idempotency_repository: PurchaseIdempotencyRepository,
    order: TransactionDto,
    subscription_id: int,
) -> None:
    """Records a delivery the fulfilled task didn't, with the days a renewal pinned:
    a refund takes those back, not the nominal duration."""
    await transaction_repository.mark_fulfilled(order.id, subscription_id)
    added_days = await idempotency_repository.find_renew_added_days(order.idempotency_key)
    if added_days is not None:
        await transaction_repository.set_added_days(order.idempotency_key, added_days)
