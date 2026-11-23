from typing import cast

from dishka import Provider, Scope, alias, provide, provide_all

from app.modules.subscriptions.change_credit import ChangeCreditCalculator
from app.modules.subscriptions.credit_segment_repository import CreditSegmentRepository
from app.modules.subscriptions.device_name_repository import DeviceNameRepository
from app.modules.subscriptions.dispatch import (
    GrantAddOnDispatch,
    PanelSyncDispatch,
    ProcessRemnawaveWebhookDispatch,
    PurchaseSubscriptionDispatch,
)
from app.modules.subscriptions.eligibility import PurchaseEligibility
from app.modules.subscriptions.idempotency_repository import PurchaseIdempotencyRepository
from app.modules.subscriptions.lifecycle import SubscriptionLifecycleService
from app.modules.subscriptions.panel_squads import PanelSquadCatalog
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.subscriptions.revoker import PanelSubscriptionRevoker
from app.modules.subscriptions.rollback import PurchaseRollbackService
from app.modules.subscriptions.stats_repository import SubscriptionStatsRepository
from app.modules.subscriptions.tasks import (
    grant_addon_task,
    process_remnawave_webhook_task,
    purchase_subscription_task,
    run_panel_sync_task,
)
from app.modules.subscriptions.usage import PanelTrafficUsage
from app.modules.subscriptions.usecases import (
    ActivateTrialSubscriptionUseCase,
    AdminDeleteAllDevicesUseCase,
    AdminDeleteDeviceUseCase,
    AdminListDevicesUseCase,
    AdminReissueSubscriptionUseCase,
    ApplyChannelMembershipUseCase,
    ChangeSubscriptionPlanUseCase,
    DeleteAllDevicesUseCase,
    DeleteDeviceUseCase,
    DeleteSubscriptionUseCase,
    DeleteUserSubscriptionsUseCase,
    EndGraceUseCase,
    EnterGraceUseCase,
    FulfillPurchaseUseCase,
    GetAvailableTrialUseCase,
    GetPanelSyncStatusUseCase,
    GetSubscriptionListStatsUseCase,
    GetSubscriptionPanelDiffUseCase,
    GetSubscriptionUseCase,
    GetTrafficResetAnchorUseCase,
    GrantAddOnUseCase,
    GrantSubscriptionUseCase,
    HandleRemnawaveWebhookUseCase,
    ListActivePlansUseCase,
    ListDevicesUseCase,
    ListMissingPanelSquadsUseCase,
    ListOwnActiveSubscriptionsUseCase,
    ListOwnSubscriptionsUsageUseCase,
    ListOwnSubscriptionsUseCase,
    ListPanelSquadsUseCase,
    ListSubscriptionsUseCase,
    ProcessRemnawaveWebhookUseCase,
    PruneIdempotencyKeysUseCase,
    PublishPanelEventUseCase,
    PullSubscriptionFromPanelUseCase,
    PurchaseSubscriptionUseCase,
    PushSubscriptionToPanelUseCase,
    ReissueSubscriptionUseCase,
    RenameDeviceUseCase,
    ResetSubscriptionTrafficUseCase,
    RestoreDuePanelConfigsUseCase,
    SetSubscriptionEnabledUseCase,
    StartPanelSyncFromUseCase,
    StartPanelSyncToUseCase,
    SweepGraceUseCase,
    SyncAllFromPanelUseCase,
    SyncAllToPanelUseCase,
    SyncSubscriptionStatusUseCase,
    UpdateSubscriptionSquadsUseCase,
    UpdateSubscriptionUseCase,
)
from app.modules.users import SubscriptionRevoker


class SubscriptionsProvider(Provider):
    scope = Scope.REQUEST

    services = provide_all(
        SubscriptionRepository,
        CreditSegmentRepository,
        DeviceNameRepository,
        SubscriptionStatsRepository,
        PurchaseIdempotencyRepository,
        SubscriptionLifecycleService,
        PurchaseEligibility,
        ChangeCreditCalculator,
        PanelSquadCatalog,
        PanelSubscriptionRevoker,
        PurchaseRollbackService,
        PanelTrafficUsage,
    )
    use_cases = provide_all(
        ListSubscriptionsUseCase,
        GetSubscriptionUseCase,
        GetSubscriptionListStatsUseCase,
        UpdateSubscriptionUseCase,
        AdminDeleteAllDevicesUseCase,
        AdminDeleteDeviceUseCase,
        AdminListDevicesUseCase,
        AdminReissueSubscriptionUseCase,
        ResetSubscriptionTrafficUseCase,
        SetSubscriptionEnabledUseCase,
        ChangeSubscriptionPlanUseCase,
        GrantSubscriptionUseCase,
        UpdateSubscriptionSquadsUseCase,
        DeleteSubscriptionUseCase,
        DeleteUserSubscriptionsUseCase,
        GetAvailableTrialUseCase,
        ActivateTrialSubscriptionUseCase,
        ApplyChannelMembershipUseCase,
        ListDevicesUseCase,
        RenameDeviceUseCase,
        DeleteDeviceUseCase,
        DeleteAllDevicesUseCase,
        GrantAddOnUseCase,
        PurchaseSubscriptionUseCase,
        ReissueSubscriptionUseCase,
        SyncSubscriptionStatusUseCase,
        ListActivePlansUseCase,
        HandleRemnawaveWebhookUseCase,
        ProcessRemnawaveWebhookUseCase,
        PublishPanelEventUseCase,
        ListOwnActiveSubscriptionsUseCase,
        ListOwnSubscriptionsUseCase,
        ListOwnSubscriptionsUsageUseCase,
        GetTrafficResetAnchorUseCase,
        FulfillPurchaseUseCase,
        GetPanelSyncStatusUseCase,
        GetSubscriptionPanelDiffUseCase,
        ListMissingPanelSquadsUseCase,
        ListPanelSquadsUseCase,
        PullSubscriptionFromPanelUseCase,
        PushSubscriptionToPanelUseCase,
        StartPanelSyncFromUseCase,
        StartPanelSyncToUseCase,
        SyncAllFromPanelUseCase,
        SyncAllToPanelUseCase,
        PruneIdempotencyKeysUseCase,
        EnterGraceUseCase,
        EndGraceUseCase,
        RestoreDuePanelConfigsUseCase,
        SweepGraceUseCase,
    )
    subscription_revoker = alias(source=PanelSubscriptionRevoker, provides=SubscriptionRevoker)

    @provide(scope=Scope.APP)
    def purchase_subscription_dispatch(self) -> PurchaseSubscriptionDispatch:
        return cast(PurchaseSubscriptionDispatch, purchase_subscription_task)

    @provide(scope=Scope.APP)
    def grant_addon_dispatch(self) -> GrantAddOnDispatch:
        return cast(GrantAddOnDispatch, grant_addon_task)

    @provide(scope=Scope.APP)
    def panel_sync_dispatch(self) -> PanelSyncDispatch:
        return cast(PanelSyncDispatch, run_panel_sync_task)

    @provide(scope=Scope.APP)
    def process_remnawave_webhook_dispatch(self) -> ProcessRemnawaveWebhookDispatch:
        return cast(ProcessRemnawaveWebhookDispatch, process_remnawave_webhook_task)


def get_subscriptions_providers() -> list[Provider]:
    return [SubscriptionsProvider()]
