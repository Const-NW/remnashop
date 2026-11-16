from dataclasses import replace

from app.modules.plans import PlanRepository
from app.modules.subscriptions.dto import PlanSnapshot, SubscriptionDto


async def current_plan_snapshots(
    snapshots: list[PlanSnapshot], plan_repository: PlanRepository
) -> list[PlanSnapshot]:
    """The customer sees a purchase under its plan's current name, translations and
    icon, so a renamed plan reads the same in the menu and the history; the
    snapshot's own only once the plan is deleted. Display copies: nothing here is
    written back."""
    plans = {
        plan.id: plan
        for plan in await plan_repository.list_by_ids(list({s.plan_id for s in snapshots}))
    }
    result = []
    for snapshot in snapshots:
        plan = plans.get(snapshot.plan_id)
        if plan is not None:
            snapshot = replace(
                snapshot,
                name=plan.name,
                names=plan.localized_names(),
                icon_emoji_id=plan.icon_emoji_id,
                icon_emoji_fallback=plan.icon_emoji_fallback,
            )
        result.append(snapshot)
    return result


async def with_current_plan_labels(
    subscriptions: list[SubscriptionDto], plan_repository: PlanRepository
) -> list[SubscriptionDto]:
    snapshots = await current_plan_snapshots(
        [s.plan_snapshot for s in subscriptions], plan_repository
    )
    return [
        s.model_copy(update={"plan_snapshot": snapshot})
        for s, snapshot in zip(subscriptions, snapshots, strict=True)
    ]
