from decimal import Decimal
from typing import Final, Optional
from uuid import UUID

from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from redis.asyncio import Redis

from app.core.broker import broker
from app.core.enums import Currency, PanelSyncDirection, PurchaseType
from app.core.utils.lock import cron_lock
from app.modules.subscriptions.dto import (
    GrantAddOnDto,
    PlanSnapshot,
    ProcessRemnawaveWebhookDto,
    PurchaseSubscriptionDto,
    SyncFromPanelOptionsDto,
    SyncToPanelOptionsDto,
)
from app.modules.subscriptions.panel_sync import read_panel_sync_status
from app.modules.subscriptions.usecases import (
    FulfillPurchaseUseCase,
    GrantAddOnUseCase,
    ProcessRemnawaveWebhookUseCase,
    PruneIdempotencyKeysUseCase,
    RestoreDuePanelConfigsUseCase,
    SweepGraceUseCase,
    SyncAllFromPanelUseCase,
    SyncAllToPanelUseCase,
)

# a minute cron's lock, short of its next run
_MINUTE_CRON_LOCK_TTL_S: Final[int] = 55


@broker.task(retry_on_error=True, max_retries=3)
@inject(patch_module=True)
async def purchase_subscription_task(
    user_id: int,
    plan_snapshot: dict,
    duration_days: int,
    price_paid: str,
    currency: str,
    purchase_type: str,
    use_case: FromDishka[FulfillPurchaseUseCase],
    target_subscription_id: Optional[int] = None,
    base_price: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    change_credit: Optional[str] = None,
    renew_other_plan: bool = False,
) -> None:
    await use_case.system(
        PurchaseSubscriptionDto(
            user_id=user_id,
            plan_snapshot=PlanSnapshot.from_json(plan_snapshot),
            duration_days=duration_days,
            price_paid=Decimal(price_paid),
            base_price=Decimal(base_price) if base_price is not None else None,
            currency=Currency(currency),
            purchase_type=PurchaseType(purchase_type),
            target_subscription_id=target_subscription_id,
            idempotency_key=idempotency_key,
            # every queued purchase is a customer's order
            reset_traffic=True,
            apply_current_plan=True,
            change_credit=Decimal(change_credit) if change_credit is not None else None,
            renew_other_plan=renew_other_plan,
        )
    )


@broker.task(retry_on_error=True, max_retries=3)
@inject(patch_module=True)
async def grant_addon_task(
    subscription_id: int,
    addon_id: int,
    use_case: FromDishka[GrantAddOnUseCase],
    idempotency_key: Optional[str] = None,
) -> None:
    await use_case.system(
        GrantAddOnDto(
            subscription_id=subscription_id, addon_id=addon_id, idempotency_key=idempotency_key
        )
    )


@broker.task(retry_on_error=True, max_retries=3)
@inject(patch_module=True)
async def process_remnawave_webhook_task(
    body: str, use_case: FromDishka[ProcessRemnawaveWebhookUseCase]
) -> None:
    await use_case.system(ProcessRemnawaveWebhookDto(body=body))


@broker.task
@inject(patch_module=True)
async def run_panel_sync_task(
    direction: str,
    from_panel: FromDishka[SyncAllFromPanelUseCase],
    to_panel: FromDishka[SyncAllToPanelUseCase],
    redis: FromDishka[Redis],
    # defaults for a message queued before the options existed
    adopt_unnamed: bool = True,
    respect_subscription_limit: bool = False,
    create_users: bool = False,
    recreate_missing: bool = True,
    delete_orphans: bool = True,
    squad_replacements: Optional[dict[str, Optional[str]]] = None,
) -> None:
    # the start set the running mark and the finish clears it: a message redelivered
    # after the finish must not run a second, unclaimed sync
    if not (await read_panel_sync_status(redis, PanelSyncDirection(direction))).running:
        logger.info(f"Panel sync '{direction}' already finished, skipping a redelivered run")
        return
    if PanelSyncDirection(direction) is PanelSyncDirection.FROM_PANEL:
        await from_panel.system(
            SyncFromPanelOptionsDto(
                adopt_unnamed=adopt_unnamed,
                respect_subscription_limit=respect_subscription_limit,
                create_users=create_users,
            )
        )
    else:
        await to_panel.system(
            SyncToPanelOptionsDto(
                recreate_missing=recreate_missing,
                delete_orphans=delete_orphans,
                squad_replacements={
                    UUID(squad): UUID(target) if target is not None else None
                    for squad, target in (squad_replacements or {}).items()
                },
            )
        )


@broker.task(schedule=[{"cron": "15 3 * * *"}])
@inject(patch_module=True)
async def prune_subscription_idempotency_task(
    use_case: FromDishka[PruneIdempotencyKeysUseCase],
) -> None:
    await use_case.system()


@broker.task(schedule=[{"cron": "*/1 * * * *"}])
@inject(patch_module=True)
async def sweep_grace_task(
    use_case: FromDishka[SweepGraceUseCase], redis: FromDishka[Redis]
) -> None:
    async with cron_lock(redis, "grace_sweep", ttl_seconds=_MINUTE_CRON_LOCK_TTL_S) as acquired:
        if acquired:
            await use_case.system()


@broker.task(schedule=[{"cron": "*/1 * * * *"}])
@inject(patch_module=True)
async def restore_due_panel_configs_task(
    use_case: FromDishka[RestoreDuePanelConfigsUseCase], redis: FromDishka[Redis]
) -> None:
    async with cron_lock(redis, "panel_restore", ttl_seconds=_MINUTE_CRON_LOCK_TTL_S) as acquired:
        if acquired:
            await use_case.system()
