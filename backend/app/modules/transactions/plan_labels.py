from app.modules.plans import PlanRepository
from app.modules.subscriptions import current_plan_snapshots
from app.modules.transactions.dto import TransactionDto


async def with_current_plan_labels(
    orders: list[TransactionDto], plan_repository: PlanRepository
) -> list[TransactionDto]:
    snapshots = await current_plan_snapshots([o.plan_snapshot for o in orders], plan_repository)
    return [
        o.model_copy(update={"plan_snapshot": snapshot})
        for o, snapshot in zip(orders, snapshots, strict=True)
    ]
